#!/usr/bin/env python3
"""gnss_orbits.py — Broadcast-ephemeris satellite position propagation.

Supports Keplerian propagation for GPS / Galileo / BeiDou (MEO+IGSO) / QZSS,
and linear PZ-90 propagation for GLONASS. SBAS is not propagated.

Used by parse_rinex.py to compute per-epoch PDOP (L1F_BASE_016).

Reference: ICD-GPS-200 (Algorithm 30 — User algorithm for ephemeris
determination), GLONASS ICD L1/L2.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

import numpy as np


# ---- constants --------------------------------------------------------------

# GPS / Galileo / QZSS / BeiDou-MEO Keplerian constants. Differences between
# constellations are negligible at PDOP precision.
GM = 3.986005e14                       # m^3/s^2
OMEGA_E_DOT = 7.2921151467e-5          # rad/s

# Time-frame offsets between each constellation's NAV record toc and GPS time.
# toc_in_gps_time = toc + offset.
# RINEX 3.x convention: G/E/J toc in system time (~GPS); C toc in BDT (GPS-14);
# R toc in UTC (GPS-18 as of 2026).
SYS_TOC_OFFSET_SEC = {"G": 0.0, "E": 0.0, "J": 0.0, "C": 14.0, "R": 18.0}

# Keplerian ephemeris validity window. RINEX broadcast is typically valid
# +/- 2 hours of toe; we permit slightly more to maximise sat coverage.
KEPLER_FIT_INTERVAL_SEC = 4 * 3600
GLONASS_FIT_INTERVAL_SEC = 30 * 60


# ---- ephemeris lookup ------------------------------------------------------

def closest_ephemeris(records: list[dict], epoch_dt_gps: datetime, sys_char: str) -> dict | None:
    """Return the ephemeris with toc closest (in GPS-time) to the epoch.

    None if no record falls within the fit interval.
    """
    if not records:
        return None
    offset = SYS_TOC_OFFSET_SEC.get(sys_char, 0.0)
    fit_window = GLONASS_FIT_INTERVAL_SEC if sys_char == "R" else KEPLER_FIT_INTERVAL_SEC
    best = None
    best_abs = None
    for rec in records:
        toc_gps = rec["toc"] + timedelta(seconds=offset)
        delta = abs((epoch_dt_gps - toc_gps).total_seconds())
        if best_abs is None or delta < best_abs:
            best_abs = delta
            best = rec
    if best_abs is None or best_abs > fit_window:
        return None
    return best


# ---- Keplerian propagation --------------------------------------------------

def propagate_keplerian(eph: dict, epoch_dt_gps: datetime, sys_char: str) -> tuple[float, float, float] | None:
    """Return ECEF (x, y, z) in metres for a GPS-style ephemeris at the given epoch.

    None if anything is malformed.
    """
    o = eph["orbit"]
    if len(o) < 17:
        return None
    try:
        Crs       = o[1]
        delta_n   = o[2]
        M0        = o[3]
        Cuc       = o[4]
        e         = o[5]
        Cus       = o[6]
        sqrtA     = o[7]
        Toe       = o[8]
        Cic       = o[9]
        Omega0    = o[10]
        Cis       = o[11]
        i0        = o[12]
        Crc       = o[13]
        omega     = o[14]
        OmegaDot  = o[15]
        IDOT      = o[16]
    except (IndexError, TypeError):
        return None

    if sqrtA <= 0 or not (0 <= e < 1):
        return None

    # Compute t (epoch GPS seconds of week) relative to Toe.
    offset = SYS_TOC_OFFSET_SEC.get(sys_char, 0.0)
    toc_gps = eph["toc"] + timedelta(seconds=offset)
    # delta from this ephemeris's toc (in GPS time)
    dt_from_toc = (epoch_dt_gps - toc_gps).total_seconds()
    # The Toe in orbit[8] is GPS seconds-of-week of the ephemeris. We work in
    # delta-time from toc which is the reference epoch the ephemeris was issued
    # for. Toe is essentially toc rounded to the issue point, so for delta_t
    # accuracy within the fit window we use dt_from_toc directly.
    tk = dt_from_toc

    A = sqrtA * sqrtA
    n0 = math.sqrt(GM / (A ** 3))
    n = n0 + delta_n
    M = M0 + n * tk

    # Solve Kepler's equation: E - e*sin(E) = M
    E = M
    for _ in range(20):
        f = E - e * math.sin(E) - M
        fp = 1.0 - e * math.cos(E)
        dE = -f / fp
        E += dE
        if abs(dE) < 1e-13:
            break

    cos_E = math.cos(E)
    sin_E = math.sin(E)
    v = math.atan2(math.sqrt(1.0 - e * e) * sin_E, cos_E - e)
    Phi = v + omega
    cos2Phi = math.cos(2.0 * Phi)
    sin2Phi = math.sin(2.0 * Phi)
    du = Cuc * cos2Phi + Cus * sin2Phi
    dr = Crc * cos2Phi + Crs * sin2Phi
    di = Cic * cos2Phi + Cis * sin2Phi
    u = Phi + du
    r = A * (1.0 - e * cos_E) + dr
    i = i0 + IDOT * tk + di
    x_op = r * math.cos(u)
    y_op = r * math.sin(u)
    Omega = Omega0 + (OmegaDot - OMEGA_E_DOT) * tk - OMEGA_E_DOT * Toe
    cos_Omega = math.cos(Omega)
    sin_Omega = math.sin(Omega)
    cos_i = math.cos(i)
    sin_i = math.sin(i)
    X = x_op * cos_Omega - y_op * cos_i * sin_Omega
    Y = x_op * sin_Omega + y_op * cos_i * cos_Omega
    Z = y_op * sin_i
    return X, Y, Z


# ---- GLONASS linear propagation --------------------------------------------

def propagate_glonass(eph: dict, epoch_dt_gps: datetime) -> tuple[float, float, float] | None:
    """Return ECEF (x, y, z) in metres for a GLONASS ephemeris at the given epoch.

    Uses position + velocity * dt + 0.5 * acceleration * dt^2 in PZ-90 frame
    (km units → m). PZ-90 vs WGS-84 offset (~1 m) is below PDOP precision.
    None if records malformed.
    """
    o = eph["orbit"]
    if len(o) < 11:
        return None
    try:
        Xkm   = o[0]   # km
        VXkm  = o[1]   # km/s
        AXkm  = o[2]   # km/s^2
        Ykm   = o[4]
        VYkm  = o[5]
        AYkm  = o[6]
        Zkm   = o[8]
        VZkm  = o[9]
        AZkm  = o[10]
    except (IndexError, TypeError):
        return None

    offset = SYS_TOC_OFFSET_SEC["R"]
    toc_gps = eph["toc"] + timedelta(seconds=offset)
    dt = (epoch_dt_gps - toc_gps).total_seconds()

    X = (Xkm + VXkm * dt + 0.5 * AXkm * dt * dt) * 1000.0
    Y = (Ykm + VYkm * dt + 0.5 * AYkm * dt * dt) * 1000.0
    Z = (Zkm + VZkm * dt + 0.5 * AZkm * dt * dt) * 1000.0
    return X, Y, Z


def propagate(eph: dict, epoch_dt_gps: datetime, sys_char: str) -> tuple[float, float, float] | None:
    if sys_char == "R":
        return propagate_glonass(eph, epoch_dt_gps)
    if sys_char in ("G", "E", "J", "C"):
        return propagate_keplerian(eph, epoch_dt_gps, sys_char)
    return None


# ---- PDOP from geometry matrix ---------------------------------------------

def compute_pdop(
    sat_positions_ecef: list[tuple[float, float, float]],
    receiver_pos_ecef: tuple[float, float, float],
    elevation_mask_deg: float = 10.0,
) -> tuple[float | None, int]:
    """Return (PDOP, n_sats_used). PDOP is None if < 4 sats survive masking.

    PDOP = sqrt(σx² + σy² + σz²) where Σ = (HᵀH)⁻¹ and rows of H are
    the line-of-sight unit vector from receiver→sat, in ECEF, with a clock
    column of 1.
    """
    rx = np.array(receiver_pos_ecef, dtype=float)
    rx_norm = float(np.linalg.norm(rx))
    if rx_norm < 1.0:
        return None, 0
    zenith = rx / rx_norm
    sin_mask = math.sin(math.radians(elevation_mask_deg))

    rows: list[list[float]] = []
    for sp in sat_positions_ecef:
        spa = np.array(sp, dtype=float)
        los = spa - rx
        rng = float(np.linalg.norm(los))
        if rng < 1.0:
            continue
        los_unit = los / rng
        # cos(zenith_angle) = los_unit · zenith. sin(elevation) = cos(zenith_angle).
        sin_elev = float(np.dot(los_unit, zenith))
        if sin_elev < sin_mask:
            continue
        rows.append([-float(los_unit[0]), -float(los_unit[1]), -float(los_unit[2]), 1.0])

    if len(rows) < 4:
        return None, len(rows)
    H = np.array(rows, dtype=float)
    try:
        cov = np.linalg.inv(H.T @ H)
    except np.linalg.LinAlgError:
        return None, len(rows)
    pdop_sq = float(cov[0, 0] + cov[1, 1] + cov[2, 2])
    if pdop_sq <= 0:
        return None, len(rows)
    return math.sqrt(pdop_sq), len(rows)


# ---- ephemeris index helper ------------------------------------------------

def build_ephemeris_index(parsed_nav: dict[str, Any]) -> dict[str, list[dict]]:
    """Return {sat_id: sorted list of ephemeris records}."""
    return parsed_nav.get("ephemerides", {})
