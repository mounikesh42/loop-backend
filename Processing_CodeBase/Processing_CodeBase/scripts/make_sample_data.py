#!/usr/bin/env python3
"""make_sample_data.py - deterministic gold-standard STUB generator.

The real sample set ships the Agisoft report + orthomosaic + dem (the DSM). It
does NOT ship a DTM, a point cloud, or a 3D mesh. This generator synthesizes
tiny, spec-faithful STUB deliverables so all five per-deliverable views compute
non-null in the baseline scenario:

  - dtm.tif         tiny EPSG:4326 GeoTIFF  (bare-earth stand-in)
  - point_cloud.las tiny EPSG:4326 LAS 1.4  (a handful of points)
  - mesh.obj        tiny OBJ                (a tetrahedron, no CRS)

Each is marked a PLACEHOLDER STUB (GeoTIFF tag / LAS system_identifier / OBJ
comment) and co-located with the real DSM so the synthetic CRS/extent is
consistent with the genuine deliverables. Deterministic (no RNG) and
self-verifying: every output is re-opened and its CRS / point count asserted.

The manifest + pp_handoff placeholders are hand-authored JSON/CSV (Steps 4 & 6),
NOT generated here - this script only produces the three binary stubs.

Run: python3 scripts/make_sample_data.py paths.json
"""
from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
import laspy
import pyproj

STUB_MARKER = "PLACEHOLDER_STUB"
EPSG = 4326
# fallback origin (lon, lat) if the real DSM is unavailable to co-locate against
_FALLBACK_ORIGIN = (77.5000, 13.0000)
_PX_DEG = 0.0001  # ~11 m/pixel at the equator - fine for a stub


def _dsm_origin(root: Path, config: dict):
    """Top-left (lon, lat) of the real DSM so stubs sit on the same ground."""
    dsm_rel = config["inputs"]["deliverables"].get("dsm")
    if dsm_rel:
        dsm_path = root / dsm_rel
        if dsm_path.is_file():
            try:
                with rasterio.open(dsm_path) as ds:
                    return ds.bounds.left, ds.bounds.top
            except rasterio.errors.RasterioError:
                pass
    return _FALLBACK_ORIGIN


def make_dtm(path: Path, origin) -> dict:
    """Tiny float32 DTM (bare-earth) in EPSG:4326."""
    lon0, lat0 = origin
    h = w = 16
    # smooth bare-earth surface ~450-470 m (a touch below the DSM band 453-481)
    yy, xx = np.mgrid[0:h, 0:w]
    data = (450.0 + 0.6 * xx + 0.4 * yy).astype("float32")
    transform = from_origin(lon0, lat0, _PX_DEG, _PX_DEG)
    profile = dict(driver="GTiff", dtype="float32", count=1, height=h, width=w,
                   crs=f"EPSG:{EPSG}", transform=transform, nodata=-9999.0)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data, 1)
        dst.update_tags(STATUS=STUB_MARKER,
                        _note="synthetic bare-earth DTM stub (make_sample_data.py)")
    with rasterio.open(path) as ds:
        return {"file": path.name, "epsg": ds.crs.to_epsg(), "shape": [ds.height, ds.width],
                "status_tag": ds.tags().get("STATUS")}


def make_point_cloud(path: Path, origin) -> dict:
    """Tiny LAS 1.4 point cloud in EPSG:4326."""
    lon0, lat0 = origin
    n = 343  # 7x7x7 lattice
    g = np.linspace(0, 6 * _PX_DEG, 7)
    gx, gy, gz = np.meshgrid(g, g, np.linspace(450.0, 470.0, 7))
    xs = lon0 + gx.ravel()
    ys = lat0 - gy.ravel()
    zs = gz.ravel()
    header = laspy.LasHeader(point_format=3, version="1.4")
    header.add_crs(pyproj.CRS.from_epsg(EPSG))
    header.system_identifier = STUB_MARKER
    header.generating_software = "make_sample_data"
    header.creation_date = datetime.date(2026, 5, 19)  # fixed (survey date) for byte-determinism
    las = laspy.LasData(header)
    las.x, las.y, las.z = xs, ys, zs
    path.parent.mkdir(parents=True, exist_ok=True)
    las.write(path)
    with laspy.open(path) as rdr:
        crs = rdr.header.parse_crs()
        return {"file": path.name, "epsg": crs.to_epsg() if crs else None,
                "points": rdr.header.point_count, "system_id": rdr.header.system_identifier.strip()}


def make_mesh(path: Path) -> dict:
    """Tiny OBJ tetrahedron (no CRS - meshes carry none)."""
    obj = (
        "# Agisoft Metashape 3D textured mesh (PLACEHOLDER STUB)\n"
        f"# _status: {STUB_MARKER}\n"
        "v 0.0 0.0 0.0\nv 1.0 0.0 0.0\nv 0.0 1.0 0.0\nv 0.0 0.0 1.0\n"
        "f 1 2 3\nf 1 2 4\nf 1 3 4\nf 2 3 4\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(obj, encoding="utf-8")
    txt = path.read_text(encoding="utf-8")
    return {"file": path.name, "vertices": txt.count("\nv "), "faces": txt.count("\nf "),
            "stub_marked": STUB_MARKER in txt}


def run(config: dict, root: Path) -> dict:
    deliv = config["inputs"]["deliverables"]
    origin = _dsm_origin(root, config)
    out = {}
    out["dtm"] = make_dtm(root / deliv["dtm"], origin)
    out["point_cloud"] = make_point_cloud(root / deliv["point_cloud"], origin)
    out["mesh_3d"] = make_mesh(root / deliv["mesh_3d"])
    # self-verification asserts
    assert out["dtm"]["epsg"] == EPSG, out["dtm"]
    assert out["dtm"]["status_tag"] == STUB_MARKER, out["dtm"]
    assert out["point_cloud"]["epsg"] == EPSG, out["point_cloud"]
    assert out["point_cloud"]["points"] == 343, out["point_cloud"]
    assert out["mesh_3d"]["vertices"] == 4 and out["mesh_3d"]["faces"] == 4, out["mesh_3d"]
    out["origin_lonlat"] = [round(origin[0], 6), round(origin[1], 6)]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Generate gold-standard stub deliverables")
    ap.add_argument("config", help="path to paths.json")
    args = ap.parse_args(argv)
    config_path = Path(args.config).resolve()
    with config_path.open() as fh:
        config = json.load(fh)
    root = config_path.parent
    out = run(config, root)
    print("Generated stub deliverables (PLACEHOLDER_STUB), co-located with the real DSM:")
    for k in ("dtm", "point_cloud", "mesh_3d"):
        print(f"  {k:12s} {out[k]}")
    print(f"  origin(lon,lat) = {out['origin_lonlat']}")
    print("Self-verification: all asserts passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
