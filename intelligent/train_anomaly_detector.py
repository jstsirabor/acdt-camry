"""
intelligent/train_anomaly_detector.py
────────────────────────────────────────
Trains the sensor autoencoder on "normal" historical data.

Run this periodically (e.g. weekly via cron) as more
normal driving data accumulates in InfluxDB.

Usage:
    python -m intelligent.train_anomaly_detector
"""
import json
import numpy as np
import torch
import torch.nn as nn
from pathlib import Path
from shared.config import SENSOR_FIELDS
from shared.influx_io import get_recent
from intelligent.anomaly_detector import SensorAutoencoder, MODEL_PATH, STATS_PATH


def collect_training_data(hours: int = 24) -> np.ndarray:
    """Pull recent sensor history and build training matrix."""
    series = {}
    min_len = None
    for field in SENSOR_FIELDS:
        points = get_recent(field, minutes=hours * 60)
        values = [v for _, v in points if v is not None]
        series[field] = values
        min_len = len(values) if min_len is None else min(min_len, len(values))

    if min_len is None or min_len < 50:
        raise ValueError(f"Not enough data to train (got {min_len} points). "
                         f"Let the simulator/vehicle run longer first.")

    matrix = np.array([series[f][:min_len] for f in SENSOR_FIELDS]).T
    return matrix.astype(np.float32)


def train(hours: int = 24, epochs: int = 200, lr: float = 1e-3):
    print(f"[ANOMALY] Collecting last {hours}h of sensor data...")
    data = collect_training_data(hours)
    print(f"[ANOMALY] Training set: {data.shape[0]} samples x {data.shape[1]} features")

    # Normalise
    mean = data.mean(axis=0)
    std  = data.std(axis=0)
    norm = (data - mean) / (std + 1e-8)

    x = torch.tensor(norm, dtype=torch.float32)

    model = SensorAutoencoder(len(SENSOR_FIELDS))
    optim = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.MSELoss()

    for epoch in range(epochs):
        optim.zero_grad()
        recon = model(x)
        loss  = loss_fn(recon, x)
        loss.backward()
        optim.step()
        if epoch % 50 == 0:
            print(f"[ANOMALY] Epoch {epoch}: loss={loss.item():.6f}")

    # Compute per-sample reconstruction error to set threshold
    with torch.no_grad():
        recon = model(x)
        errors = ((x - recon) ** 2).mean(dim=1).numpy()

    # Threshold = 99th percentile of normal reconstruction error
    threshold = float(np.percentile(errors, 99))
    print(f"[ANOMALY] Anomaly threshold set to {threshold:.6f} "
          f"(99th percentile of normal data)")

    # Save model and stats
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), MODEL_PATH)
    with open(STATS_PATH, "w") as f:
        json.dump({
            "mean": mean.tolist(),
            "std": std.tolist(),
            "threshold": threshold,
            "trained_on_samples": int(data.shape[0]),
        }, f, indent=2)

    print(f"[ANOMALY] Model saved to {MODEL_PATH}")
    print(f"[ANOMALY] Stats saved to {STATS_PATH}")


if __name__ == "__main__":
    train()
