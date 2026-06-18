#!/usr/bin/env python3
"""parse_nav.py — RINEX 3.x broadcast NAV file parser.

Reads broadcast ephemeris records for GPS / Galileo / GLONASS / BeiDou / QZSS
and returns them grouped by satellite ID. Used by parse_rinex.py to compute
per-epoch PDOP from sat geometry (L1F_GCP_017).

Constellation physics only — carries no spec field IDs, so it is lifted
verbatim from the base-station build.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Satellites in these constellations use Keplerian broadcast (7 orbit lines).
KEPLERIAN_SYSTEMS = {"G", "E", "J", "C"}
# GLONASS and SBAS use a 3-orbit-line state-vector format.
STATE_VECTOR_SYSTEMS = {"R", "S"}


def _parse_d_float(s: str) -> float:
    """Parse a RINEX D/E exponent number. Returns 0.0 on failure."""
    if not s:
        return 0.0
    s = s.strip()
    if not s:
        return 0.0
    if "D" in s or "d" in s:
        s = s.replace("D", "E").replace("d", "E")
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_nav(nav_path: Path) -> dict[str, Any]:
    """Parse a RINEX 3.x NAV file.

    Returns:
      {
        "header": {"rinex_version": "3.03", "leap_seconds": 18, ...},
        "ephemerides": {
          "G18": [ {sat_id, toc (datetime), af0, af1, af2, orbit: [..28]}, ... ],
          "R03": [ {sat_id, toc, af0, af1, af2, orbit: [..12]}, ... ],
          ...
        }
      }
    """
    result: dict[str, Any] = {
        "header": {
            "rinex_version": None,
            "file_type": None,
            "system": None,
            "leap_seconds": None,
            "iono_corr": {},
            "time_system_corr": {},
        },
        "ephemerides": {},
    }
    in_body = False
    with nav_path.open("r", encoding="ascii", errors="replace") as fh:
        # Header
        for raw in fh:
            line = raw.rstrip("\r\n")
            label = line[60:].strip() if len(line) > 60 else ""
            content = line[:60] if len(line) >= 60 else line
            if label == "END OF HEADER":
                in_body = True
                break
            if label == "RINEX VERSION / TYPE":
                result["header"]["rinex_version"] = content[:9].strip()
                result["header"]["file_type"] = content[20:21].strip()
                result["header"]["system"] = content[40:41].strip()
            elif label == "LEAP SECONDS":
                try:
                    result["header"]["leap_seconds"] = int(content.split()[0])
                except (ValueError, IndexError):
                    pass
            elif label == "IONOSPHERIC CORR":
                key = content[0:4].strip()
                vals = [_parse_d_float(content[5 + i * 12 : 5 + (i + 1) * 12]) for i in range(4)]
                result["header"]["iono_corr"][key] = vals
            elif label == "TIME SYSTEM CORR":
                key = content[0:4].strip()
                result["header"]["time_system_corr"][key] = content[5:].strip()

        if not in_body:
            return result

        # Body
        while True:
            line = fh.readline()
            if not line:
                break
            line = line.rstrip("\r\n")
            if not line or len(line) < 4:
                continue
            sys_char = line[0]
            if sys_char not in (KEPLERIAN_SYSTEMS | STATE_VECTOR_SYSTEMS):
                continue
            sat_id = line[0:3]
            # First line layout (RINEX 3.x):
            #  cols  4- 7: year (4)
            #        9-10: month
            #       12-13: day
            #       15-16: hour
            #       18-19: minute
            #       21-22: second
            #       23-41: af0 (19 chars)
            #       42-60: af1
            #       61-79: af2
            try:
                year = int(line[4:8])
                month = int(line[9:11])
                day = int(line[12:14])
                hour = int(line[15:17])
                minute = int(line[18:20])
                second = int(line[21:23])
            except (ValueError, IndexError):
                continue
            af0 = _parse_d_float(line[23:42]) if len(line) >= 42 else 0.0
            af1 = _parse_d_float(line[42:61]) if len(line) >= 61 else 0.0
            af2 = _parse_d_float(line[61:80]) if len(line) >= 80 else 0.0
            try:
                toc = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
            except ValueError:
                continue

            n_orbit_lines = 3 if sys_char in STATE_VECTOR_SYSTEMS else 7
            orbit: list[float] = []
            for _ in range(n_orbit_lines):
                ol_raw = fh.readline()
                if not ol_raw:
                    break
                ol = ol_raw.rstrip("\r\n")
                # Four 19-char fields starting at col 4.
                for i in range(4):
                    start = 4 + i * 19
                    end = start + 19
                    if end > len(ol):
                        orbit.append(0.0)
                    else:
                        orbit.append(_parse_d_float(ol[start:end]))

            record = {
                "sat_id": sat_id,
                "toc": toc,
                "af0": af0,
                "af1": af1,
                "af2": af2,
                "orbit": orbit,
            }
            result["ephemerides"].setdefault(sat_id, []).append(record)

    # Sort ephemerides by toc for binary search later.
    for sat_id, recs in result["ephemerides"].items():
        recs.sort(key=lambda r: r["toc"])

    return result


# ---------------------------------------------------------------------------

def _cli(argv: list[str]) -> int:
    import json
    import sys

    if len(argv) != 2:
        print("usage: parse_nav.py <nav_path>", file=sys.stderr)
        return 2
    nav_path = Path(argv[1]).resolve()
    res = parse_nav(nav_path)
    summary = {
        "header": res["header"],
        "sats_with_ephemeris": len(res["ephemerides"]),
        "records_per_sat": {
            sat_id: len(recs) for sat_id, recs in sorted(res["ephemerides"].items())
        },
        "total_records": sum(len(recs) for recs in res["ephemerides"].values()),
    }
    json.dump(summary, sys.stdout, indent=2, sort_keys=True, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv))
