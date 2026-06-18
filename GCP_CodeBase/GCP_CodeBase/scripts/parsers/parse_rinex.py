#!/usr/bin/env python3
"""parse_rinex.py — SRC_GCP_RINEX parser (per point / per occupation).

Emits L1F_GCP_001..018 (18 source fields) for one GCP device's RINEX
observation file. Called once per discovered point folder.

Single streaming pass over the body to keep memory bounded for long
occupations. The pass produces:
  - per-satellite C/N0 mean and std-dev (Welford accumulator)  -> L1F_GCP_015
  - per-satellite cycle-slip counts (LLI bit 0)                -> L1F_GCP_016
  - epoch timestamps -> obs_start/end, epoch_interval_sec, inter-epoch gap
    stats (feed L2D_GCP gap/continuity fields)                 -> L1F_GCP_009/010/013/014
  - sat_count_per_epoch summary + first-window samples         -> L1F_GCP_018
  - PDOP series from sibling NAV + broadcast ephemeris         -> L1F_GCP_017

Hardware Override (4-tier priority) for the header-identity fields
(marker_name / antenna_type / receiver_type / firmware_version / device_id):
  1. RINEX header (skipped if blank)
  2. <point_folder>/hardware.json  (per-point operator override)
  3. inferred from COMMENT records (best-effort)
  4. empty string + parser_meta note

The override is PER POINT (GCP devices each get their own folder), unlike the
base-station build's single survey-wide hardware.json.
"""
from __future__ import annotations

import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PARSER_ID = "parse_rinex"
PARSER_VERSION = "1.0"  # GCP: header via georinex hybrid; body streamed in-house; adds device_id (L1F_GCP_012)
SOURCE_FILE_ID = "SRC_GCP_RINEX"

# Cadence for PDOP samples (seconds of session time). PDOP varies slowly with
# satellite geometry; 30s sampling captures the mean/max profile with
# negligible loss. (Mirrors paths.json options.pdop_sample_cadence_sec.)
PDOP_SAMPLE_INTERVAL_SEC = 30.0
PDOP_ELEVATION_MASK_DEG = 10.0  # survey-GNSS industry standard (mirrors options.pdop_elevation_mask_deg)

# Advisory only — the authoritative format gate is L2D_GCP_013
# (rinex_version_supported), evaluated in Stage 3a by reading the spec. Kept
# here solely to annotate parser_meta.notes. Mirrors the spec's set:
#   rinex_version in {2.10, 2.11, 3.02, 3.03, 3.04, 3.05}
SUPPORTED_RINEX_VERSIONS = {"2.10", "2.11", "3.02", "3.03", "3.04", "3.05"}

# Constellation letter -> human name (just for parser_meta notes).
SYS_NAME = {"G": "GPS", "R": "GLONASS", "E": "Galileo", "J": "QZSS", "C": "BeiDou", "S": "SBAS", "I": "IRNSS"}

# C/N0 / sat-count sample window for the acquisition derivation (L2D_GCP_012):
# keep all epochs in the first 600s so acquisition ramp has full resolution.
ACQUISITION_WINDOW_SEC = 600.0


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond:06d}Z"


# ----------------------------------------------------------------------------
# Header parsing (georinex hybrid)
# ----------------------------------------------------------------------------

def _parse_header(path: Path) -> dict[str, Any]:
    """Parse RINEX header via georinex (1.16+) — robust to vendor quirks,
    multi-line SYS/#/OBS TYPES, and RINEX 2.x / 3.x format differences.

    Body streaming below stays in-house for speed; only header parsing uses
    the library (the full xarray obs load path was ~600x slower in base-build
    benchmarking).
    """
    import georinex as gr  # heavy import — lazy
    raw = gr.rinexheader(path)

    def _grab(key: str, slice_: tuple[int, int] | None = None, default: str = "") -> str:
        s = raw.get(key, default) or default
        if not isinstance(s, str):
            return ""
        if slice_ is None:
            return s.strip()
        return s[slice_[0]:slice_[1]].strip()

    # Comments come back as a single concatenated string from georinex; split
    # by newline OR by multiple-space runs to recover individual COMMENT records
    # for the Tier-3 inference logic below.
    comment_raw = raw.get("COMMENT", "") or ""
    if isinstance(comment_raw, list):
        comments = [str(c).strip() for c in comment_raw if str(c).strip()]
    else:
        s = str(comment_raw)
        if "\n" in s:
            comments = [c.strip() for c in s.split("\n") if c.strip()]
        else:
            # georinex concatenates multi-record COMMENTs with whitespace;
            # split on runs of >=4 spaces to recover the original lines.
            import re as _re
            parts = [p.strip() for p in _re.split(r"\s{4,}", s) if p.strip()]
            comments = parts or ([s.strip()] if s.strip() else [])

    obs_types_by_sys = dict(raw.get("fields", {}) or {})

    # rinex_version comes back as a float (e.g. 3.03); preserve as a 4-char
    # string for spec compatibility (L2D_GCP_013 looks up against a string set).
    ver = raw.get("version")
    if isinstance(ver, (int, float)):
        rinex_version = f"{float(ver):.2f}"
    else:
        rinex_version = str(ver or "").strip()

    header: dict[str, Any] = {
        "rinex_version": rinex_version,
        "file_type_char": str(raw.get("filetype", "") or "").strip().upper()[:1],
        "satellite_system_char": str(raw.get("system", "") or "").strip().upper()[:1] or (
            "M" if len(obs_types_by_sys) > 1 else (next(iter(obs_types_by_sys.keys()), ""))
        ),
        "marker_name": _grab("MARKER NAME"),
        "marker_number": _grab("MARKER NUMBER"),
        "antenna_type": _grab("ANT # / TYPE", (20, 40)),
        "antenna_number": _grab("ANT # / TYPE", (0, 20)),
        "receiver_type": _grab("REC # / TYPE / VERS", (20, 40)),
        "receiver_number": _grab("REC # / TYPE / VERS", (0, 20)),
        "firmware_version": _grab("REC # / TYPE / VERS", (40, 60)),
        "approx_position_xyz": None,
        "antenna_delta_h_e_n": None,
        "time_of_first_obs": raw.get("t0"),
        "time_of_last_obs": raw.get("t1"),
        "interval_header_sec": raw.get("interval"),
        "obs_types_by_sys": obs_types_by_sys,
        "comments": comments,
        "leap_seconds": (
            int(raw["LEAP SECONDS"].split()[0]) if (isinstance(raw.get("LEAP SECONDS"), str) and raw["LEAP SECONDS"].split())
            else (raw.get("LEAP SECONDS") if isinstance(raw.get("LEAP SECONDS"), int) else None)
        ),
        "pgm": _grab("PGM / RUN BY / DATE", (0, 20)),
        "run_by": _grab("PGM / RUN BY / DATE", (20, 40)),
        "pgm_date": _grab("PGM / RUN BY / DATE", (40, 60)),
        "_end_of_header_seen": True,    # if rinexheader returned, EOH was reached
        "_header_parser": f"georinex {getattr(gr, '__version__', '?')}",
    }

    pos_str = raw.get("APPROX POSITION XYZ", "")
    if isinstance(pos_str, str):
        parts = pos_str.split()
        if len(parts) >= 3:
            try:
                header["approx_position_xyz"] = [float(parts[0]), float(parts[1]), float(parts[2])]
            except ValueError:
                pass

    delta_str = raw.get("ANTENNA: DELTA H/E/N", "")
    if isinstance(delta_str, str):
        parts = delta_str.split()
        if len(parts) >= 3:
            try:
                header["antenna_delta_h_e_n"] = [float(parts[0]), float(parts[1]), float(parts[2])]
            except ValueError:
                pass

    return header


def _parse_time_of_obs(value: str) -> datetime | None:
    """Parse '2026     5    19    11     1   27.3960000     GPS' style."""
    if not value:
        return None
    parts = value.split()
    if len(parts) < 6:
        return None
    try:
        y, mo, d, h, mi = (int(parts[i]) for i in range(5))
        s = float(parts[5])
    except ValueError:
        return None
    sec = int(s)
    us = int(round((s - sec) * 1_000_000))
    if us == 1_000_000:
        sec += 1
        us = 0
    try:
        return datetime(y, mo, d, h, mi, sec, us, tzinfo=timezone.utc)
    except ValueError:
        return None


# ----------------------------------------------------------------------------
# Body stream  (constellation-agnostic; lifted verbatim from base build)
# ----------------------------------------------------------------------------

def _stream_body(path: Path, obs_types_by_sys: dict[str, list[str]]) -> dict[str, Any]:
    interesting: dict[str, list[tuple[int, str]]] = {}
    for sys_char, types in obs_types_by_sys.items():
        positions: list[tuple[int, str]] = []
        for i, otype in enumerate(types):
            if otype.startswith("S") or otype.startswith("L"):
                start = 3 + i * 16
                positions.append((start, otype))
        interesting[sys_char] = positions

    epoch_count = 0
    first_epoch_dt: datetime | None = None
    last_epoch_dt: datetime | None = None
    prev_epoch_dt: datetime | None = None

    inter_epoch_intervals: list[float] = []
    max_inter_epoch_sec = 0.0
    count_gap_gt_5s = 0
    count_gap_gt_60s = 0

    sat_count_min = math.inf
    sat_count_max = 0
    sat_count_sum = 0
    sat_count_n = 0
    acquisition_samples: list[tuple[float, int]] = []  # (offset_sec, nsat)

    cn0_welford: dict[str, list[float]] = {}  # sat_id -> [n, mean, M2]
    cn0_welford_per_band: dict[str, dict[str, list[float]]] = {}  # sat_id -> band -> stats
    cycle_slip_per_sat: dict[str, int] = {}
    total_cycle_slips = 0
    sats_seen: set[str] = set()

    pdop_sample_epochs: list[dict[str, Any]] = []  # [{dt, sats: [sat_id,..]}, ...]
    current_pdop_sample: dict[str, Any] | None = None
    last_pdop_sample_dt: datetime | None = None

    in_body = False

    with path.open("rb") as fh:
        for raw in fh:
            line = raw.decode("ascii", errors="replace").rstrip("\r\n")
            if not in_body:
                if "END OF HEADER" in line:
                    in_body = True
                continue
            if not line:
                continue

            if line[0] == ">":
                parts = line.split()
                if len(parts) < 9:
                    continue
                try:
                    y = int(parts[1]); mo = int(parts[2]); d = int(parts[3])
                    h = int(parts[4]); mi = int(parts[5]); s = float(parts[6])
                    _eflag = int(parts[7]); nsat = int(parts[8])
                except (ValueError, IndexError):
                    continue
                sec = int(s)
                us = int(round((s - sec) * 1_000_000))
                if us == 1_000_000:
                    sec += 1
                    us = 0
                try:
                    dt = datetime(y, mo, d, h, mi, sec, us, tzinfo=timezone.utc)
                except ValueError:
                    continue
                # Finalize any in-progress PDOP sample on the prior epoch.
                if current_pdop_sample is not None:
                    pdop_sample_epochs.append(current_pdop_sample)
                    current_pdop_sample = None
                epoch_count += 1
                if first_epoch_dt is None:
                    first_epoch_dt = dt
                if prev_epoch_dt is not None:
                    delta = (dt - prev_epoch_dt).total_seconds()
                    if delta > 0:
                        inter_epoch_intervals.append(delta)
                        if delta > max_inter_epoch_sec:
                            max_inter_epoch_sec = delta
                        if delta > 5.0:
                            count_gap_gt_5s += 1
                        if delta > 60.0:
                            count_gap_gt_60s += 1
                last_epoch_dt = dt
                prev_epoch_dt = dt
                if nsat < sat_count_min:
                    sat_count_min = nsat
                if nsat > sat_count_max:
                    sat_count_max = nsat
                sat_count_sum += nsat
                sat_count_n += 1
                offset = (dt - first_epoch_dt).total_seconds()
                if offset <= ACQUISITION_WINDOW_SEC:
                    acquisition_samples.append((offset, nsat))
                # Open a new PDOP sample if cadence elapsed.
                if (
                    last_pdop_sample_dt is None
                    or (dt - last_pdop_sample_dt).total_seconds() >= PDOP_SAMPLE_INTERVAL_SEC
                ):
                    current_pdop_sample = {"dt": dt, "sats": []}
                    last_pdop_sample_dt = dt
                continue

            sys_char = line[0]
            if sys_char not in interesting:
                continue
            sat_id = line[0:3]
            sats_seen.add(sat_id)
            if current_pdop_sample is not None:
                current_pdop_sample["sats"].append(sat_id)
            llen = len(line)
            for start, otype in interesting[sys_char]:
                if start + 16 > llen:
                    break
                if otype[0] == "S":
                    seg = line[start:start + 14].strip()
                    if not seg:
                        continue
                    try:
                        cn0 = float(seg)
                    except ValueError:
                        continue
                    if cn0 <= 0.0:
                        continue
                    st = cn0_welford.get(sat_id)
                    if st is None:
                        st = [0.0, 0.0, 0.0]
                        cn0_welford[sat_id] = st
                    st[0] += 1.0
                    delta = cn0 - st[1]
                    st[1] += delta / st[0]
                    st[2] += delta * (cn0 - st[1])

                    band = otype[1:2]
                    by_band = cn0_welford_per_band.setdefault(sat_id, {})
                    bst = by_band.get(band)
                    if bst is None:
                        bst = [0.0, 0.0, 0.0]
                        by_band[band] = bst
                    bst[0] += 1.0
                    bdelta = cn0 - bst[1]
                    bst[1] += bdelta / bst[0]
                    bst[2] += bdelta * (cn0 - bst[1])
                else:
                    lli_char = line[start + 14]
                    if lli_char in ("1", "3", "5", "7"):
                        cycle_slip_per_sat[sat_id] = cycle_slip_per_sat.get(sat_id, 0) + 1
                        total_cycle_slips += 1

    # Flush the last in-progress PDOP sample if any.
    if current_pdop_sample is not None:
        pdop_sample_epochs.append(current_pdop_sample)

    # ---- finalize stats ----
    if inter_epoch_intervals:
        median_interval = statistics.median(inter_epoch_intervals)
    else:
        median_interval = None

    per_sat_cn0 = {}
    for sat_id, st in sorted(cn0_welford.items()):
        n = st[0]
        if n < 1:
            continue
        mean = round(st[1], 3)
        std = round(math.sqrt(st[2] / (n - 1)), 3) if n > 1 else 0.0
        per_band_out = {}
        for band, bst in cn0_welford_per_band.get(sat_id, {}).items():
            bn = bst[0]
            if bn < 1:
                continue
            per_band_out[band] = {
                "n": int(bn),
                "mean_dbhz": round(bst[1], 3),
                "std_dbhz": round(math.sqrt(bst[2] / (bn - 1)), 3) if bn > 1 else 0.0,
            }
        per_sat_cn0[sat_id] = {
            "n": int(n),
            "mean_dbhz": mean,
            "std_dbhz": std,
            "per_band": per_band_out,
        }
    overall_n = sum(int(st[0]) for st in cn0_welford.values())
    overall_mean = (
        round(sum(st[0] * st[1] for st in cn0_welford.values()) / overall_n, 3)
        if overall_n
        else None
    )

    sat_count_summary = {
        "min": int(sat_count_min) if sat_count_n else None,
        "max": int(sat_count_max) if sat_count_n else None,
        "mean": round(sat_count_sum / sat_count_n, 3) if sat_count_n else None,
        "n_epochs": sat_count_n,
    }

    return {
        "epoch_count": epoch_count,
        "first_epoch_dt": first_epoch_dt,
        "last_epoch_dt": last_epoch_dt,
        "median_inter_epoch_sec": median_interval,
        "max_inter_epoch_sec": round(max_inter_epoch_sec, 3),
        "count_gap_gt_5s": count_gap_gt_5s,
        "count_gap_gt_60s": count_gap_gt_60s,
        "sat_count_summary": sat_count_summary,
        "acquisition_samples": [(round(o, 3), n) for o, n in acquisition_samples],
        "cn0_per_sat": per_sat_cn0,
        "cn0_overall_mean_dbhz": overall_mean,
        "cn0_overall_n_samples": overall_n,
        "cycle_slip_per_sat": dict(sorted(cycle_slip_per_sat.items())),
        "cycle_slip_total": total_cycle_slips,
        "sats_seen_count": len(sats_seen),
        "pdop_sample_epochs": pdop_sample_epochs,
    }


# ----------------------------------------------------------------------------
# Hardware Override (4-tier resolution)
# ----------------------------------------------------------------------------

def _resolve(field_name: str, header_val: str, override: dict | None, comments: list[str]) -> tuple[str, str]:
    """Return (resolved_value, source_tier)."""
    if header_val:
        return header_val, "tier_1_rinex_header"
    if override and field_name in override and override[field_name]:
        return str(override[field_name]).strip(), "tier_2_operator_override"
    inferred = _infer_from_comments(field_name, comments)
    if inferred:
        return inferred, "tier_3_inferred_from_comment"
    return "", "tier_4_empty_with_note"


def _infer_from_comments(field_name: str, comments: list[str]) -> str:
    joined = " ".join(comments).lower()
    if field_name == "receiver_type":
        if "u-blox" in joined or "ublox" in joined:
            return "u-blox (inferred from COMMENT input_format)"
    # firmware_version: the RINEX PGM record names the conversion tool, not the
    # receiver firmware. device_id: a serial cannot be inferred from a format
    # comment. Both fall to Tier 4 (honest empty) when header/override are blank.
    return ""


def _load_override(point_folder: Path, override_filename: str = "hardware.json") -> tuple[dict | None, Path | None]:
    """Load the per-point hardware override from <point_folder>/<filename>.

    Returns (override_dict, path). Placeholder lifecycle markers (_status,
    _note) are ignored by _resolve, which only reads field-named keys.
    """
    candidate = point_folder / override_filename
    if not candidate.exists():
        return None, None
    try:
        with candidate.open("r", encoding="utf-8") as fh:
            return json.load(fh), candidate
    except (OSError, json.JSONDecodeError):
        return None, candidate


# ----------------------------------------------------------------------------
# Main parse() entry point
# ----------------------------------------------------------------------------

def _detect_dual_freq(obs_types_by_sys: dict[str, list[str]]) -> bool:
    """True if any system reports carrier phase on at least two distinct freq bands."""
    for types in obs_types_by_sys.values():
        bands = {t[1:2] for t in types if t.startswith("L") and len(t) >= 2}
        if len(bands) >= 2:
            return True
    return False


def _compute_pdop_series(
    pdop_sample_epochs: list[dict[str, Any]],
    receiver_pos_ecef: list[float] | None,
    rinex_obs_path: Path,
) -> dict[str, Any]:
    """Compute PDOP at each sampled epoch using broadcast ephemeris.

    Looks for a sibling NAV file next to the OBS file (same stem, .NN[pn] or
    .nav). Returns a dict with per-epoch PDOP series + summary statistics +
    metadata about ephemeris coverage. If the NAV file is missing or the
    receiver position is unknown, returns a structured null result (PDOP
    degrades rather than hard-fails — matches Stage 1 critical_set_policy).
    """
    result: dict[str, Any] = {
        "samples": [],
        "summary": {"min": None, "max": None, "mean": None, "median": None, "n_samples": 0},
        "nav_file": None,
        "ephemerides_sats": 0,
        "notes": [],
    }

    if not receiver_pos_ecef:
        result["notes"].append(
            "Receiver approx_position_xyz unavailable — PDOP cannot be computed without it."
        )
        return result

    # Locate sibling NAV file.
    nav_candidates: list[Path] = []
    stem = rinex_obs_path.stem
    parent = rinex_obs_path.parent
    for p in sorted(parent.iterdir()):
        if not p.is_file() or p == rinex_obs_path:
            continue
        if p.stem != stem:
            continue
        sfx = p.suffix.lower()
        # Accept .nav / .NNn / .NNp / .NNg / .NNl / .NNq / .n / .p
        if sfx == ".nav" or sfx == ".n" or sfx == ".p":
            nav_candidates.append(p)
        elif len(sfx) == 4 and sfx[1:3].isdigit() and sfx[3] in ("n", "p", "g", "l", "q"):
            nav_candidates.append(p)
    if not nav_candidates:
        result["notes"].append(
            f"No NAV file found alongside {rinex_obs_path.name}; PDOP left null."
        )
        return result
    nav_path = nav_candidates[0]
    result["nav_file"] = nav_path.name

    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import parse_nav  # type: ignore
        import gnss_orbits  # type: ignore
    finally:
        _sys.path.pop(0)

    parsed_nav = parse_nav.parse_nav(nav_path)
    eph_index = gnss_orbits.build_ephemeris_index(parsed_nav)
    result["ephemerides_sats"] = len(eph_index)

    if not pdop_sample_epochs:
        result["notes"].append("No PDOP sample epochs collected by body streamer.")
        return result

    rx = tuple(receiver_pos_ecef)
    series: list[dict[str, Any]] = []
    skipped_no_eph = 0
    used_total_sats = 0
    n_with_pdop = 0
    for sample in pdop_sample_epochs:
        epoch_dt = sample["dt"]
        sat_positions: list[tuple[float, float, float]] = []
        for sat_id in sample["sats"]:
            sys_char = sat_id[0]
            records = eph_index.get(sat_id)
            if not records:
                skipped_no_eph += 1
                continue
            eph = gnss_orbits.closest_ephemeris(records, epoch_dt, sys_char)
            if eph is None:
                skipped_no_eph += 1
                continue
            pos = gnss_orbits.propagate(eph, epoch_dt, sys_char)
            if pos is None:
                continue
            sat_positions.append(pos)
        pdop_val, n_used = gnss_orbits.compute_pdop(sat_positions, rx, elevation_mask_deg=PDOP_ELEVATION_MASK_DEG)
        if pdop_val is not None:
            series.append({
                "epoch_utc": _iso(epoch_dt),
                "pdop": round(pdop_val, 3),
                "n_sats_used": n_used,
                "n_sats_in_epoch": len(sample["sats"]),
            })
            used_total_sats += n_used
            n_with_pdop += 1

    if series:
        vals = [s["pdop"] for s in series]
        result["summary"] = {
            "min": round(min(vals), 3),
            "max": round(max(vals), 3),
            "mean": round(sum(vals) / len(vals), 3),
            "median": round(statistics.median(vals), 3),
            "n_samples": len(vals),
            "mean_n_sats_in_solution": round(used_total_sats / n_with_pdop, 1) if n_with_pdop else None,
        }
    result["samples"] = series

    if skipped_no_eph > 0:
        result["notes"].append(
            f"{skipped_no_eph} sat-epoch lookups had no usable ephemeris (typical when "
            "broadcast NAV doesn't cover every observed satellite)."
        )
    result["notes"].append(
        f"PDOP sampled every {PDOP_SAMPLE_INTERVAL_SEC:.0f}s; elevation mask "
        f"{PDOP_ELEVATION_MASK_DEG:.1f}°."
    )
    return result


def parse(rinex_obs_path: Path, project_root: Path, hardware_override_path: Path | None = None) -> dict[str, Any]:
    """Parse one GCP occupation's RINEX OBS (+ sibling NAV for PDOP).

    hardware_override_path: explicit per-point override file. When None,
    defaults to <point_folder>/hardware.json (the point folder is the OBS
    file's parent). Stage 2 passes the inventory-resolved path so a
    configurable hardware_filename is honoured.
    """
    started_at = datetime.now(timezone.utc)
    header = _parse_header(rinex_obs_path)
    body = _stream_body(rinex_obs_path, header["obs_types_by_sys"])

    if hardware_override_path is not None:
        override, override_path = _load_override(
            hardware_override_path.parent, hardware_override_path.name
        )
    else:
        override, override_path = _load_override(rinex_obs_path.parent)

    pdop_result = _compute_pdop_series(
        body["pdop_sample_epochs"], header["approx_position_xyz"], rinex_obs_path
    )

    # 4-tier resolved header-identity fields.
    marker_name, marker_src = _resolve("marker_name", header["marker_name"], override, header["comments"])
    antenna_type, antenna_type_src = _resolve("antenna_type", header["antenna_type"], override, header["comments"])
    receiver_type, receiver_type_src = _resolve("receiver_type", header["receiver_type"], override, header["comments"])
    firmware_version, firmware_src = _resolve("firmware_version", header["firmware_version"], override, header["comments"])
    # device_id (L1F_GCP_012): a GCP device's serial/ID. RINEX header source is
    # the receiver serial (REC # field); marker_number is a secondary fallback.
    device_id_header = header["receiver_number"] or header["marker_number"]
    device_id, device_id_src = _resolve("device_id", device_id_header, override, header["comments"])

    def _coerce_header_dt(value):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, str):
            return _parse_time_of_obs(value)
        return None

    obs_start_dt = body["first_epoch_dt"] or _coerce_header_dt(header["time_of_first_obs"])
    obs_end_dt = body["last_epoch_dt"] or _coerce_header_dt(header["time_of_last_obs"])

    epoch_interval_sec = header["interval_header_sec"] or body["median_inter_epoch_sec"]
    constellation_set = sorted(header["obs_types_by_sys"].keys())
    dual_freq_present = _detect_dual_freq(header["obs_types_by_sys"])

    notes: list[str] = []
    flags_raised: list[dict] = []

    if not header["marker_name"]:
        notes.append(f"marker_name blank in RINEX header → resolved via {marker_src}.")
    if not header["antenna_type"]:
        notes.append(f"antenna_type blank in RINEX header → resolved via {antenna_type_src}.")
    if not header["receiver_type"]:
        notes.append(f"receiver_type blank in RINEX header → resolved via {receiver_type_src}.")
    if not header["firmware_version"]:
        notes.append(f"firmware_version blank in RINEX header → resolved via {firmware_src}.")
    if not device_id_header:
        notes.append(f"device_id blank in RINEX header → resolved via {device_id_src}.")
    if header["antenna_delta_h_e_n"] == [0.0, 0.0, 0.0]:
        notes.append(
            "antenna_delta_h is 0.000 in RINEX header — typical when the device leaves it unset. "
            "L2D_GCP_018 antenna_height_agreement will treat the header height as absent and skip "
            "the cross-check against the operator-entered antenna_height_m."
        )

    body_n_intervals = body["epoch_count"] - 1 if body["epoch_count"] > 0 else 0
    if epoch_interval_sec and body["epoch_count"] > 1:
        notes.append(
            f"epoch_interval_sec derived as median of {body_n_intervals} inter-epoch deltas = {epoch_interval_sec}s. "
            f"max_inter_epoch_gap = {body['max_inter_epoch_sec']}s."
        )

    if pdop_result["summary"]["n_samples"] > 0:
        s = pdop_result["summary"]
        notes.append(
            f"L1F_GCP_017 pdop_per_epoch computed from broadcast ephemeris "
            f"({pdop_result['nav_file']}): {s['n_samples']} samples at "
            f"{int(PDOP_SAMPLE_INTERVAL_SEC)}s cadence; mean={s['mean']}, "
            f"max={s['max']}, mean_sats_in_solution={s['mean_n_sats_in_solution']}."
        )
    else:
        for n in pdop_result["notes"]:
            notes.append(f"L1F_GCP_017 pdop_per_epoch null: {n}")
    notes.append(
        "L1F_GCP_015 cn0_per_sat retained as per-satellite Welford aggregates (mean, std, n, per band) "
        "rather than full per-epoch arrays. cn0_mean (L2D_GCP_008) and multipath_risk (L2D_GCP_009) "
        "are computable from these aggregates."
    )
    notes.append(
        "L1F_GCP_018 sat_count_per_epoch retained as overall summary + first "
        f"{int(ACQUISITION_WINDOW_SEC)}s samples for the acquisition derivation (L2D_GCP_012)."
    )

    rinex_version_str = header["rinex_version"]
    if rinex_version_str and rinex_version_str not in SUPPORTED_RINEX_VERSIONS:
        notes.append(
            f"RINEX version {rinex_version_str} not in the supported set; L2D_GCP_013 will be False "
            "and RINEX_VERSION_UNSUPPORTED (FLG_006) will trip at the format indicator in Stage 3b."
        )

    obs_start_utc_iso = _iso(obs_start_dt) if obs_start_dt else None
    obs_end_utc_iso = _iso(obs_end_dt) if obs_end_dt else None

    fields = {
        "L1F_GCP_001_marker_name": marker_name,
        "L1F_GCP_002_antenna_type": antenna_type,
        "L1F_GCP_003_antenna_delta_h": (
            header["antenna_delta_h_e_n"][0] if header["antenna_delta_h_e_n"] else None
        ),
        "L1F_GCP_004_receiver_type": receiver_type,
        "L1F_GCP_005_firmware_version": firmware_version,
        "L1F_GCP_006_approx_position_xyz": header["approx_position_xyz"],
        "L1F_GCP_007_rinex_version": rinex_version_str,
        "L1F_GCP_008_constellation_set": constellation_set,
        "L1F_GCP_009_obs_start_utc": obs_start_utc_iso,
        "L1F_GCP_010_obs_end_utc": obs_end_utc_iso,
        "L1F_GCP_011_dual_freq_present": dual_freq_present,
        "L1F_GCP_012_device_id": device_id,
        "L1F_GCP_013_epoch_interval_sec": (
            round(epoch_interval_sec, 4) if epoch_interval_sec is not None else None
        ),
        "L1F_GCP_014_total_epochs": body["epoch_count"],
        "L1F_GCP_015_cn0_per_sat": {
            "overall_mean_dbhz": body["cn0_overall_mean_dbhz"],
            "overall_n_samples": body["cn0_overall_n_samples"],
            "per_sat": body["cn0_per_sat"],
        },
        "L1F_GCP_016_cycle_slip_markers": {
            "total_count": body["cycle_slip_total"],
            "per_sat_count": body["cycle_slip_per_sat"],
        },
        "L1F_GCP_017_pdop_per_epoch": (
            {
                "summary": pdop_result["summary"],
                "sample_interval_sec": PDOP_SAMPLE_INTERVAL_SEC,
                "elevation_mask_deg": PDOP_ELEVATION_MASK_DEG,
                "nav_file": pdop_result["nav_file"],
                "ephemerides_sats_available": pdop_result["ephemerides_sats"],
                "samples": pdop_result["samples"],
                "_notes": pdop_result["notes"],
            }
            if pdop_result["summary"]["n_samples"] > 0
            else None
        ),
        "L1F_GCP_018_sat_count_per_epoch": {
            "summary": body["sat_count_summary"],
            "acquisition_window_sec": ACQUISITION_WINDOW_SEC,
            "acquisition_samples": body["acquisition_samples"],
        },
    }

    field_sources = {
        "L1F_GCP_001_marker_name": marker_src,
        "L1F_GCP_002_antenna_type": antenna_type_src,
        "L1F_GCP_003_antenna_delta_h": "tier_1_rinex_header" if header["antenna_delta_h_e_n"] else "tier_4_empty_with_note",
        "L1F_GCP_004_receiver_type": receiver_type_src,
        "L1F_GCP_005_firmware_version": firmware_src,
        "L1F_GCP_006_approx_position_xyz": "tier_1_rinex_header" if header["approx_position_xyz"] else "tier_4_empty_with_note",
        "L1F_GCP_007_rinex_version": "tier_1_rinex_header",
        "L1F_GCP_008_constellation_set": "tier_1_rinex_header_sys_obs_types",
        "L1F_GCP_009_obs_start_utc": "body_first_epoch",
        "L1F_GCP_010_obs_end_utc": "body_last_epoch",
        "L1F_GCP_011_dual_freq_present": "tier_1_rinex_header_sys_obs_types",
        "L1F_GCP_012_device_id": device_id_src,
        "L1F_GCP_013_epoch_interval_sec": (
            "tier_1_rinex_header_interval" if header["interval_header_sec"] else "body_median_inter_epoch"
        ),
        "L1F_GCP_014_total_epochs": "body_stream_count",
        "L1F_GCP_015_cn0_per_sat": "body_stream_per_sat_welford",
        "L1F_GCP_016_cycle_slip_markers": "body_stream_lli_bit0",
        "L1F_GCP_017_pdop_per_epoch": (
            "computed_from_broadcast_ephemeris"
            if pdop_result["summary"]["n_samples"] > 0
            else "null_no_nav_or_no_receiver_pos"
        ),
        "L1F_GCP_018_sat_count_per_epoch": "body_stream_epoch_nsat",
    }

    finished_at = datetime.now(timezone.utc)

    override_rel = None
    if override_path is not None:
        try:
            override_rel = str(override_path.relative_to(project_root))
        except ValueError:
            override_rel = str(override_path)

    parser_meta = {
        "parser_id": PARSER_ID,
        "parser_version": PARSER_VERSION,
        "source_file_id": SOURCE_FILE_ID,
        "source_file_name": rinex_obs_path.name,
        "instance_found": True,
        "started_at": _iso(started_at),
        "finished_at": _iso(finished_at),
        "wall_time_sec": round((finished_at - started_at).total_seconds(), 3),
        "rinex_header": {
            "rinex_version": rinex_version_str,
            "file_type_char": header["file_type_char"],
            "satellite_system_char": header["satellite_system_char"],
            "constellations_observed": [SYS_NAME.get(c, c) for c in constellation_set],
            "obs_types_by_sys": header["obs_types_by_sys"],
            "antenna_delta_h_e_n": header["antenna_delta_h_e_n"],
            "approx_position_xyz": header["approx_position_xyz"],
            "interval_header_sec": header["interval_header_sec"],
            "leap_seconds": header["leap_seconds"],
            "pgm": header.get("pgm"),
            "run_by": header.get("run_by"),
            "pgm_date": header.get("pgm_date"),
            "end_of_header_seen": header["_end_of_header_seen"],
            "_header_parser": header.get("_header_parser"),
        },
        "stream_stats_for_derived_fields": {
            "max_inter_epoch_sec": body["max_inter_epoch_sec"],
            "count_gap_gt_5s": body["count_gap_gt_5s"],
            "count_gap_gt_60s": body["count_gap_gt_60s"],
            "sats_seen_count": body["sats_seen_count"],
            "median_inter_epoch_sec": body["median_inter_epoch_sec"],
        },
        "fields_provided": sorted(fields.keys()),
        "field_sources": field_sources,
        "hardware_override_used": override is not None,
        "hardware_override_path": override_rel,
        "notes": notes,
        "flags_raised": flags_raised,
    }

    return {"fields": fields, "parser_meta": parser_meta}


# ----------------------------------------------------------------------------
# CLI for standalone smoke-test
# ----------------------------------------------------------------------------

def _cli(argv: list[str]) -> int:
    import sys
    if len(argv) != 3:
        print("usage: parse_rinex.py <project_root> <rinex_obs_path>", file=sys.stderr)
        return 2
    root = Path(argv[1]).resolve()
    rinex_path = Path(argv[2]).resolve()
    out = parse(rinex_path, root)
    json.dump(out, sys.stdout, indent=2, sort_keys=True, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv))
