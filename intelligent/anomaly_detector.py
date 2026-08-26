"""
intelligent/anomaly_detector.py
─────────────────────────────────
Autoencoder-based anomaly detection on live OBD-II sensor data.
Catches unusual sensor combinations that no single threshold
rule would flag — the "unknown unknowns."
"""
import json
import numpy as np
import torch
import torch.nn as nn
from pathlib import Path
from shared.config import SENSOR_FIELDS
from shared.influx_io import get_all_latest, get_recent

MODEL_PATH = Path(__file__).parent / "models" / "anomaly_autoencoder.pt"
STATS_PATH = Path(__file__).parent / "models" / "sensor_stats.json"


class SensorAutoencoder(nn.Module):
    def __init__(self, n_features: int):
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Linear(n_features, 16), nn.ReLU(),
            nn.Linear(16, 8),  nn.ReLU(),
            nn.Linear(8, 4),   nn.ReLU(),
        )
        self.decoder = nn.Sequential(
            nn.Linear(4, 8),   nn.ReLU(),
            nn.Linear(8, 16),  nn.ReLU(),
            nn.Linear(16, n_features),
        )

    def forward(self, x):
        return self.decoder(self.encoder(x))


_model = None
_stats = None  # mean/std for normalisation
_threshold = None


def _load():
    global _model, _stats, _threshold
    if _model is not None:
        return
    if not MODEL_PATH.exists():
        return
    _model = SensorAutoencoder(len(SENSOR_FIELDS))
    _model.load_state_dict(torch.load(MODEL_PATH))
    _model.eval()
    with open(STATS_PATH) as f:
        s = json.load(f)
        _stats = (np.array(s["mean"]), np.array(s["std"]))
        _threshold = s["threshold"]


def _vectorise(readings: dict) -> np.ndarray | None:
    vals = []
    for f in SENSOR_FIELDS:
        v = readings.get(f)
        if v is None:
            return None
        vals.append(v)
    return np.array(vals, dtype=np.float32)


def score_current_state() -> dict:
    """
    Returns anomaly score for the current sensor snapshot.
    score > 1.0 means reconstruction error exceeds the
    learned normal threshold.
    """
    _load()
    if _model is None:
        return {"available": False, "reason": "Model not trained yet"}

    readings = get_all_latest()
    vec = _vectorise(readings)
    if vec is None:
        return {"available": False, "reason": "Incomplete sensor data"}

    mean, std = _stats
    norm = (vec - mean) / (std + 1e-8)

    with torch.no_grad():
        x = torch.tensor(norm, dtype=torch.float32).unsqueeze(0)
        recon = _model(x).squeeze(0).numpy()

    error = float(np.mean((norm - recon) ** 2))
    score = error / _threshold

    # Identify which sensors contributed most to the error
    per_field_error = (norm - recon) ** 2
    top_idx = np.argsort(per_field_error)[-3:][::-1]
    top_contributors = [
        {"field": SENSOR_FIELDS[i], "value": float(vec[i]),
         "deviation": float(per_field_error[i])}
        for i in top_idx
    ]

    return {
        "available": True,
        "anomaly_score": round(score, 3),
        "is_anomalous": score > 1.0,
        "reconstruction_error": round(error, 5),
        "threshold": round(_threshold, 5),
        "top_contributors": top_contributors,
    }
