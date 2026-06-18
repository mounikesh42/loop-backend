#!/usr/bin/env python3
"""Stage 2 fetcher — Open-Meteo historical weather (SRC_API_01).

Emits L1F_API_001 mean_wind_speed_ms by querying Open-Meteo's free historical
archive for the takeoff coordinates over the flight window. Cached per
(lat_3dp, lng_3dp, date) under cache/openmeteo/.

Inputs (from BIN parse result):
  - takeoff_lat (L1F_BIN_MP_004)
  - takeoff_lng (L1F_BIN_MP_005)
  - flight_start_utc / flight_end_utc (parser_meta)

Endpoint: https://archive-api.open-meteo.com/v1/era5
  hourly: wind_speed_10m   (m/s; 10m height)
  start_date / end_date in YYYY-MM-DD UTC

Fallback when API unreachable:
  Per spec — derive a coarse band from BIN ATT roll/pitch stdev. The spec's
  band labels are "calm / moderate / unknown"; the L3I_FC_005 indicator
  scoring uses numeric m/s. We surface the proxy band in parser_meta and
  leave L1F_API_001 = None so the indicator can detect fallback via the
  WIND_API_FALLBACK flag we raise here.
"""
import hashlib
import json
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/era5"


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    s = s.rstrip("Z")
    try:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _cache_key(lat: float, lng: float, date_str: str) -> str:
    raw = f"{round(lat, 3)}_{round(lng, 3)}_{date_str}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16] + f"_{date_str}.json"


def _fetch(url: str, timeout: int) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "drone-provenance-ppk/1.1.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch(config: dict, project_root: Path, bin_result: dict) -> dict:
    bin_fields = bin_result["fields"]
    bin_meta = bin_result["parser_meta"]

    lat = bin_fields.get("L1F_BIN_MP_004")
    lng = bin_fields.get("L1F_BIN_MP_005")
    flight_start = _parse_iso(bin_meta.get("flight_start_utc"))
    flight_end = _parse_iso(bin_meta.get("flight_end_utc"))

    cache_dir = project_root / config["options"]["openmeteo_cache_dir"]
    cache_dir.mkdir(parents=True, exist_ok=True)
    timeout = int(config["options"].get("openmeteo_timeout_sec", 30))

    api_called = False
    cache_hit = False
    raw = None
    error = None
    fallback_used = False

    if lat is None or lng is None or flight_start is None or flight_end is None:
        error = "missing inputs (takeoff lat/lng or flight window) — cannot query Open-Meteo"
    else:
        date_str = flight_start.strftime("%Y-%m-%d")
        cache_path = cache_dir / _cache_key(lat, lng, date_str)
        if cache_path.exists():
            try:
                raw = json.loads(cache_path.read_text())
                cache_hit = True
            except (json.JSONDecodeError, OSError) as e:
                error = f"cache read failed: {e}"

        if raw is None:
            query = urllib.parse.urlencode({
                "latitude": round(lat, 4),
                "longitude": round(lng, 4),
                "start_date": date_str,
                "end_date": date_str,
                "hourly": "wind_speed_10m",
            })
            url = f"{OPEN_METEO_URL}?{query}"
            try:
                api_called = True
                raw = _fetch(url, timeout)
                cache_path.write_text(json.dumps(raw, sort_keys=True))
            except (urllib.error.URLError, socket.timeout, json.JSONDecodeError, OSError) as e:
                error = f"API call failed: {type(e).__name__}: {e}"
                raw = None

    # ---- Compute mean wind over the flight window from the hourly series ----
    mean_wind = None
    samples_used = []
    if raw is not None:
        times = raw.get("hourly", {}).get("time", [])
        speeds = raw.get("hourly", {}).get("wind_speed_10m", [])
        # Open-Meteo hourly times are local-naive in the response's timezone
        # (default UTC for archive-api when no tz specified). Treat as UTC.
        for t_str, v in zip(times, speeds):
            try:
                t = datetime.fromisoformat(t_str).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if v is None:
                continue
            # Include the hour if it overlaps the flight window (±1 hour buffer)
            if flight_start - hour(1) <= t <= flight_end + hour(1):
                samples_used.append((t.isoformat(), v))
        # Restrict tightly to samples within [flight_start - 1h, flight_end + 1h]
        if samples_used:
            mean_wind = sum(v for _, v in samples_used) / len(samples_used)

    # ---- Fallback proxy: ATT roll/pitch stdev sum if API failed ----
    fallback_band = None
    fallback_proxy = None
    if mean_wind is None:
        fallback_used = True
        roll_stdev = bin_fields.get("L1F_BIN_FDR_009")
        pitch_stdev = bin_fields.get("L1F_BIN_FDR_010")
        if roll_stdev is not None and pitch_stdev is not None:
            # Combined attitude excursion as a coarse wind proxy
            fallback_proxy = round(((roll_stdev ** 2 + pitch_stdev ** 2) ** 0.5), 4)
            # Crude bands tuned to a quad-copter survey drone
            if fallback_proxy < 3.0:
                fallback_band = "calm"
            elif fallback_proxy < 7.0:
                fallback_band = "moderate"
            else:
                fallback_band = "high"
        else:
            fallback_band = "unknown"

    flags_raised = []
    if fallback_used:
        flags_raised.append({
            "flag_id": "FLG_010",
            "flag_name": "WIND_API_FALLBACK",
            "severity": "LOW",
            "stage": "threshold_band",
            "raised_by": "L1F_API_001",
            "context": f"Open-Meteo unavailable; using ATT roll/pitch stdev proxy. error={error}",
        })

    fields = {
        "L1F_API_001": round(mean_wind, 4) if mean_wind is not None else None,
    }

    parser_meta = {
        "parser": "fetch_openmeteo",
        "endpoint": OPEN_METEO_URL,
        "takeoff_lat": lat,
        "takeoff_lng": lng,
        "flight_start_utc": bin_meta.get("flight_start_utc"),
        "flight_end_utc": bin_meta.get("flight_end_utc"),
        "cache_dir": str(cache_dir),
        "cache_hit": cache_hit,
        "api_called": api_called,
        "error": error,
        "samples_used": samples_used,
        "fallback_used": fallback_used,
        "fallback_proxy_value": fallback_proxy,
        "fallback_band": fallback_band,
    }

    return {
        "fields": fields,
        "parser_meta": parser_meta,
        "flags_raised": flags_raised,
    }


def hour(n):
    from datetime import timedelta
    return timedelta(hours=n)


def main() -> int:
    """Standalone run: parses BIN on the fly. Slow because parse_bin is slow."""
    if len(sys.argv) != 2:
        print("usage: fetch_openmeteo.py <paths.json>", file=sys.stderr)
        return 2
    config_path = Path(sys.argv[1]).resolve()
    project_root = config_path.parent
    config = json.loads(config_path.read_text())

    sys.path.insert(0, str(Path(__file__).parent))
    import parse_bin  # noqa: E402
    bin_result = parse_bin.parse(config, project_root)
    result = fetch(config, project_root, bin_result)
    fields = result["fields"]
    meta = result["parser_meta"]

    print("fetch_openmeteo:")
    print(f"  takeoff: ({meta['takeoff_lat']}, {meta['takeoff_lng']})")
    print(f"  window:  {meta['flight_start_utc']} → {meta['flight_end_utc']}")
    print(f"  cache hit: {meta['cache_hit']}   api called: {meta['api_called']}   error: {meta['error']}")
    print(f"  samples used: {len(meta['samples_used'])}  -> {meta['samples_used'][:5]}")
    print(f"  fallback used: {meta['fallback_used']}  band: {meta['fallback_band']}  proxy: {meta['fallback_proxy_value']}")
    print()
    print(f"  L1F_API_001 mean_wind_speed_ms = {fields['L1F_API_001']}")
    print(f"  flags raised: {[f['flag_name'] for f in result['flags_raised']] or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
