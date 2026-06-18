# BaseStation Pipeline — Demo README

This repository contains a pipeline that computes a base-station confidence score from RINEX, operator logs, and user input. The sample data and a demo `paths.json` are included.

Quick demo (Windows PowerShell, run from project root):

1. Create and activate a virtual environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

2. Install dependencies

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

3. Run the pipeline using the provided `paths.json` (uses `sample_data/`):

```powershell
python scripts/run_pipeline.py paths.json
```

4. Outputs

- JSON outputs are written to the `outputs/` folder (e.g. `outputs/06_base_station_score.json`).
- A shared SQLite DB is created at `../../apicalls/pipeline.db`. BaseStation writes one table per stage, prefixed with `base_station_`.

Querying the DB (example):

```powershell
.venv\Scripts\python.exe -c "import sqlite3, pathlib; p=pathlib.Path('../../apicalls/pipeline.db'); conn=sqlite3.connect(str(p)); cur=conn.cursor(); cur.execute(\"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name\"); print(cur.fetchall()); conn.close()"
```

Notes

- If you run on real survey data, replace the placeholder values in `sample_data/*` and update `paths.json` accordingly.
- The pipeline will print warnings and halt on hard failures depending on `paths.json` options.

If you want, I can:
- Normalize the DB schema into separate tables (sources, fields, indicators, blocks, score), or
- Add a small `scripts/export_sql.py` to dump rows into CSV files. 

Tell me which next step you prefer.
