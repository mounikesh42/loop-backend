#!/usr/bin/env python3
"""geometry.py - dependency-free 2-D geometry for Stage 3a.

Pre-processing's spatial derived fields (GCP/CP distribution coverage, coord
bbox-sanity, CP-GCP independence) need a little computational geometry but no
heavy dependency. All functions operate on (x, y) tuples in a projected frame
(easting, northing in metres) - the same frame as reconstruction_extent_polygon.

Pure stdlib; deterministic. Used by stage3a_derived.py (and previewed by
scripts/make_sample_data.py).
"""
from __future__ import annotations

from typing import Sequence

Point = tuple[float, float]


def as_xy(pos) -> Point | None:
    """Coerce a position (dict with easting/northing, or (x,y) pair) to (x, y)."""
    if pos is None:
        return None
    if isinstance(pos, dict):
        e, n = pos.get("easting"), pos.get("northing")
        if e is None or n is None:
            return None
        return (float(e), float(n))
    if isinstance(pos, (list, tuple)) and len(pos) >= 2:
        try:
            return (float(pos[0]), float(pos[1]))
        except (TypeError, ValueError):
            return None
    return None


def convex_hull(points: Sequence[Point]) -> list[Point]:
    """Andrew's monotone chain. Returns hull vertices CCW; <=2 unique pts pass through."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return list(pts)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[Point] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[Point] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def polygon_area(poly: Sequence[Point]) -> float:
    """Shoelace absolute area."""
    n = len(poly)
    if n < 3:
        return 0.0
    a = 0.0
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def bbox_of(poly: Sequence[Point]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return (min(xs), min(ys), max(xs), max(ys))


def point_in_polygon(pt: Point, poly: Sequence[Point]) -> bool:
    """Ray-casting; points on the boundary count as inside (good enough for sanity)."""
    x, y = pt
    n = len(poly)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x <= x_cross:
                inside = not inside
        j = i
    return inside


def point_within(pt: Point, poly: Sequence[Point], margin_m: float = 0.0) -> bool:
    """Inside the polygon, or within margin_m of its bbox (catches small overshoots
    while still rejecting axis-swap / unit-misparse, which land far outside)."""
    if point_in_polygon(pt, poly):
        return True
    if margin_m <= 0:
        return False
    minx, miny, maxx, maxy = bbox_of(poly)
    return (minx - margin_m <= pt[0] <= maxx + margin_m
            and miny - margin_m <= pt[1] <= maxy + margin_m)


def hull_coverage_fraction(points: Sequence[Point], polygon: Sequence[Point]) -> float | None:
    """Fraction of the polygon's area covered by the points' convex hull.

    Uses min(hull_area, polygon_area)/polygon_area so points spilling outside the
    polygon cannot push coverage above 1.0 (a clamp, not a true intersection -
    documented as the chosen approximation)."""
    pa = polygon_area(polygon)
    if pa <= 0 or len(points) < 3:
        return None
    ha = polygon_area(convex_hull(points))
    return round(min(ha, pa) / pa, 4)


def euclidean(a: Point, b: Point) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def min_pairwise_distance(a: Sequence[Point], b: Sequence[Point]) -> float | None:
    """Minimum distance between any point in a and any point in b (None if either empty)."""
    if not a or not b:
        return None
    return round(min(euclidean(p, q) for p in a for q in b), 4)
