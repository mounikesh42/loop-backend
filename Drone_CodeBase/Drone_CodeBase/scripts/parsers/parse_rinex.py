#!/usr/bin/env python3
"""Stage 2 parser — Rover RINEX (SRC_GNSS_01).

Produces L1F_GNSS_001..019 using the georinex library. georinex is the
established Python RINEX parser; it handles edge cases (RINEX 2.x vs 3.x,
event-flag epochs, header continuation lines, unusual obs-type combos)
that a hand-rolled scanner can miss. The trade-off is load time: ~3-4
minutes on a 25MB RINEX 3.03 mixed-GNSS file at 5 Hz with 5 constellations.

Field ownership:
  Header (instant scan):    001 obs_start_utc, 002 obs_end_utc,
                            003 obs_duration_sec, 007 epochs_per_second,
                            018 receiver_type, 019 antenna_type
  Epoch-by-epoch stats:     006 epochs_total, 008 cn0_mean_dbhz,
                            009 cn0_min_dbhz, 010 sat_count_mean,
                            011 sat_count_min, 012 cycle_slip_count,
                            013 dual_freq_ratio, 014 triple_freq_ratio,
                            015 constellation_count, 016 gap_gt_5s_count,
                            017 any_gap_gt_60s
  Joined with BIN (Step 6): 004 pre_buffer_sec, 005 post_buffer_sec
                            (left null here; orchestrator fills them after
                            parse_bin runs, since obs_start/end and
                            flight_start/end must both exist first).

RINEX duplicate strategy: if multiple observation/navigation files exist
in the rinex_folder, prefer the one whose basename does NOT contain
'(' (i.e. skip 'foo (1).26O' in favour of 'foo.26O'). User-confirmed
in Step 2 review.

Receiver/antenna metadata resolution priority (highest first):
  1. RINEX header (REC # / TYPE / VERS, ANT # / TYPE) — authoritative
     when populated.
  2. SRC_UI_02 operator override file (paths.json -> inputs.user_hardware_file,
     default sample_data/user_input/hardware.json). Optional file with
     {receiver_type, antenna_type} string fields. Used when RINEX header
     is blank — common with u-blox UBX → RINEX conversion that strips
     the REC/ANT fields.
  3. COMMENT-line inference (e.g. 'input_format: u-blox'). Surfaced in
     parser_meta even when not used so the operator can see why a value
     was chosen.
  4. Empty string.

Each L1F_GNSS_018/019 value carries a parser_meta entry naming which
tier resolved it (one of: rinex_header | user_hardware_override |
comment_inference | unresolved).
"""
import json
import re
import statistics
import sys
import warnings
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from path_utils import resolve_path  # noqa: E402

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import georinex as gr


RINEX_OBS_RE = re.compile(r"^\.(\d{2}o|obs)$", re.IGNORECASE)
RINEX_NAV_RE = re.compile(r"^\.(\d{2}[nglp]|nav)$", re.IGNORECASE)


def pick_primary(files: list[Path]) -> Path | None:
    """Choose one file when several candidates exist. Prefer names without '('."""
    if not files:
        return None
    files = sorted(files, key=lambda p: ("(" in p.name, p.name))
    return files[0]


def classify_rinex_files(folder: Path) -> dict:
    obs, nav, other = [], [], []
    for p in sorted(folder.iterdir()):
        if not p.is_file() or p.name.startswith("."):
            continue
        if RINEX_OBS_RE.match(p.suffix):
            obs.append(p)
        elif RINEX_NAV_RE.match(p.suffix):
            nav.append(p)
        else:
            other.append(p)
    return {
        "all_obs": obs,
        "all_nav": nav,
        "other": other,
        "primary_obs": pick_primary(obs),
        "primary_nav": pick_primary(nav),
    }


def _iso_from_np_datetime(dt64) -> str:
    """numpy.datetime64 → ISO 8601 string with microseconds + Z."""
    if dt64 is None:
        return None
    s = str(np.datetime_as_string(dt64, unit="us"))
    if not s.endswith("Z"):
        s += "Z"
    return s


def _band_of_obs_code(code: str) -> str | None:
    """Map RINEX 3 observation code (e.g. 'S1C', 'C2X', 'L7I') to a frequency band tag.

    Convention: 1→L1, 2→L2, 5/7/8→L5-equivalent (Galileo E5a/E5b/E5, BDS B2 etc).
    """
    if len(code) < 2:
        return None
    band_char = code[1]
    if band_char == "1":
        return "L1"
    if band_char == "2":
        return "L2"
    if band_char in ("5", "7", "8"):
        return "L5"
    return None


def _infer_from_comments(header: dict) -> dict:
    """Try to pull receiver/antenna hints from RINEX COMMENT lines.

    georinex may return COMMENT either as a list of separate comment lines
    or as a single concatenated string with the next-label boundary collapsed.
    A naive split(':', 1) fails on the concatenated form, so we use a
    regex anchored on 'input_format:' to grab only the manufacturer token.
    """
    comments = header.get("COMMENT")
    if comments is None:
        return {"receiver_inferred": None, "antenna_inferred": None, "source": None}
    if isinstance(comments, str):
        comments = [comments]
    receiver = None
    antenna = None
    # input_format: <token> — capture the first whitespace-delimited token
    # (e.g. 'u-blox', 'septentrio', 'novatel') after 'input_format:'.
    ifmt_re = re.compile(r"input_format:\s*([^\s]+)", re.IGNORECASE)
    for c in comments:
        if receiver is None:
            m = ifmt_re.search(c)
            if m:
                receiver = m.group(1).strip()
        # No reliable comment convention for antenna in u-blox exports.
    return {
        "receiver_inferred": receiver,
        "antenna_inferred": antenna,
        "source": "RINEX COMMENT line 'input_format'" if receiver else None,
    }


def _load_hardware_override(config: dict, project_root: Path) -> dict:
    """Read SRC_UI_02 hardware.json if configured and present.

    Returns {receiver_type, antenna_type, file_path, present}. Missing file
    or missing key resolves to None — caller falls through to next tier.
    """
    rel = config.get("inputs", {}).get("user_hardware_file")
    if not rel:
        return {"receiver_type": None, "antenna_type": None, "file_path": None, "present": False}
    path = resolve_path(project_root, rel)
    if not path.exists():
        return {"receiver_type": None, "antenna_type": None, "file_path": str(path), "present": False}
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"receiver_type": None, "antenna_type": None, "file_path": str(path), "present": True, "parse_error": True}
    rec = payload.get("receiver_type")
    ant = payload.get("antenna_type")
    return {
        "receiver_type": rec if isinstance(rec, str) and rec.strip() else None,
        "antenna_type": ant if isinstance(ant, str) and ant.strip() else None,
        "file_path": str(path),
        "present": True,
    }


def _resolve_hardware(rinex_raw: str, override_value, inferred_value, kind: str) -> dict:
    """Apply the 4-tier resolution. Returns {value, source}."""
    rinex_raw = (rinex_raw or "").strip()
    if rinex_raw:
        return {"value": rinex_raw, "source": "rinex_header"}
    if override_value:
        return {"value": override_value, "source": "user_hardware_override"}
    if inferred_value:
        return {"value": inferred_value, "source": "comment_inference"}
    return {"value": "", "source": "unresolved"}


def _compute_body_stats(obs_path: Path) -> dict:
    """Load the obs body via georinex and compute Stage 2 statistics."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        # fast=True is acceptable here — it skips a minority of edge-case
        # validations but produces identical statistics for well-formed files.
        # useindicators=True exposes LLI flags as <obs>lli columns for cycle slips.
        ds = gr.load(str(obs_path), fast=True, useindicators=True)

    times = ds.time.values  # np.datetime64[ns]
    sv_list = [str(s) for s in ds.sv.values]
    constellations_seen = sorted({s[0] for s in sv_list if s})

    # ---- C/N0 stats from all S* observables ----
    s_vars = [v for v in ds.data_vars if v.startswith("S") and not v.endswith("lli") and not v.endswith("ssi")]
    cn0_values_all: list[np.ndarray] = []
    for v in s_vars:
        arr = ds[v].values
        flat = arr[~np.isnan(arr)]
        if flat.size:
            cn0_values_all.append(flat)
    cn0_all = np.concatenate(cn0_values_all) if cn0_values_all else np.array([], dtype=float)
    cn0_mean = float(cn0_all.mean()) if cn0_all.size else None
    cn0_min = float(cn0_all.min()) if cn0_all.size else None
    cn0_count = int(cn0_all.size)

    # ---- per-epoch satellite counts ----
    # An SV is "tracked" in epoch t if any observable for it is non-NaN.
    # Use the first signal-strength variable as a sufficient indicator.
    if s_vars:
        # Stack S* observables; a sat is tracked if any S* has a value
        # for it at that epoch.
        any_signal = np.zeros((len(times), len(sv_list)), dtype=bool)
        for v in s_vars:
            arr = ds[v].values  # shape (time, sv)
            any_signal |= ~np.isnan(arr)
        sv_count_per_epoch = any_signal.sum(axis=1)
    else:
        # Fallback: count any non-NaN obs
        first_obs = next(iter(ds.data_vars))
        sv_count_per_epoch = (~np.isnan(ds[first_obs].values)).sum(axis=1)
    sat_count_mean = float(sv_count_per_epoch.mean()) if sv_count_per_epoch.size else None
    sat_count_min = int(sv_count_per_epoch.min()) if sv_count_per_epoch.size else None

    # ---- per-epoch cn0_mean (for rover_acquisition_time_sec) ----
    # cn0_mean_per_epoch[t] = mean of all S* observables at epoch t (across SVs and bands).
    if s_vars:
        cn0_stack = np.stack([ds[v].values for v in s_vars], axis=-1)  # (time, sv, s_var)
        with np.errstate(invalid="ignore"):
            cn0_mean_per_epoch = np.nanmean(cn0_stack.reshape(len(times), -1), axis=1)
    else:
        cn0_mean_per_epoch = np.full(len(times), np.nan)

    # First epoch where sat_count >= 4 AND cn0_mean_per_epoch >= 30.
    # Acquisition time is delta from first epoch to that first stable epoch.
    acq_time_sec = None
    if len(times) >= 1:
        ACQ_SAT_MIN = 4
        ACQ_CN0_MIN = 30.0
        stable_mask = (sv_count_per_epoch >= ACQ_SAT_MIN) & (cn0_mean_per_epoch >= ACQ_CN0_MIN)
        if stable_mask.any():
            first_stable_idx = int(np.argmax(stable_mask))
            t0_ns = times[0].astype("datetime64[ns]").astype("int64")
            ts_ns = times[first_stable_idx].astype("datetime64[ns]").astype("int64")
            acq_time_sec = float((ts_ns - t0_ns) / 1e9)
        else:
            acq_time_sec = None  # never stabilized — extreme case

    # ---- cycle slips: count LLI bit 0 set across all phase observations ----
    lli_vars = [v for v in ds.data_vars if v.endswith("lli")]
    cycle_slips = 0
    for v in lli_vars:
        # Only count slips on phase observations (L* not C*/D*/S*)
        base = v[:-3]  # strip 'lli'
        if not base.startswith("L"):
            continue
        arr = ds[v].values
        # LLI is stored as float in xarray; values are 0..7 or NaN
        flat = arr[~np.isnan(arr)]
        if flat.size:
            cycle_slips += int(((flat.astype(int) & 1) == 1).sum())

    # ---- dual/triple frequency ratios (per-epoch presence of L1, L2, L5-equiv bands) ----
    band_present = {"L1": np.zeros(len(times), dtype=bool),
                    "L2": np.zeros(len(times), dtype=bool),
                    "L5": np.zeros(len(times), dtype=bool)}
    for v in ds.data_vars:
        if v.endswith("lli") or v.endswith("ssi"):
            continue
        band = _band_of_obs_code(v)
        if band is None or band not in band_present:
            continue
        arr = ds[v].values
        # Any non-NaN for this epoch (across any SV) means band is present
        band_present[band] |= (~np.isnan(arr)).any(axis=1)
    epochs_total = len(times)
    if epochs_total > 0:
        dual_ratio = float((band_present["L1"] & band_present["L2"]).sum() / epochs_total)
        triple_ratio = float((band_present["L1"] & band_present["L2"] & band_present["L5"]).sum() / epochs_total)
    else:
        dual_ratio = 0.0
        triple_ratio = 0.0

    # ---- gap analysis ----
    gap_gt_5s = 0
    any_gap_gt_60s = False
    intervals_sec = np.array([])
    if len(times) >= 2:
        dt_sec = np.diff(times) / np.timedelta64(1, "s")
        intervals_sec = dt_sec[dt_sec > 0]
        gap_gt_5s = int((intervals_sec > 5.0).sum())
        any_gap_gt_60s = bool((intervals_sec > 60.0).any())

    sampling_rate_hz = None
    if intervals_sec.size:
        median_int = float(np.median(intervals_sec))
        if median_int > 0:
            sampling_rate_hz = 1.0 / median_int

    duration_sec = float((times[-1] - times[0]) / np.timedelta64(1, "s")) if len(times) >= 2 else 0.0

    return {
        "first_epoch_utc": _iso_from_np_datetime(times[0]) if len(times) else None,
        "last_epoch_utc": _iso_from_np_datetime(times[-1]) if len(times) else None,
        "duration_sec": duration_sec,
        "epochs_total": epochs_total,
        "sampling_rate_hz": sampling_rate_hz,
        "cn0_mean": cn0_mean,
        "cn0_min": cn0_min,
        "cn0_count_obs": cn0_count,
        "sat_count_mean": sat_count_mean,
        "sat_count_min": sat_count_min,
        "cycle_slips": cycle_slips,
        "dual_freq_ratio": dual_ratio,
        "triple_freq_ratio": triple_ratio,
        "constellations_seen": constellations_seen,
        "gap_gt_5s_count": gap_gt_5s,
        "any_gap_gt_60s": any_gap_gt_60s,
        "rover_acquisition_time_sec": acq_time_sec,
    }


def parse(config: dict, project_root: Path) -> dict:
    rinex_folder = resolve_path(project_root, config["inputs"]["rinex_folder"])
    classified = classify_rinex_files(rinex_folder)

    obs_path = classified["primary_obs"]
    nav_path = classified["primary_nav"]

    warns: list[str] = []
    if obs_path is None:
        raise FileNotFoundError("no RINEX observation file found")
    if len(classified["all_obs"]) > 1:
        warns.append(
            f"multiple observation files present, using {obs_path.name} "
            f"(skipping: {[p.name for p in classified['all_obs'] if p != obs_path]})"
        )
    if nav_path is None:
        warns.append("no RINEX navigation file found — PPK can still run if base+rover nav is sourced elsewhere")
    elif len(classified["all_nav"]) > 1:
        warns.append(
            f"multiple navigation files present, using {nav_path.name} "
            f"(skipping: {[p.name for p in classified['all_nav'] if p != nav_path]})"
        )

    # Header via georinex (instant)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        hdr = gr.rinexheader(str(obs_path))

    receiver_raw = str(hdr.get("REC # / TYPE / VERS", "") or "").strip()
    antenna_raw = str(hdr.get("ANT # / TYPE", "") or "").strip()
    inference = _infer_from_comments(hdr)
    override = _load_hardware_override(config, project_root)

    receiver_resolved = _resolve_hardware(
        receiver_raw, override["receiver_type"], inference["receiver_inferred"], "receiver"
    )
    antenna_resolved = _resolve_hardware(
        antenna_raw, override["antenna_type"], inference["antenna_inferred"], "antenna"
    )

    # Body stats via georinex (slow ~3-4 min)
    body = _compute_body_stats(obs_path)

    duration = body["duration_sec"]
    epochs_per_sec = body["sampling_rate_hz"]
    if epochs_per_sec is None:
        hdr_interval = hdr.get("interval")
        if hdr_interval:
            epochs_per_sec = 1.0 / float(hdr_interval)

    fields = {
        "L1F_GNSS_001": body["first_epoch_utc"],
        "L1F_GNSS_002": body["last_epoch_utc"],
        "L1F_GNSS_003": round(duration, 4) if duration is not None else None,
        "L1F_GNSS_004": None,  # pre_buffer_sec — joined with BIN at Step 6 merge
        "L1F_GNSS_005": None,  # post_buffer_sec — same
        "L1F_GNSS_006": body["epochs_total"],
        "L1F_GNSS_007": round(epochs_per_sec, 4) if epochs_per_sec is not None else None,
        "L1F_GNSS_008": round(body["cn0_mean"], 4) if body["cn0_mean"] is not None else None,
        "L1F_GNSS_009": round(body["cn0_min"], 4) if body["cn0_min"] is not None else None,
        "L1F_GNSS_010": round(body["sat_count_mean"], 4) if body["sat_count_mean"] is not None else None,
        "L1F_GNSS_011": body["sat_count_min"],
        "L1F_GNSS_012": body["cycle_slips"],
        "L1F_GNSS_013": round(body["dual_freq_ratio"], 4),
        "L1F_GNSS_014": round(body["triple_freq_ratio"], 4),
        "L1F_GNSS_015": len(body["constellations_seen"]),
        "L1F_GNSS_016": body["gap_gt_5s_count"],
        "L1F_GNSS_017": body["any_gap_gt_60s"],
        "L1F_GNSS_018": receiver_resolved["value"],
        "L1F_GNSS_019": antenna_resolved["value"],
    }

    parser_meta = {
        "parser": "parse_rinex",
        "engine": "georinex",
        "engine_version": getattr(gr, "__version__", "unknown"),
        "primary_obs_file": obs_path.name,
        "primary_nav_file": nav_path.name if nav_path else None,
        "rinex_version": hdr.get("version"),
        "rinex_type": hdr.get("rinex_type") or hdr.get("filetype"),
        "system_marker": hdr.get("systems"),
        "approx_position_xyz": hdr.get("position"),
        "constellation_codes_in_header": sorted(hdr.get("fields", {}).keys()) if isinstance(hdr.get("fields"), dict) else None,
        "constellations_seen_in_body": body["constellations_seen"],
        "cn0_count_obs": body["cn0_count_obs"],
        "rover_acquisition_time_sec": body["rover_acquisition_time_sec"],
        "receiver_type_source": receiver_resolved["source"],
        "antenna_type_source": antenna_resolved["source"],
        "receiver_type_rinex_header": receiver_raw or None,
        "antenna_type_rinex_header": antenna_raw or None,
        "receiver_type_user_override": override["receiver_type"],
        "antenna_type_user_override": override["antenna_type"],
        "receiver_type_inferred": inference["receiver_inferred"],
        "antenna_type_inferred": inference["antenna_inferred"],
        "comment_inference_source": inference["source"],
        "hardware_override_file": override["file_path"],
        "hardware_override_present": override["present"],
        "warnings": warns,
    }

    return {
        "fields": fields,
        "parser_meta": parser_meta,
        "flags_raised": [],
    }


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: parse_rinex.py <paths.json>", file=sys.stderr)
        return 2
    config_path = Path(sys.argv[1]).resolve()
    project_root = config_path.parent
    config = json.loads(config_path.read_text())

    print("[parse_rinex] loading with georinex (typically 3-4 min on a 25MB 5Hz multi-GNSS file)...", flush=True)
    result = parse(config, project_root)
    fields = result["fields"]
    meta = result["parser_meta"]

    if "--full" in sys.argv:
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return 0

    print(f"parse_rinex: primary obs = {meta['primary_obs_file']}, nav = {meta['primary_nav_file']}")
    print(f"  engine: {meta['engine']} v{meta['engine_version']}")
    print(f"  RINEX v{meta['rinex_version']} ({meta['rinex_type']}, marker={meta['system_marker']})")
    print(f"  constellations seen: {meta['constellations_seen_in_body']}")
    print()
    print(f"  L1F_GNSS_001 obs_start_utc       = {fields['L1F_GNSS_001']}")
    print(f"  L1F_GNSS_002 obs_end_utc         = {fields['L1F_GNSS_002']}")
    print(f"  L1F_GNSS_003 obs_duration_sec    = {fields['L1F_GNSS_003']}")
    print(f"  L1F_GNSS_004 pre_buffer_sec      = {fields['L1F_GNSS_004']} (filled at Step 6 merge)")
    print(f"  L1F_GNSS_005 post_buffer_sec     = {fields['L1F_GNSS_005']} (filled at Step 6 merge)")
    print(f"  L1F_GNSS_006 epochs_total        = {fields['L1F_GNSS_006']}")
    print(f"  L1F_GNSS_007 epochs_per_second   = {fields['L1F_GNSS_007']}")
    print(f"  L1F_GNSS_008 cn0_mean_dbhz       = {fields['L1F_GNSS_008']}")
    print(f"  L1F_GNSS_009 cn0_min_dbhz        = {fields['L1F_GNSS_009']}")
    print(f"  L1F_GNSS_010 sat_count_mean      = {fields['L1F_GNSS_010']}")
    print(f"  L1F_GNSS_011 sat_count_min       = {fields['L1F_GNSS_011']}")
    print(f"  L1F_GNSS_012 cycle_slip_count    = {fields['L1F_GNSS_012']}")
    print(f"  L1F_GNSS_013 dual_freq_ratio     = {fields['L1F_GNSS_013']}")
    print(f"  L1F_GNSS_014 triple_freq_ratio   = {fields['L1F_GNSS_014']}")
    print(f"  L1F_GNSS_015 constellation_count = {fields['L1F_GNSS_015']}")
    print(f"  L1F_GNSS_016 gap_gt_5s_count     = {fields['L1F_GNSS_016']}")
    print(f"  L1F_GNSS_017 any_gap_gt_60s      = {fields['L1F_GNSS_017']}")
    print(f"  L1F_GNSS_018 receiver_type       = {fields['L1F_GNSS_018']!r}  (source: {meta['receiver_type_source']})")
    print(f"                  rinex_header: {meta['receiver_type_rinex_header']!r}  user_override: {meta['receiver_type_user_override']!r}  comment_inferred: {meta['receiver_type_inferred']!r}")
    print(f"  L1F_GNSS_019 antenna_type        = {fields['L1F_GNSS_019']!r}  (source: {meta['antenna_type_source']})")
    print(f"                  rinex_header: {meta['antenna_type_rinex_header']!r}  user_override: {meta['antenna_type_user_override']!r}  comment_inferred: {meta['antenna_type_inferred']!r}")
    print()
    if meta["warnings"]:
        print(f"  warnings ({len(meta['warnings'])}):")
        for w in meta["warnings"]:
            print(f"    - {w}")
    print(f"  cn0 observations counted: {meta['cn0_count_obs']:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
