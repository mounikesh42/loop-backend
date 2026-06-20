# Survey data (one folder per site)

Change **only this folder** (and `site.json`) when switching to another mine/site.

## Files

| Path | Purpose |
|------|---------|
| `site.json` | Site name, bounds, S3 URLs, TiTiler/CTOD, 3D Tiles, raster COGs, **all layer scores** |
| `geotagged/manifest.json` | Photo GPS list + image URLs |
| `../shapes/*.geojson` | Analytics polygons (stockpiles, pits, etc.) — at project root |

## New site checklist

1. Copy the whole `survey/` folder (e.g. `survey-hyderabad` → `survey-kcm`).
2. Edit `site.json`: `services.s3Base`, `rasters`, `pointCloud`, `model3d`, `geotagged`, `bounds`.
3. Replace `geotagged/manifest.json` and `shapes/*.geojson`.
4. Point the app at your config if needed: `SurveyConfig.load('./survey/site.json')` (default).

No need to edit `js/*.js` for URL or coordinate changes.

## Layer scores (single JSON)

Edit **`survey/site.json` → `scores`** to change scores for every stage:

| Section | Keys | Drives |
|---------|------|--------|
| `scores.capture` | `drone`, `flightpath`, `basestation`, `gcp`, `anomalies` | Capture panel + flight anomaly markers |
| `capture.surveyPoints` | `POINT 1` … | GCP / base station positions + per-point score |
| `scores.processing` | `ortho`, `dsm`, `dtm`, `mesh`, `pointcloud`, `images` | Processing right panel + geotagged image point colors |
| `scores.analytics` | `stockpiles`, `pits`, `wastedumps`, `cfzones` | Analytics panel + polygon colors (use **SCORE** tab) |

Each entry uses `score`, `state` (`good` / `warn` / `crit`), and optional `pastScore`.  
`thresholds` auto-derives `state` from `score` when `state` is omitted.  
Per-image overrides: `scores.processing.images.overrides["DSC00003"]`.

## 3D model & point cloud (CB-UI style)

Processing page (`globe_processing.html`) matches production **CB-UI** behaviour:

- Load `generated/…/3dmodel_3dtiles/tileset.json` and `…/pointcloud_3dtiles/tileset.json`
- **Flat ellipsoid** terrain, `depthTestAgainstTerrain = false`
- **`modelMatrix = IDENTITY`** — trust georeferencing in `tileset.json` (no manual shift)

`site.json` → `alignment.method` ( **3D model only** ):

| Value | Behaviour |
|--------|-----------|
| `trustTileset` | **Default for mesh** — CB-UI `Layers3DModel` (IDENTITY matrix, flat globe) |

**Point cloud** uses the original compare loader: DEM sample for height, flat globe, `pointCloudMaxScreenSpaceError` default 8 — not `trustTileset`.

**Point cloud tiles**

S3 only: `{s3Base}/generated/POINT_CLOUD_32644_point_cloud_file_key_1153/pointcloud_3dtiles/tileset.json`

Local `./CesiumTiles/` is no longer used by the app (folder may remain for backup). Ensure the S3 bucket allows CORS for your dev origin.

3D model tile URLs: `{s3Base}/generated/{jobName}/3dmodel_3dtiles/`.
