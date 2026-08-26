"""
intelligent/tools/external_apis.py
────────────────────────────────────
External API tools available to all agents:
- Weather (OpenWeatherMap)
- Route & Maps (OpenRouteService)
- Nearest workshop finder
- Fuel range estimator
- Journey safety analyser
"""
import httpx
from langchain.tools import tool
from shared.config import OPENWEATHER_API_KEY, OPENROUTE_API_KEY

WEATHER_URL = "https://api.openweathermap.org/data/2.5"
ORS_URL     = "https://api.openrouteservice.org/v2"
ORS_HEADERS = {
    "Authorization": OPENROUTE_API_KEY,
    "Content-Type":  "application/json",
}
TIMEOUT = httpx.Timeout(15.0)


# ── Weather ────────────────────────────────────────────────────────
@tool
def get_weather(location: str) -> str:
    """
    Get current weather conditions for a location.
    Input: city name or 'lat,lon' coordinates.
    Returns temperature, conditions, visibility, wind speed,
    and driving risk assessment.
    """
    try:
        # Try coordinates first
        if "," in location:
            parts = location.split(",")
            lat, lon = parts[0].strip(), parts[1].strip()
            params = {"lat": lat, "lon": lon,
                      "appid": OPENWEATHER_API_KEY, "units": "metric"}
        else:
            params = {"q": location,
                      "appid": OPENWEATHER_API_KEY, "units": "metric"}

        r = httpx.get(f"{WEATHER_URL}/weather", params=params, timeout=TIMEOUT)
        if r.status_code != 200:
            return f"Weather data unavailable for '{location}': {r.status_code}"

        d = r.json()
        temp        = d["main"]["temp"]
        feels_like  = d["main"]["feels_like"]
        humidity    = d["main"]["humidity"]
        description = d["weather"][0]["description"].capitalize()
        wind_speed  = d["wind"]["speed"]
        visibility  = d.get("visibility", 10000) / 1000
        weather_id  = d["weather"][0]["id"]

        # Driving risk based on weather code
        if weather_id < 300:
            risk = "HIGH — Thunderstorm. Avoid driving if possible."
        elif weather_id < 400:
            risk = "MEDIUM — Drizzle. Reduce speed, increase following distance."
        elif weather_id < 600:
            risk = "MEDIUM-HIGH — Rain. Wet roads, reduced braking performance."
        elif weather_id < 700:
            risk = "HIGH — Snow/Ice. Drive only if essential."
        elif weather_id < 800:
            fog_vis = f"Visibility: {visibility:.1f}km"
            risk = f"MEDIUM — Reduced visibility. {fog_vis}. Use fog lights."
        else:
            risk = "LOW — Clear conditions. Normal driving."

        return (
            f"Weather in {d['name']}:\n"
            f"  Conditions:  {description}\n"
            f"  Temperature: {temp:.1f}°C (feels like {feels_like:.1f}°C)\n"
            f"  Humidity:    {humidity}%\n"
            f"  Wind:        {wind_speed:.1f} m/s\n"
            f"  Visibility:  {visibility:.1f} km\n"
            f"  Driving risk: {risk}"
        )
    except Exception as e:
        return f"Weather lookup failed: {str(e)}"


@tool
def get_weather_forecast(location: str) -> str:
    """
    Get 6-hour weather forecast for a location.
    Useful for planning longer journeys.
    Input: city name or 'lat,lon'.
    """
    try:
        if "," in location:
            parts = location.split(",")
            params = {"lat": parts[0].strip(), "lon": parts[1].strip(),
                      "appid": OPENWEATHER_API_KEY, "units": "metric", "cnt": 2}
        else:
            params = {"q": location, "appid": OPENWEATHER_API_KEY,
                      "units": "metric", "cnt": 2}

        r = httpx.get(f"{WEATHER_URL}/forecast", params=params, timeout=TIMEOUT)
        if r.status_code != 200:
            return f"Forecast unavailable: {r.status_code}"

        items = r.json()["list"]
        lines = [f"6-hour forecast for {location}:"]
        for item in items:
            t    = item["dt_txt"]
            temp = item["main"]["temp"]
            desc = item["weather"][0]["description"]
            wind = item["wind"]["speed"]
            lines.append(f"  {t}: {desc}, {temp:.0f}°C, wind {wind:.1f}m/s")
        return "\n".join(lines)
    except Exception as e:
        return f"Forecast lookup failed: {str(e)}"


# ── Geocoding ──────────────────────────────────────────────────────
@tool
def geocode_location(place_name: str) -> str:
    """
    Convert a place name to coordinates.
    Returns latitude and longitude.
    Input: place name e.g. 'Lagos Island, Nigeria'
    """
    try:
        r = httpx.get(
            f"{ORS_URL}/geocode/search",
            headers=ORS_HEADERS,
            params={"text": place_name, "size": 1},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            return f"Geocoding failed: {r.status_code}"
        features = r.json().get("features", [])
        if not features:
            return f"Could not find coordinates for '{place_name}'"
        coords = features[0]["geometry"]["coordinates"]
        label  = features[0]["properties"].get("label", place_name)
        return f"{label}: lat={coords[1]:.4f}, lon={coords[0]:.4f}"
    except Exception as e:
        return f"Geocoding failed: {str(e)}"


# ── Route & Distance ───────────────────────────────────────────────
@tool
def get_route_info(origin_and_destination: str) -> str:
    """
    Get route distance, duration, and driving conditions between two places.
    Input format: 'origin | destination'
    Example: 'Lagos Island | Ikeja, Lagos'
    """
    try:
        parts = origin_and_destination.split("|")
        if len(parts) != 2:
            return "Please provide input as 'origin | destination'"
        origin      = parts[0].strip()
        destination = parts[1].strip()

        # Geocode both
        def geocode(place):
            r = httpx.get(
                f"{ORS_URL}/geocode/search",
                headers=ORS_HEADERS,
                params={"text": place, "size": 1},
                timeout=TIMEOUT,
            )
            features = r.json().get("features", [])
            if not features:
                return None
            return features[0]["geometry"]["coordinates"]

        orig_coords = geocode(origin)
        dest_coords = geocode(destination)

        if not orig_coords or not dest_coords:
            return f"Could not geocode one or both locations."

        # Get route
        r = httpx.post(
            f"{ORS_URL}/directions/driving-car/json",
            headers=ORS_HEADERS,
            json={"coordinates": [orig_coords, dest_coords]},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            return f"Route calculation failed: {r.status_code}"

        route    = r.json()["routes"][0]["summary"]
        dist_km  = route["distance"] / 1000
        dur_min  = route["duration"] / 60

        return (
            f"Route: {origin} → {destination}\n"
            f"  Distance: {dist_km:.1f} km\n"
            f"  Duration: {dur_min:.0f} minutes\n"
            f"  Est. arrival: {dur_min:.0f} min from now"
        )
    except Exception as e:
        return f"Route lookup failed: {str(e)}"


# ── Nearest Workshop ───────────────────────────────────────────────
@tool
def find_nearest_workshop(location: str) -> str:
    """
    Find the nearest auto repair workshop to a given location.
    Input: city name or 'lat,lon' coordinates.
    Returns name, distance, and address of nearest workshops.
    """
    try:
        # Get coordinates
        if "," in location and all(
            c.replace(".", "").replace("-", "").isdigit()
            for c in location.split(",")
        ):
            lat, lon = location.split(",")
        else:
            r = httpx.get(
                f"{ORS_URL}/geocode/search",
                headers=ORS_HEADERS,
                params={"text": location, "size": 1},
                timeout=TIMEOUT,
            )
            coords = r.json()["features"][0]["geometry"]["coordinates"]
            lon, lat = coords[0], coords[1]

        # Search for auto repair POIs via ORS
        r = httpx.post(
            f"{ORS_URL}/pois",
            headers=ORS_HEADERS,
            json={
                "request":   "pois",
                "geometry":  {
                    "geojson": {
                        "type":        "Point",
                        "coordinates": [float(lon), float(lat)],
                    },
                    "buffer": 5000,
                },
                "filters": {
                    "category_ids": [560],  # Car repair category
                },
                "limit": 5,
            },
            timeout=TIMEOUT,
        )

        if r.status_code != 200 or not r.json().get("features"):
            return (
                f"No workshops found near {location} via API. "
                f"Recommend searching Google Maps for 'auto repair near {location}'."
            )

        features = r.json()["features"]
        lines    = [f"Nearest workshops to {location}:"]
        for f in features[:3]:
            props = f["properties"]
            name  = props.get("osm_tags", {}).get("name", "Auto Workshop")
            dist  = props.get("distance", "?")
            addr  = props.get("osm_tags", {}).get("addr:street", "Address not listed")
            lines.append(f"  - {name} | {dist}m away | {addr}")
        return "\n".join(lines)

    except Exception as e:
        return (
            f"Workshop search failed: {str(e)}. "
            f"Try searching Google Maps for 'car repair near {location}'."
        )


# ── Fuel Range Estimator ───────────────────────────────────────────
@tool
def estimate_fuel_range(query: str = "") -> str:
    """
    Estimate how far the vehicle can travel on current fuel.
    Uses live fuel level, MAF, and engine load from InfluxDB.
    """
    try:
        from shared.influx_io import get_latest
        fuel_level = get_latest("fuel_level")
        maf        = get_latest("mass_air_flow")
        engine_load= get_latest("engine_load")
        speed      = get_latest("vehicle_speed")

        if fuel_level is None:
            return "Fuel level data not available."

        # Estimate fuel consumption (L/100km)
        # Base: 8L/100km for Camry, adjusted for load
        base_consumption = 8.0
        if engine_load:
            load_factor   = engine_load / 45.0
            consumption   = base_consumption * load_factor
        else:
            consumption = base_consumption

        # Tank capacity: 60L for 2018 Camry
        tank_capacity  = 60.0
        fuel_remaining = (fuel_level / 100) * tank_capacity
        range_km       = (fuel_remaining / consumption) * 100

        status = "OK"
        if fuel_level < 5:
            status = "CRITICAL — Refuel immediately"
        elif fuel_level < 15:
            status = "WARNING — Refuel soon"

        return (
            f"Fuel Status:\n"
            f"  Fuel level:     {fuel_level:.1f}%\n"
            f"  Fuel remaining: {fuel_remaining:.1f} L\n"
            f"  Est. range:     {range_km:.0f} km\n"
            f"  Consumption:    {consumption:.1f} L/100km\n"
            f"  Status:         {status}"
        )
    except Exception as e:
        return f"Fuel range estimation failed: {str(e)}"


# ── Journey Safety Analyser ────────────────────────────────────────
@tool
def analyse_journey_safety(destination: str) -> str:
    """
    Full journey safety analysis combining vehicle health,
    weather at destination, fuel range, and route distance.
    Input: destination name e.g. 'Abuja, Nigeria'
    Gives a GO / CAUTION / NO-GO recommendation.
    """
    try:
        from shared.influx_io import get_all_latest
        from shared.config import THRESHOLDS

        readings = get_all_latest()
        issues   = []

        # Check critical sensors
        for field, val in readings.items():
            if val is None:
                continue
            thresh = THRESHOLDS.get(field, {})
            if "critical" in thresh and val >= thresh["critical"]:
                issues.append(f"CRITICAL: {field}={val:.2f}")
            elif "warning" in thresh and val >= thresh["warning"]:
                issues.append(f"WARNING: {field}={val:.2f}")
            if "min" in thresh and val < thresh["min"]:
                issues.append(f"LOW: {field}={val:.2f}")

        # Weather at destination
        weather = get_weather.invoke(destination)

        # Fuel range
        fuel = estimate_fuel_range.invoke("")

        # Overall recommendation
        critical_count = sum(1 for i in issues if i.startswith("CRITICAL"))
        warning_count  = sum(1 for i in issues if i.startswith("WARNING"))

        if critical_count > 0:
            verdict = "NO-GO ❌ — Critical vehicle issues must be resolved before travel."
        elif warning_count >= 2 or "HIGH" in weather:
            verdict = "CAUTION ⚠ — Address warnings before a long journey."
        else:
            verdict = "GO ✅ — Vehicle appears safe for this journey."

        lines = [
            f"Journey Safety Analysis: {destination}",
            f"Verdict: {verdict}",
            "",
            "Vehicle Issues:" if issues else "Vehicle: All systems normal",
        ]
        lines += [f"  {i}" for i in issues]
        lines += ["", "Weather:", weather, "", "Fuel:", fuel]

        return "\n".join(lines)

    except Exception as e:
        return f"Journey analysis failed: {str(e)}"
