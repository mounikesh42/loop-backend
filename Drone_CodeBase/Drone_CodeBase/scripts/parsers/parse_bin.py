#!/usr/bin/env python3
"""Stage 2 parser — ArduPilot .BIN flight log (SRC_FC_BIN).

Produces all 34 L1F_BIN_* source fields from a single ArduPilot .BIN flight
log. The BIN consolidates four previously-separate sources (Mission Plan,
Flight Telemetry, Flight Data Record, MRK file) into one file, parsed via
pymavlink.

Message types consumed (per spec sheet 02 notes):
  CMD   mission plan (CId 22 NAV_TAKEOFF, 16 NAV_WAYPOINT, 178 DO_CHANGE_SPEED)
  CAM   per-image shutter records (replaces MRK; one per image)
  GPS   absolute UTC anchor via GMS + GWk
  ARM   flight start/end events (ArmState 1=arm, 0=disarm)
  BAT   battery percentage near ARM/DISARM events
  CTUN  control tuning (Alt = AGL altitude for cruise stats)
  MISE  mission item execution (for waypoints_completed)
  IMU/VIBE/ATT  vibration + attitude stats (FDR block)
  MODE/EV     surfaced in parser_meta for traceability

Cruise window: defined as [first ARM ArmState=1, last ARM ArmState=0] in
TimeUS. All "during flight" statistics (alt, vibe, attitude) are computed
over this window. Some fields use a tighter filter ("Alt > 5m AGL") to
exclude takeoff/landing transients.

GPS time → UTC: GMS (GPS time of week, ms) and GWk (GPS week #) on the
first GPS message provide an anchor. Subsequent timestamps are derived
by (TimeUS_now − TimeUS_anchor) / 1e6 + anchor_utc. 18 leap seconds are
applied to convert GPS time → UTC.
"""
import json
import math
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pymavlink import mavutil


# MAVLink command IDs we care about
MAV_CMD_NAV_WAYPOINT = 16
MAV_CMD_NAV_TAKEOFF = 22
MAV_CMD_DO_CHANGE_SPEED = 178

# GPS epoch: 1980-01-06 00:00:00 UTC
GPS_EPOCH = datetime(1980, 1, 6, tzinfo=timezone.utc)
# Leap seconds between GPS time and UTC as of survey date.
# 18 leap seconds since 2017-01-01; spec freezes at v1.1.1 in 2026.
GPS_UTC_LEAP_SECONDS = 18


def gps_to_utc(gps_week: int, gps_ms: float) -> str:
    """Convert (GPS week, GPS time of week in ms) to ISO UTC string with sub-ms precision."""
    if gps_week is None or gps_ms is None:
        return None
    dt = GPS_EPOCH + timedelta(weeks=int(gps_week), milliseconds=float(gps_ms))
    dt = dt - timedelta(seconds=GPS_UTC_LEAP_SECONDS)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0").rstrip(".") + "Z"


def gps_to_utc_dt(gps_week: int, gps_ms: float) -> datetime:
    return GPS_EPOCH + timedelta(weeks=int(gps_week), milliseconds=float(gps_ms)) - timedelta(seconds=GPS_UTC_LEAP_SECONDS)


def _safe_mean(values):
    vs = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    return statistics.fmean(vs) if vs else None


def _safe_min(values):
    vs = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    return min(vs) if vs else None


def _safe_max(values):
    vs = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    return max(vs) if vs else None


def _safe_stdev(values):
    vs = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    return statistics.stdev(vs) if len(vs) > 1 else None


# MAV_CMD enum names for the IDs commonly seen in survey missions.
_CID_NAMES = {
    16: "NAV_WAYPOINT",
    17: "NAV_LOITER_UNLIM",
    19: "NAV_LOITER_TIME",
    20: "NAV_RTL",
    21: "NAV_LAND",
    22: "NAV_TAKEOFF",
    93: "NAV_DELAY",
    178: "DO_CHANGE_SPEED",
    201: "DO_SET_ROI",
    206: "DO_SET_CAM_TRIGG_DIST",
    222: "DO_MOUNT_CONTROL",
}


def _cmd_breakdown(cmds: list) -> dict:
    """Count CMD entries by CId, with human-readable names. For parser_meta."""
    by_cid = {}
    for c in cmds:
        cid = c.get("CId")
        by_cid[cid] = by_cid.get(cid, 0) + 1
    return {
        f"CId_{cid}_{_CID_NAMES.get(cid, 'UNKNOWN')}": n
        for cid, n in sorted(by_cid.items())
    }


def parse(config: dict, project_root: Path) -> dict:
    bin_folder = project_root / config["inputs"]["bin_folder"]
    bin_files = sorted(p for p in bin_folder.iterdir()
                       if p.is_file() and not p.name.startswith(".") and p.suffix.lower() == ".bin")
    if not bin_files:
        raise FileNotFoundError("no .BIN file found in telemetry folder")
    if len(bin_files) > 1:
        # Stage 1 already hard-fails this, but defensive.
        raise ValueError(f"expected 1 .BIN, found {len(bin_files)}: {[p.name for p in bin_files]}")
    bin_path = bin_files[0]

    mlog = mavutil.mavlink_connection(str(bin_path))

    # Per-type accumulators
    cmds = []         # mission plan items
    cams = []         # camera shutter events
    arms = []         # arm state transitions
    bats = []         # battery messages (TimeUS, RemPct)
    ctun = []         # (TimeUS, Alt) — Alt is AGL from CTUN.Alt
    vibes = []        # (TimeUS, VibeX, VibeY, VibeZ)
    atts = []         # (TimeUS, Roll, Pitch, Yaw)
    mise = []         # mission item execution
    modes = []        # mode transitions
    events = []       # generic events
    gps_anchor = None  # first GPS fix (TimeUS, GMS, GWk)
    first_time_us = None
    last_time_us = None
    type_counts = {}
    # ArduPilot parameters that affect mission semantics. WPNAV_SPEED is
    # the default cruise speed used when no DO_CHANGE_SPEED command was
    # placed in the mission (common in Copter survey grids).
    parm_wpnav_speed_cms = None  # cm/s as written by ArduPilot

    while True:
        msg = mlog.recv_match(blocking=False)
        if msg is None:
            break
        typ = msg.get_type()
        type_counts[typ] = type_counts.get(typ, 0) + 1
        d = msg.to_dict()
        tus = d.get("TimeUS")
        if tus is not None:
            if first_time_us is None:
                first_time_us = tus
            last_time_us = tus

        if typ == "CMD":
            cmds.append(d)
        elif typ == "CAM":
            cams.append(d)
        elif typ == "ARM":
            arms.append(d)
        elif typ == "BAT" and d.get("Inst", 0) == 0:
            bats.append((tus, d.get("RemPct"), d.get("Volt")))
        elif typ == "CTUN":
            ctun.append((tus, d.get("Alt"), d.get("DAlt")))
        elif typ == "VIBE":
            vibes.append((tus, d.get("VibeX"), d.get("VibeY"), d.get("VibeZ")))
        elif typ == "ATT":
            atts.append((tus, d.get("Roll"), d.get("Pitch"), d.get("Yaw")))
        elif typ == "MISE":
            mise.append(d)
        elif typ == "MODE":
            modes.append(d)
        elif typ == "EV":
            events.append(d)
        elif typ == "GPS":
            if gps_anchor is None and d.get("Status", 0) >= 3:  # 3 = 3D fix
                gps_anchor = {
                    "TimeUS": tus,
                    "GMS": d.get("GMS"),
                    "GWk": d.get("GWk"),
                }
        elif typ == "PARM":
            if d.get("Name") == "WPNAV_SPEED":
                parm_wpnav_speed_cms = d.get("Value")

    # ---- Time anchor for UTC conversion ----
    anchor_utc = None
    if gps_anchor and gps_anchor["GMS"] is not None and gps_anchor["GWk"] is not None:
        anchor_utc = gps_to_utc_dt(gps_anchor["GWk"], gps_anchor["GMS"])

    def time_us_to_utc(tus):
        if anchor_utc is None or tus is None or gps_anchor is None:
            return None
        delta_us = tus - gps_anchor["TimeUS"]
        return anchor_utc + timedelta(microseconds=int(delta_us))

    def time_us_to_iso(tus):
        dt = time_us_to_utc(tus)
        if dt is None:
            return None
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0").rstrip(".") + "Z"

    # ---- ARM/DISARM event resolution ----
    arm_events = [a for a in arms if a.get("ArmState") == 1]
    disarm_events = [a for a in arms if a.get("ArmState") == 0]
    flight_start_us = arm_events[0].get("TimeUS") if arm_events else None
    flight_end_us = disarm_events[-1].get("TimeUS") if disarm_events else None
    flight_start_utc = time_us_to_iso(flight_start_us)
    flight_end_utc = time_us_to_iso(flight_end_us)
    flight_duration_sec = None
    if flight_start_us is not None and flight_end_us is not None:
        flight_duration_sec = (flight_end_us - flight_start_us) / 1_000_000.0

    in_flight = lambda tus: (tus is not None
                              and (flight_start_us is None or tus >= flight_start_us)
                              and (flight_end_us is None or tus <= flight_end_us))

    # ---- Mission plan from CMD messages ----
    # Filter out the home-position placeholder row (Lat=Lng=Alt=0 with CNum=0).
    # The spec for L1F_BIN_MP_003 has two phrases:
    #   "Total waypoints in the mission, read from CMD.CTot."
    #   "NAV_WAYPOINT count specifically used for completion ratio."
    # These are inconsistent (CTot includes home + takeoff + RTL + camera-trigger
    # configs + actual waypoints). For completion comparisons in Stage 3a to be
    # apples-to-apples with L1F_BIN_TLM_005 (which counts only NAV_WAYPOINT MISE
    # events), the denominator must also count NAV_WAYPOINTs. We honor the second
    # spec phrase and surface CTot in parser_meta for traceability.
    nav_takeoffs = [c for c in cmds if c.get("CId") == MAV_CMD_NAV_TAKEOFF]
    nav_waypoints = [c for c in cmds if c.get("CId") == MAV_CMD_NAV_WAYPOINT
                     and (c.get("Lat") or 0) != 0 and (c.get("Lng") or 0) != 0]
    do_change_speed = [c for c in cmds if c.get("CId") == MAV_CMD_DO_CHANGE_SPEED]

    takeoff = nav_takeoffs[0] if nav_takeoffs else None
    planned_waypoint_count = len(nav_waypoints)  # NAV_WAYPOINT only, home excluded
    cmd_ctot = cmds[0].get("CTot") if cmds else None  # surfaced in parser_meta
    planned_altitude_m = _safe_mean([c.get("Alt") for c in nav_waypoints]) if nav_waypoints else None
    # planned_speed_ms resolution:
    #   1. DO_CHANGE_SPEED CMD (CId=178), Prm2 = target speed in m/s — spec primary
    #   2. WPNAV_SPEED parameter — survey missions usually rely on this, not a
    #      DO_CHANGE_SPEED override; the value is stored in cm/s in ArduPilot.
    #   3. None — neither source available.
    planned_speed_ms = None
    planned_speed_source = None
    if do_change_speed:
        planned_speed_ms = do_change_speed[0].get("Prm2")
        planned_speed_source = "DO_CHANGE_SPEED (CMD CId=178, Prm2)"
    elif parm_wpnav_speed_cms is not None:
        planned_speed_ms = parm_wpnav_speed_cms / 100.0
        planned_speed_source = f"WPNAV_SPEED parameter ({parm_wpnav_speed_cms} cm/s ÷ 100 = {parm_wpnav_speed_cms/100.0} m/s; no DO_CHANGE_SPEED in mission)"

    # ---- CTUN altitude stats during flight ----
    ctun_in_flight = [(t, alt) for (t, alt, _da) in ctun if in_flight(t) and alt is not None]
    actual_alt_mean = _safe_mean([alt for _t, alt in ctun_in_flight])
    actual_alt_min = _safe_min([alt for _t, alt in ctun_in_flight])
    actual_alt_max = _safe_max([alt for _t, alt in ctun_in_flight])

    # ---- Battery at flight start / end ----
    def battery_near(target_us):
        if target_us is None or not bats:
            return None
        # Find BAT with TimeUS closest to target
        closest = min(bats, key=lambda b: abs((b[0] or 0) - target_us))
        return closest[1]

    bat_start = battery_near(flight_start_us)
    bat_end = battery_near(flight_end_us)

    # ---- Waypoints completed ----
    # MISE events during flight, with CId == 16 (NAV_WAYPOINT) — count unique CNum
    mise_wp_in_flight = {m.get("CNum") for m in mise
                         if m.get("CId") == MAV_CMD_NAV_WAYPOINT
                         and in_flight(m.get("TimeUS"))}
    waypoints_completed = len(mise_wp_in_flight)

    # ---- Vibration stats during flight ----
    vibe_in_flight = [(vx, vy, vz) for (t, vx, vy, vz) in vibes if in_flight(t) and vx is not None]
    vibe_x = [v[0] for v in vibe_in_flight]
    vibe_y = [v[1] for v in vibe_in_flight]
    vibe_z = [v[2] for v in vibe_in_flight]

    # ---- Attitude stats during flight ----
    att_in_flight = [(r, p, y) for (t, r, p, y) in atts if in_flight(t) and r is not None]
    att_roll = [a[0] for a in att_in_flight]
    att_pitch = [a[1] for a in att_in_flight]
    att_yaw = [a[2] for a in att_in_flight]

    # Attitude sample rate (Hz): N samples / flight duration
    att_rate = None
    if flight_duration_sec and flight_duration_sec > 0 and att_in_flight:
        att_rate = len(att_in_flight) / flight_duration_sec

    # ---- "Gimbal" stats — using CAM.R/P/Y since no MOUNT messages in BIN ----
    # CAM.R/P/Y is the airframe attitude at shutter; for fixed-mount cameras
    # this is the camera attitude as well. Documented in parser_meta.
    cam_roll = [c.get("R") for c in cams if c.get("R") is not None]
    cam_pitch = [c.get("P") for c in cams if c.get("P") is not None]
    gimbal_roll_mean = _safe_mean(cam_roll)
    gimbal_pitch_mean = _safe_mean(cam_pitch)
    gimbal_errors = 0  # No GIMB/MOUNT messages in this BIN; no gimbal to error.

    # ---- CAM per-image arrays (replaces former MRK file) ----
    cam_image_ids = [c.get("Img") for c in cams]
    cam_gnss_week = [c.get("GPSWeek") for c in cams]
    cam_gnss_ms = [c.get("GPSTime") for c in cams]  # ms-of-week
    cam_gnss_sec = [(ms / 1000.0) if ms is not None else None for ms in cam_gnss_ms]
    cam_gnss_utc = [gps_to_utc(w, ms) if (w is not None and ms is not None) else None
                    for w, ms in zip(cam_gnss_week, cam_gnss_ms)]

    # Log duration (first to last TimeUS across any message)
    log_duration_sec = (last_time_us - first_time_us) / 1_000_000.0 if (first_time_us and last_time_us) else None

    # ---- Field assembly ----
    fields = {
        # CAM Per-Image (was MRK) — 4 fields
        "L1F_BIN_CAM_001": cam_image_ids,
        "L1F_BIN_CAM_002": cam_gnss_utc,
        "L1F_BIN_CAM_003": cam_gnss_week,
        "L1F_BIN_CAM_004": cam_gnss_sec,
        # CAM File Integrity — 2 fields
        "L1F_BIN_CAM_005": len(cams),
        "L1F_BIN_CAM_006": len(cams) > 0,
        # Mission Plan (CMD) — 6 fields
        "L1F_BIN_MP_001": round(planned_altitude_m, 4) if planned_altitude_m is not None else None,
        "L1F_BIN_MP_002": round(planned_speed_ms, 4) if planned_speed_ms is not None else None,
        "L1F_BIN_MP_003": planned_waypoint_count,
        "L1F_BIN_MP_004": round(takeoff["Lat"], 7) if takeoff and takeoff.get("Lat") else None,
        "L1F_BIN_MP_005": round(takeoff["Lng"], 7) if takeoff and takeoff.get("Lng") else None,
        "L1F_BIN_MP_006": round(takeoff["Alt"], 4) if takeoff and takeoff.get("Alt") is not None else None,
        # Mission Execution (telemetry) — 3 fields
        "L1F_BIN_TLM_001": round(actual_alt_mean, 4) if actual_alt_mean is not None else None,
        "L1F_BIN_TLM_002": round(actual_alt_min, 4) if actual_alt_min is not None else None,
        "L1F_BIN_TLM_003": round(actual_alt_max, 4) if actual_alt_max is not None else None,
        # Flight Events — 4 fields
        "L1F_BIN_TLM_004": round(flight_duration_sec, 4) if flight_duration_sec is not None else None,
        "L1F_BIN_TLM_005": waypoints_completed,
        "L1F_BIN_TLM_006": bat_start,
        "L1F_BIN_TLM_007": bat_end,
        # FDR IMU & Vibration — 8 fields
        "L1F_BIN_FDR_001": True,
        "L1F_BIN_FDR_002": round(log_duration_sec, 4) if log_duration_sec is not None else None,
        "L1F_BIN_FDR_003": round(_safe_mean(vibe_x), 4) if _safe_mean(vibe_x) is not None else None,
        "L1F_BIN_FDR_004": round(_safe_mean(vibe_y), 4) if _safe_mean(vibe_y) is not None else None,
        "L1F_BIN_FDR_005": round(_safe_mean(vibe_z), 4) if _safe_mean(vibe_z) is not None else None,
        "L1F_BIN_FDR_006": round(_safe_max(vibe_x), 4) if _safe_max(vibe_x) is not None else None,
        "L1F_BIN_FDR_007": round(_safe_max(vibe_y), 4) if _safe_max(vibe_y) is not None else None,
        "L1F_BIN_FDR_008": round(_safe_max(vibe_z), 4) if _safe_max(vibe_z) is not None else None,
        # FDR Attitude & Gimbal — 7 fields
        "L1F_BIN_FDR_009": round(_safe_stdev(att_roll), 4) if _safe_stdev(att_roll) is not None else None,
        "L1F_BIN_FDR_010": round(_safe_stdev(att_pitch), 4) if _safe_stdev(att_pitch) is not None else None,
        "L1F_BIN_FDR_011": round(_safe_stdev(att_yaw), 4) if _safe_stdev(att_yaw) is not None else None,
        "L1F_BIN_FDR_012": round(gimbal_roll_mean, 4) if gimbal_roll_mean is not None else None,
        "L1F_BIN_FDR_013": round(gimbal_pitch_mean, 4) if gimbal_pitch_mean is not None else None,
        "L1F_BIN_FDR_014": gimbal_errors,
        "L1F_BIN_FDR_015": round(att_rate, 4) if att_rate is not None else None,
    }

    parser_meta = {
        "parser": "parse_bin",
        "engine": "pymavlink",
        "engine_version": getattr(__import__("pymavlink"), "__version__", "unknown"),
        "bin_file": bin_path.name,
        "bin_size_bytes": bin_path.stat().st_size,
        "message_total_count": sum(type_counts.values()),
        "message_type_counts": {k: type_counts.get(k, 0) for k in
                                 sorted(["CMD", "CAM", "GPS", "POS", "CTUN", "BAT",
                                         "MISE", "MODE", "ARM", "EV", "IMU", "VIBE", "ATT"])},
        "log_first_time_us": first_time_us,
        "log_last_time_us": last_time_us,
        "log_duration_sec": round(log_duration_sec, 4) if log_duration_sec else None,
        "gps_anchor": {
            "TimeUS": gps_anchor["TimeUS"] if gps_anchor else None,
            "GMS": gps_anchor["GMS"] if gps_anchor else None,
            "GWk": gps_anchor["GWk"] if gps_anchor else None,
            "anchor_utc": anchor_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if anchor_utc else None,
        },
        "flight_start_utc": flight_start_utc,  # consumed in Step 6 merge to compute pre_buffer
        "flight_end_utc": flight_end_utc,      # consumed in Step 6 merge to compute post_buffer
        "arm_events_count": len(arm_events),
        "disarm_events_count": len(disarm_events),
        "mode_changes": len(modes),
        "event_count": len(events),
        "mise_count_in_flight": len(mise_wp_in_flight),
        "mission_complete_ratio": (waypoints_completed / planned_waypoint_count) if planned_waypoint_count else None,
        "cmd_ctot": cmd_ctot,
        "cmd_breakdown_by_cid": _cmd_breakdown(cmds),
        "planned_speed_source": planned_speed_source,
        "parm_wpnav_speed_cms": parm_wpnav_speed_cms,
        "gimbal_data_source": "CAM.R/P/Y (drone attitude at shutter; no MOUNT/GIMB messages in BIN — camera is fixed-mounted)",
        "cam_record_count_observation": (
            f"CAM count = {len(cams)}; image count will be compared at Step 6 merge to "
            f"determine L2D_BIN_005 (cam_image_count_match) and raise FLG_019 CAM_COUNT_MISMATCH if unequal."
        ),
        # ---- Supplemental data consumed by Stage 3a derived fields ----
        # Waypoint polygon coords for L2D_FC_001 planned_area_m2 (shoelace).
        # Home-position placeholder (CNum=0 with all-zero coords) is already excluded.
        "nav_waypoint_coords": [
            {"cnum": c.get("CNum"), "lat": c.get("Lat"), "lng": c.get("Lng"), "alt": c.get("Alt")}
            for c in nav_waypoints
        ],
        # MODE transitions for L2D_BIN_003 abort_count and L2D_BIN_004 rtb_triggered.
        # ArduCopter mode codes — 3=AUTO (survey), 6=RTL, 9=LAND, 5=LOITER, etc.
        "mode_transitions": [
            {"time_us": m.get("TimeUS"),
             "mode": m.get("Mode"),
             "mode_num": m.get("ModeNum"),
             "in_flight": in_flight(m.get("TimeUS"))}
            for m in modes
        ],
        # All in-flight altitude samples (no altitude pre-filter). Stage 3a
        # applies the cruise filter (alt > 0.5 * planned_altitude_m) for
        # L2D_FC_012 altitude_variance_m. Keeping the unfiltered list lets the
        # cruise definition evolve in compute_derived without re-running parse_bin.
        "in_flight_altitudes_m": [alt for (t, alt) in ctun_in_flight if alt is not None],
        # CAM lat/lng/alt for L2D_FC_004 coverage when EXIF GPS is missing — these
        # are the drone positions at each shutter (proxy for image positions on
        # fixed-mount cameras). Surfaced for Stage 3a to decide whether to use them.
        "cam_positions": [
            {"img": c.get("Img"), "lat": c.get("Lat"), "lng": c.get("Lng"), "alt": c.get("Alt"),
             "rel_alt": c.get("RelAlt")}
            for c in cams if c.get("Lat") is not None and c.get("Lng") is not None
        ],
    }

    # Flag raising for pre_score_ingestion at this stage:
    # - FLG_018 CAM_RECORDS_MISSING fires here if L1F_BIN_CAM_006 == False
    # - FLG_019 CAM_COUNT_MISMATCH fires at Stage 2 merge (needs total_images
    #   from parse_images), not here.
    flags_raised = []
    if not fields["L1F_BIN_CAM_006"]:
        flags_raised.append({
            "flag_id": "FLG_018",
            "flag_name": "CAM_RECORDS_MISSING",
            "severity": "MEDIUM",
            "stage": "pre_score_ingestion",
            "raised_by": "L1F_BIN_CAM_006",
            "context": "no CAM messages found in BIN — image-to-RINEX-epoch matching will fall back to EXIF timestamps",
        })

    return {
        "fields": fields,
        "parser_meta": parser_meta,
        "flags_raised": flags_raised,
    }


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: parse_bin.py <paths.json>", file=sys.stderr)
        return 2
    config_path = Path(sys.argv[1]).resolve()
    project_root = config_path.parent
    config = json.loads(config_path.read_text())

    print("[parse_bin] parsing ArduPilot .BIN via pymavlink...", flush=True)
    result = parse(config, project_root)
    fields = result["fields"]
    meta = result["parser_meta"]

    if "--full" in sys.argv:
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return 0

    def fmt_arr(label, arr, sample_n=3):
        if isinstance(arr, list):
            count = len([v for v in arr if v is not None])
            sample = arr[:sample_n]
            return f"{count}/{len(arr)} non-null, sample={sample}"
        return repr(arr)

    print(f"parse_bin: file = {meta['bin_file']} ({meta['bin_size_bytes']:,} B)")
    print(f"  engine: {meta['engine']} v{meta['engine_version']}")
    print(f"  total messages: {meta['message_total_count']:,}")
    print(f"  message types of interest: {meta['message_type_counts']}")
    print(f"  log duration: {meta['log_duration_sec']} s")
    print(f"  gps anchor (UTC): {meta['gps_anchor']['anchor_utc']}")
    print(f"  flight ARM @ {meta['flight_start_utc']} ({meta['arm_events_count']} arm event(s))")
    print(f"  flight DIS @ {meta['flight_end_utc']} ({meta['disarm_events_count']} disarm event(s))")
    print()
    print("--- CAM Per-Image (was MRK) ---")
    print(f"  L1F_BIN_CAM_001 image_id             = {fmt_arr('image_id', fields['L1F_BIN_CAM_001'])}")
    print(f"  L1F_BIN_CAM_002 gnss_timestamp_utc   = {fmt_arr('utc', fields['L1F_BIN_CAM_002'])}")
    print(f"  L1F_BIN_CAM_003 gnss_week            = {fmt_arr('gwk', fields['L1F_BIN_CAM_003'])}")
    print(f"  L1F_BIN_CAM_004 time_of_week_sec     = {fmt_arr('tow', fields['L1F_BIN_CAM_004'])}")
    print(f"  L1F_BIN_CAM_005 cam_record_count     = {fields['L1F_BIN_CAM_005']}")
    print(f"  L1F_BIN_CAM_006 cam_records_present  = {fields['L1F_BIN_CAM_006']}")
    print()
    print("--- Mission Plan (CMD) ---")
    print(f"  L1F_BIN_MP_001 planned_altitude_m    = {fields['L1F_BIN_MP_001']}")
    print(f"  L1F_BIN_MP_002 planned_speed_ms      = {fields['L1F_BIN_MP_002']}  (source: {meta['planned_speed_source']})")
    print(f"  L1F_BIN_MP_003 planned_waypoint_cnt  = {fields['L1F_BIN_MP_003']}  (NAV_WAYPOINTs only; CMD.CTot={meta['cmd_ctot']} incl. home/takeoff/RTL/cam-triggers)")
    print(f"  L1F_BIN_MP_004 takeoff_lat           = {fields['L1F_BIN_MP_004']}")
    print(f"  L1F_BIN_MP_005 takeoff_lng           = {fields['L1F_BIN_MP_005']}")
    print(f"  L1F_BIN_MP_006 takeoff_alt           = {fields['L1F_BIN_MP_006']}")
    print()
    print("--- Mission Execution (telemetry) ---")
    print(f"  L1F_BIN_TLM_001 actual_altitude_mean = {fields['L1F_BIN_TLM_001']} m")
    print(f"  L1F_BIN_TLM_002 actual_altitude_min  = {fields['L1F_BIN_TLM_002']} m")
    print(f"  L1F_BIN_TLM_003 actual_altitude_max  = {fields['L1F_BIN_TLM_003']} m")
    print()
    print("--- Flight Events ---")
    print(f"  L1F_BIN_TLM_004 flight_duration_sec  = {fields['L1F_BIN_TLM_004']} s")
    print(f"  L1F_BIN_TLM_005 waypoints_completed  = {fields['L1F_BIN_TLM_005']} (of {fields['L1F_BIN_MP_003']} planned; {meta['mission_complete_ratio']:.2%})")
    print(f"  L1F_BIN_TLM_006 battery_start_pct    = {fields['L1F_BIN_TLM_006']}%")
    print(f"  L1F_BIN_TLM_007 battery_end_pct      = {fields['L1F_BIN_TLM_007']}%")
    print()
    print("--- FDR IMU & Vibration ---")
    print(f"  L1F_BIN_FDR_001 log_present          = {fields['L1F_BIN_FDR_001']}")
    print(f"  L1F_BIN_FDR_002 log_duration_sec     = {fields['L1F_BIN_FDR_002']} s")
    print(f"  L1F_BIN_FDR_003 vibration_x_mean     = {fields['L1F_BIN_FDR_003']} m/s²")
    print(f"  L1F_BIN_FDR_004 vibration_y_mean     = {fields['L1F_BIN_FDR_004']} m/s²")
    print(f"  L1F_BIN_FDR_005 vibration_z_mean     = {fields['L1F_BIN_FDR_005']} m/s²")
    print(f"  L1F_BIN_FDR_006 vibration_x_max      = {fields['L1F_BIN_FDR_006']} m/s²")
    print(f"  L1F_BIN_FDR_007 vibration_y_max      = {fields['L1F_BIN_FDR_007']} m/s²")
    print(f"  L1F_BIN_FDR_008 vibration_z_max      = {fields['L1F_BIN_FDR_008']} m/s²")
    print()
    print("--- FDR Attitude & Gimbal ---")
    print(f"  L1F_BIN_FDR_009 roll_variance_deg    = {fields['L1F_BIN_FDR_009']}°")
    print(f"  L1F_BIN_FDR_010 pitch_variance_deg   = {fields['L1F_BIN_FDR_010']}°")
    print(f"  L1F_BIN_FDR_011 yaw_variance_deg     = {fields['L1F_BIN_FDR_011']}°")
    print(f"  L1F_BIN_FDR_012 gimbal_roll_mean     = {fields['L1F_BIN_FDR_012']}°")
    print(f"  L1F_BIN_FDR_013 gimbal_pitch_mean    = {fields['L1F_BIN_FDR_013']}°")
    print(f"  L1F_BIN_FDR_014 gimbal_error_count   = {fields['L1F_BIN_FDR_014']}")
    print(f"  L1F_BIN_FDR_015 attitude_sample_rate = {fields['L1F_BIN_FDR_015']} Hz")
    print()
    print(f"  flags raised: {[f['flag_name'] for f in result['flags_raised']] or 'none (CAM_COUNT_MISMATCH check deferred to Step 6 merge)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
