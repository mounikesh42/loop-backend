"""Combined API for the shared BaseStation + GCP pipeline database."""

import json
import os
import ssl
import sqlite3
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from flask import Flask, jsonify, abort, request, send_from_directory, Response
from flask_cors import CORS
from werkzeug.utils import secure_filename

app = Flask(__name__)
CORS(app)
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("LOOP_MAX_UPLOAD_BYTES", 1024 * 1024 * 1024))

ROOT_PATH = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("LOOP_PIPELINE_DB", Path(__file__).parent / "pipeline.db"))
UPLOAD_ROOT = Path(os.environ.get("LOOP_UPLOAD_ROOT", ROOT_PATH / "uploads"))
JOBS_DB_PATH = Path(os.environ.get("LOOP_JOBS_DB", Path(__file__).parent / "jobs.db"))
PIPELINE_SCRIPT = Path(os.environ.get("LOOP_PIPELINE_SCRIPT", ROOT_PATH / "run_all_to_db.ps1"))
WEB_PATH = ROOT_PATH / "web"
SITE_REALITY_PATH = ROOT_PATH / "site_reality" / "compare"
UPLOAD_RETENTION_DAYS = int(os.environ.get("LOOP_UPLOAD_RETENTION_DAYS", "30"))
ADMIN_TOKEN = os.environ.get("LOOP_ADMIN_TOKEN", "")
pipeline_lock = threading.Lock()

TITILER_UPSTREAM = os.environ.get("LOOP_TITILER_UPSTREAM", "https://titiler2.cbstack.online")
CTOD_UPSTREAM = os.environ.get("LOOP_CTOD_UPSTREAM", "https://ctod2.cbstack.online")
S3_UPSTREAM = os.environ.get("LOOP_S3_UPSTREAM", "https://prodcrystalball.s3.amazonaws.com")
SITE_REALITY_ASSET_BASE = os.environ.get(
    "SITE_REALITY_ASSET_BASE",
    "https://prodcrystalball.s3.amazonaws.com/site-reality/hyderabad-m7-2026-05-18",
).rstrip("/")
SITE_REALITY_PROXIES = {
    "titiler-proxy": TITILER_UPSTREAM,
    "ctod-proxy": CTOD_UPSTREAM,
    "s3-proxy": S3_UPSTREAM,
}
SSL_CTX = ssl._create_unverified_context()

TARGETS = {"base_station", "drone", "gcp", "check_point", "all"}
TARGET_SEQUENCE = ("base_station", "drone", "gcp", "check_point")
TARGET_REQUIRED_INPUTS = {
    "base_station": ("base_rinex", "anchor_session", "ant_setup"),
    "drone": ("raw_images", "rover_rinex", "mrk", "drone_user_input", "cam_calib"),
    "gcp": ("gcp_rinex",),
    "check_point": ("checkpoint_points",),
}
INPUT_LABELS = {
    "base_rinex": "Base station RINEX files",
    "anchor_session": "Base station operator log files",
    "ant_setup": "Base station antenna/user input files",
    "raw_images": "Drone raw image files",
    "rover_rinex": "Drone rover RINEX files",
    "mrk": "Drone telemetry/MRK files",
    "drone_user_input": "Drone user input form JSON",
    "cam_calib": "Drone camera/hardware calibration JSON",
    "gcp_rinex": "GCP point folders/files",
    "checkpoint_points": "Check point folders/files",
}


# ── helpers ────────────────────────────────────────────────────────────────────

def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def get_jobs_db():
    JOBS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(JOBS_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            upload_dir TEXT NOT NULL,
            file_count INTEGER NOT NULL DEFAULT 0,
            files_json TEXT NOT NULL DEFAULT '[]',
            stdout TEXT,
            stderr TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT
        )
        """
    )
    existing_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
    }
    migrations = {
        "target": "ALTER TABLE jobs ADD COLUMN target TEXT",
        "config_path": "ALTER TABLE jobs ADD COLUMN config_path TEXT",
        "config_json": "ALTER TABLE jobs ADD COLUMN config_json TEXT",
    }
    for column, statement in migrations.items():
        if column not in existing_columns:
            conn.execute(statement)
    conn.commit()
    return conn


def cleanup_old_uploads(retention_days: int = UPLOAD_RETENTION_DAYS, dry_run: bool = False):
    if retention_days <= 0:
        return {"retention_days": retention_days, "deleted": [], "skipped": [], "dry_run": dry_run}

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    deleted = []
    skipped = []

    with get_jobs_db() as conn:
        rows = conn.execute("SELECT id, status, upload_dir, created_at FROM jobs").fetchall()
        for row in rows:
            job_created = parse_iso(row["created_at"])
            upload_dir = Path(row["upload_dir"])

            if row["status"] == "running":
                skipped.append({"job_id": row["id"], "reason": "running"})
                continue
            if not job_created or job_created >= cutoff:
                continue
            if not upload_dir.exists():
                continue

            deleted.append({
                "job_id": row["id"],
                "upload_dir": str(upload_dir),
                "created_at": row["created_at"],
            })
            if not dry_run:
                import shutil
                shutil.rmtree(upload_dir, ignore_errors=True)
                conn.execute(
                    "UPDATE jobs SET status = ?, upload_dir = ?, error = ? WHERE id = ?",
                    (
                        "expired",
                        str(upload_dir),
                        f"Uploaded files deleted after {retention_days} day retention period.",
                        row["id"],
                    ),
                )
        if not dry_run:
            conn.commit()

    return {
        "retention_days": retention_days,
        "deleted": deleted,
        "skipped": skipped,
        "dry_run": dry_run,
    }


def update_job(job_id: str, **fields):
    if not fields:
        return
    keys = list(fields.keys())
    assignments = ", ".join(f"{key} = ?" for key in keys)
    values = [fields[key] for key in keys] + [job_id]
    with get_jobs_db() as conn:
        conn.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", values)
        conn.commit()


def job_to_dict(row):
    item = dict(row)
    item["files"] = json_or_raw(item.pop("files_json", "[]")) or []
    return item


def get_job(job_id: str):
    with get_jobs_db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return job_to_dict(row) if row else None


def safe_upload_target(upload_dir: Path, raw_filename: str, fallback_name: str) -> Path:
    clean_parts = []
    normalized = (raw_filename or "").replace("\\", "/")
    for part in normalized.split("/"):
        if not part or part in {".", ".."}:
            continue
        cleaned = secure_filename(part)
        if cleaned:
            clean_parts.append(cleaned)

    if not clean_parts:
        clean_parts = [fallback_name]

    target = upload_dir.joinpath(*clean_parts)
    upload_root = upload_dir.resolve()
    resolved_parent = target.parent.resolve()
    if upload_root != resolved_parent and upload_root not in resolved_parent.parents:
        raise ValueError(f"Unsafe upload filename: {raw_filename}")
    return target


def pipeline_command():
    shell = "powershell" if os.name == "nt" else "pwsh"
    return [shell, "-ExecutionPolicy", "Bypass", "-File", str(PIPELINE_SCRIPT)]


def add_site_packages(env: dict, root: Path):
    site_packages = root / ".venv" / "Lib" / "site-packages"
    if not site_packages.exists():
        return
    existing_pythonpath = env.get("PYTHONPATH", "")
    paths = [str(site_packages)]
    if existing_pythonpath:
        paths.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(paths)


def required_inputs_for_target(target: str):
    if target == "all":
        seen = []
        for item in TARGET_SEQUENCE:
            for input_id in TARGET_REQUIRED_INPUTS[item]:
                if input_id not in seen:
                    seen.append(input_id)
        return tuple(seen)
    return TARGET_REQUIRED_INPUTS.get(target, ())


def validate_job_inputs(job: dict, target: str):
    missing = []
    files = job.get("files", [])
    for input_id in required_inputs_for_target(target):
        matches = [
            item for item in files
            if item.get("input_id") == input_id and item.get("path") and Path(item["path"]).exists()
        ]
        if not matches:
            missing.append({
                "input_id": input_id,
                "label": INPUT_LABELS.get(input_id, input_id),
            })

    if missing:
        details = ", ".join(f"{item['label']} ({item['input_id']})" for item in missing)
        raise ValueError(f"Missing required upload group(s) for target '{target}': {details}")


def module_commands(job_id: str, target: str, env: dict):
    python_exe = os.environ.get("LOOP_PIPELINE_PYTHON", sys.executable)

    if target == "base_station":
        root = module_root("BaseStation_CodeBase", "BaseStation_CodeBase")
        add_site_packages(env, root)
        config_path = write_base_station_job_config(job_id)
        return [(root, [python_exe, "scripts/run_pipeline.py", str(config_path)])]

    if target == "drone":
        root = module_root("Drone_CodeBase", "Drone_CodeBase")
        add_site_packages(env, root)
        config_path = write_drone_job_config(job_id)
        return [
            (root, [python_exe, "scripts/run_pipeline.py", str(config_path)]),
            (root, [python_exe, "scripts/load_to_db.py", str(config_path)]),
        ]

    if target == "gcp":
        root = module_root("GCP_CodeBase", "GCP_CodeBase")
        add_site_packages(env, root)
        config_path = write_gcp_job_config(job_id)
        return [
            (root, [python_exe, "scripts/run_pipeline.py", str(config_path)]),
            (root, [python_exe, "scripts/load_to_db.py", str(config_path)]),
        ]

    if target == "check_point":
        root = module_root("CheckPoint_CodeBase", "CheckPoint_CodeBase")
        add_site_packages(env, root)
        config_path = write_check_point_job_config(job_id)
        return [(root, [python_exe, "scripts/sqlite_pipeline.py", "save", str(config_path)])]

    if target == "all":
        commands = []
        for item in TARGET_SEQUENCE:
            commands.extend(module_commands(job_id, item, env))
        return commands

    raise ValueError(f"Unsupported validation target: {target}")


def append_job_files(job_id: str, saved_files):
    job = get_job(job_id)
    if not job:
        abort(404, description=f"Job '{job_id}' not found")

    files = job.get("files", []) + saved_files
    with get_jobs_db() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET file_count = ?, files_json = ?
            WHERE id = ?
            """,
            (len(files), json.dumps(files), job_id),
        )
        conn.commit()


def uploaded_files_for(job, input_id: str):
    matches = [
        Path(item["path"])
        for item in job.get("files", [])
        if item.get("input_id") == input_id and item.get("path")
    ]
    existing = [path for path in matches if path.exists()]
    if not existing:
        label = INPUT_LABELS.get(input_id, input_id)
        raise ValueError(f"Missing required upload group: {label} ({input_id})")
    return existing


def uploaded_folder_for(job, input_id: str) -> Path:
    matches = [
        path.parent
        for path in uploaded_files_for(job, input_id)
    ]
    try:
        return Path(os.path.commonpath([str(parent) for parent in matches]))
    except ValueError:
        return matches[0]


def uploaded_file_for(job, input_id: str) -> Path:
    return uploaded_files_for(job, input_id)[-1]


def module_root(*parts: str) -> Path:
    return ROOT_PATH.joinpath(*parts)


def write_json_config(job_id: str, root: Path, filename_prefix: str, config: dict, target: str) -> Path:
    config["survey_id"] = job_id
    config_path = root / f"paths.{job_id}.{filename_prefix}.json"
    with config_path.open("w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)
    update_job(
        job_id,
        target=target,
        config_path=str(config_path),
        config_json=json.dumps(config, indent=2),
    )
    return config_path


def write_base_station_job_config(job_id: str) -> Path:
    job = get_job(job_id)
    if not job:
        raise FileNotFoundError(f"Job '{job_id}' not found")

    base_root = ROOT_PATH / "BaseStation_CodeBase" / "BaseStation_CodeBase"
    with (base_root / "paths.json").open("r", encoding="utf-8") as fh:
        config = json.load(fh)

    config["inputs"]["rinex_folder"] = str(
        uploaded_folder_for(job, "base_rinex")
    )
    config["inputs"]["operator_log_folder"] = str(
        uploaded_folder_for(job, "anchor_session")
    )
    config["inputs"]["user_input_folder"] = str(
        uploaded_folder_for(job, "ant_setup")
    )

    return write_json_config(job_id, base_root, "base_station", config, "base_station")


def write_drone_job_config(job_id: str) -> Path:
    job = get_job(job_id)
    if not job:
        raise FileNotFoundError(f"Job '{job_id}' not found")

    root = module_root("Drone_CodeBase", "Drone_CodeBase")
    with (root / "paths.json").open("r", encoding="utf-8") as fh:
        config = json.load(fh)

    config["inputs"]["images_folder"] = str(
        uploaded_folder_for(job, "raw_images")
    )
    config["inputs"]["rinex_folder"] = str(
        uploaded_folder_for(job, "rover_rinex")
    )
    config["inputs"]["bin_folder"] = str(
        uploaded_folder_for(job, "mrk")
    )
    config["inputs"]["user_input_file"] = str(
        uploaded_file_for(job, "drone_user_input")
    )
    config["inputs"]["user_hardware_file"] = str(
        uploaded_file_for(job, "cam_calib")
    )
    return write_json_config(job_id, root, "drone", config, "drone")


def write_gcp_job_config(job_id: str) -> Path:
    job = get_job(job_id)
    if not job:
        raise FileNotFoundError(f"Job '{job_id}' not found")

    root = module_root("GCP_CodeBase", "GCP_CodeBase")
    with (root / "paths.json").open("r", encoding="utf-8") as fh:
        config = json.load(fh)

    config["inputs"]["points_root"] = str(
        uploaded_folder_for(job, "gcp_rinex")
    )
    return write_json_config(job_id, root, "gcp", config, "gcp")


def write_check_point_job_config(job_id: str) -> Path:
    job = get_job(job_id)
    if not job:
        raise FileNotFoundError(f"Job '{job_id}' not found")

    root = module_root("CheckPoint_CodeBase", "CheckPoint_CodeBase")
    with (root / "paths.json").open("r", encoding="utf-8") as fh:
        config = json.load(fh)

    config["inputs"]["points_root"] = str(
        uploaded_folder_for(job, "checkpoint_points")
    )
    return write_json_config(job_id, root, "check_point", config, "check_point")


def run_pipeline_job(job_id: str, target: str = "all"):
    update_job(job_id, status="running", started_at=utc_now())
    try:
        env = os.environ.copy()
        env["LOOP_PIPELINE_DB"] = str(DB_PATH)
        commands = module_commands(job_id, target, env)

        with pipeline_lock:
            stdout_parts = []
            stderr_parts = []
            returncode = 0
            for cwd, command in commands:
                completed = subprocess.run(
                    command,
                    cwd=str(cwd),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=int(os.environ.get("LOOP_PIPELINE_TIMEOUT_SECONDS", 1800)),
                )
                stdout_parts.append(f"$ {' '.join(command)}\n{completed.stdout}")
                if completed.stderr:
                    stderr_parts.append(completed.stderr)
                returncode = completed.returncode
                if completed.returncode != 0:
                    break

        stdout = "\n".join(stdout_parts)
        stderr = "\n".join(stderr_parts)

        update_job(
            job_id,
            status="completed" if returncode == 0 else "failed",
            stdout=stdout[-20000:],
            stderr=stderr[-20000:],
            error=None if returncode == 0 else f"Pipeline exited with code {returncode}",
            completed_at=utc_now(),
        )
    except Exception as exc:
        update_job(
            job_id,
            status="failed",
            error=str(exc),
            completed_at=utc_now(),
        )


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def json_or_raw(value):
    if value is None:
        return None
    try:
        return json.loads(value)
    except Exception:
        return value


def base_station_latest_indicators():
    """Return the indicator_traces dict from the most recent run."""
    with get_db() as conn:
        if not table_exists(conn, "base_station_stage3b_indicators"):
            return {}
        row = conn.execute(
            "SELECT value_json FROM base_station_stage3b_indicators "
            "WHERE key = 'indicator_traces' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return {}
    return json.loads(row["value_json"])


def base_station_latest_flags():
    with get_db() as conn:
        if not table_exists(conn, "base_station_stage3b_indicators"):
            return []
        row = conn.execute(
            "SELECT value_json FROM base_station_stage3b_indicators "
            "WHERE key = 'flags_raised_stage3b' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return json.loads(row["value_json"]) if row else []


def base_station_latest_meta():
    with get_db() as conn:
        if not table_exists(conn, "base_station_stage3b_indicators"):
            return {}
        row = conn.execute(
            "SELECT value_json FROM base_station_stage3b_indicators "
            "WHERE key = 'stage3b_meta' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return json.loads(row["value_json"]) if row else {}


def first_row(table: str):
    with get_db() as conn:
        if not table_exists(conn, table):
            return None
        return conn.execute(f'SELECT * FROM "{table}" LIMIT 1').fetchone()


def envelope_data(table: str):
    row = first_row(table)
    if not row or "envelope" not in row.keys():
        return {}
    envelope = json_or_raw(row["envelope"])
    if isinstance(envelope, dict):
        return envelope.get("data", envelope)
    return {}


def row_json_field(table: str, field: str, default=None):
    row = first_row(table)
    if not row or field not in row.keys():
        return default
    parsed = json_or_raw(row[field])
    return default if parsed is None else parsed


def row_dict(table: str):
    row = first_row(table)
    return dict(row) if row else {}


# ── routes ─────────────────────────────────────────────────────────────────────

@app.get("/upload")
@app.get("/upload/")
def upload_page():
    return send_from_directory(WEB_PATH, "upload.html")


@app.get("/web/<path:filename>")
def web_asset(filename: str):
    return send_from_directory(WEB_PATH, filename)


@app.get("/site-reality")
@app.get("/site-reality/")
def site_reality_index():
    if not SITE_REALITY_PATH.exists():
        abort(404, description="Site Reality bundle is not available.")
    return send_from_directory(SITE_REALITY_PATH, "index.html")


@app.get("/site-reality/survey/site.json")
def site_reality_config():
    config_path = SITE_REALITY_PATH / "survey" / "site.json"
    if not config_path.exists():
        abort(404, description="Site Reality survey config is not available.")

    with config_path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    if SITE_REALITY_ASSET_BASE:
        asset_base = SITE_REALITY_ASSET_BASE
        config.setdefault("services", {})["assetBase"] = asset_base

        point_cloud = config.setdefault("pointCloud", {})
        point_cloud["source"] = "s3"
        point_cloud["tilesetUrl"] = f"{asset_base}/pointcloud_3dtiles/tileset.json"
        point_cloud["metadataUrl"] = f"{asset_base}/pointcloud_3dtiles/metadata.json"

        capture = config.setdefault("capture", {})
        capture["droneLog"] = {
            "gpx": f"{asset_base}/drone_log/00000215.BIN.gpx",
            "wpl": f"{asset_base}/drone_log/00000215.BIN0wp.txt",
            "kml": f"{asset_base}/drone_log/_kmz_extract/00000215.BIN.kml",
            "param": f"{asset_base}/drone_log/00000215.BIN.param",
        }
        capture["models"] = {
            "drone": f"{asset_base}/models/CesiumDrone/CesiumDrone.glb",
            "droneFallback": "https://raw.githubusercontent.com/CesiumGS/cesium/main/Apps/SampleData/models/CesiumDrone/CesiumDrone.glb",
            "tripod": f"{asset_base}/models/leica_tripod/scene.gltf",
            "baseStation": f"{asset_base}/models/gnss_university_of_applied_sciences_mainz/scene.gltf",
        }

    return jsonify(config)


@app.get("/site-reality/<path:filename>")
def site_reality_asset(filename: str):
    if not SITE_REALITY_PATH.exists():
        abort(404, description="Site Reality bundle is not available.")
    return send_from_directory(SITE_REALITY_PATH, filename)


def proxy_headers():
    origin = request.headers.get("Origin", "*") or "*"
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization, Range",
        "Access-Control-Max-Age": "86400",
        "Vary": "Origin",
    }


def site_reality_proxy(prefix: str, suffix: str = ""):
    upstream = SITE_REALITY_PROXIES[prefix].rstrip("/")
    target = upstream + ("/" + suffix.lstrip("/") if suffix else "")
    if request.query_string:
        target += "?" + request.query_string.decode("utf-8", errors="ignore")

    headers = {
        "User-Agent": request.headers.get(
            "User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        ),
        "Accept": request.headers.get("Accept", "*/*"),
    }
    for name in ("Authorization", "Referer", "Range"):
        value = request.headers.get(name)
        if value:
            headers[name] = value

    req = urllib.request.Request(
        target,
        method="HEAD" if request.method == "HEAD" else "GET",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=120) as resp:
            body = b"" if request.method == "HEAD" else resp.read()
            response_headers = proxy_headers()
            for key, value in resp.headers.items():
                if key.lower() in {"transfer-encoding", "connection", "content-encoding", "content-length"}:
                    continue
                response_headers[key] = value
            return Response(body, status=resp.status, headers=response_headers)
    except urllib.error.HTTPError as exc:
        body = exc.read()
        response_headers = proxy_headers()
        response_headers["Content-Type"] = exc.headers.get("Content-Type", "text/plain")
        return Response(body, status=exc.code, headers=response_headers)
    except Exception as exc:
        return jsonify({"error": repr(exc), "target": target}), 502


@app.route("/titiler-proxy", methods=["GET", "HEAD", "OPTIONS"])
@app.route("/titiler-proxy/<path:suffix>", methods=["GET", "HEAD", "OPTIONS"])
@app.route("/ctod-proxy", methods=["GET", "HEAD", "OPTIONS"])
@app.route("/ctod-proxy/<path:suffix>", methods=["GET", "HEAD", "OPTIONS"])
@app.route("/s3-proxy", methods=["GET", "HEAD", "OPTIONS"])
@app.route("/s3-proxy/<path:suffix>", methods=["GET", "HEAD", "OPTIONS"])
def site_reality_proxy_route(suffix: str = ""):
    prefix = request.path.strip("/").split("/", 1)[0]
    if request.method == "OPTIONS":
        return Response(b"", status=204, headers=proxy_headers())
    return site_reality_proxy(prefix, suffix)


@app.get("/")
def health():
    with get_db() as conn:
        tables = [
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
        ]
    return jsonify({
        "status": "ok",
        "database": str(DB_PATH),
        "tables": tables,
        "endpoints": {
            "base_station": "/api/base-station",
            "gcp": "/api/gcp",
            "drone": "/api/drone",
            "check_point": "/api/check-point",
            "create_job": "POST /api/jobs",
            "list_jobs": "GET /api/jobs",
        },
    })


@app.post("/api/jobs")
def create_job():
    cleanup_old_uploads()
    uploaded_files = []
    for field_name, values in request.files.lists():
        input_id = request.form.get("input_id") or (field_name if field_name != "files" else "")
        for item in values:
            uploaded_files.append((input_id, item))

    uploaded_files = [(input_id, item) for input_id, item in uploaded_files if item and item.filename]
    if not uploaded_files:
        abort(400, description="Upload at least one file using multipart/form-data.")

    job_id = uuid.uuid4().hex
    upload_dir = UPLOAD_ROOT / job_id
    upload_dir.mkdir(parents=True, exist_ok=False)

    saved_files = []
    for input_id, item in uploaded_files:
        target = safe_upload_target(
            upload_dir,
            item.filename,
            f"upload-{len(saved_files) + 1}",
        )
        counter = 1
        while target.exists():
            target = target.with_name(f"{target.stem}-{counter}{target.suffix}")
            counter += 1
        target.parent.mkdir(parents=True, exist_ok=True)
        item.save(target)
        relative_path = target.relative_to(upload_dir).as_posix()
        saved_files.append({
            "input_id": input_id,
            "name": target.name,
            "relative_path": relative_path,
            "path": str(target),
            "size_bytes": target.stat().st_size,
        })

    created_at = utc_now()
    with get_jobs_db() as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                id, status, upload_dir, file_count, files_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                "uploaded",
                str(upload_dir),
                len(saved_files),
                json.dumps(saved_files),
                created_at,
            ),
        )
        conn.commit()

    return jsonify({
        "job_id": job_id,
        "status": "uploaded",
        "upload_dir": str(upload_dir),
        "files": saved_files,
        "status_url": f"/api/jobs/{job_id}",
        "results_url": f"/api/jobs/{job_id}/results",
        "validate_url": f"/api/jobs/{job_id}/validate",
    }), 201


@app.post("/api/admin/cleanup-uploads")
def cleanup_uploads_endpoint():
    if ADMIN_TOKEN and request.headers.get("X-Admin-Token") != ADMIN_TOKEN:
        abort(403, description="Invalid admin token.")
    dry_run = request.args.get("dry_run", "").lower() in {"1", "true", "yes"}
    retention_days = int(request.args.get("retention_days", UPLOAD_RETENTION_DAYS))
    return jsonify(cleanup_old_uploads(retention_days=retention_days, dry_run=dry_run))


@app.post("/api/jobs/<job_id>/files")
def add_job_files(job_id: str):
    job = get_job(job_id)
    if not job:
        abort(404, description=f"Job '{job_id}' not found")
    if job["status"] == "running":
        abort(409, description="Cannot add files while validation is running.")

    uploaded_files = []
    for field_name, values in request.files.lists():
        input_id = request.form.get("input_id") or (field_name if field_name != "files" else "")
        for item in values:
            uploaded_files.append((input_id, item))
    uploaded_files = [(input_id, item) for input_id, item in uploaded_files if item and item.filename]
    if not uploaded_files:
        abort(400, description="Upload at least one file using multipart/form-data.")

    upload_dir = Path(job["upload_dir"])
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved_files = []
    for input_id, item in uploaded_files:
        target = safe_upload_target(
            upload_dir,
            item.filename,
            f"upload-{job['file_count'] + len(saved_files) + 1}",
        )
        counter = 1
        while target.exists():
            target = target.with_name(f"{target.stem}-{counter}{target.suffix}")
            counter += 1
        target.parent.mkdir(parents=True, exist_ok=True)
        item.save(target)
        relative_path = target.relative_to(upload_dir).as_posix()
        saved_files.append({
            "input_id": input_id,
            "name": target.name,
            "relative_path": relative_path,
            "path": str(target),
            "size_bytes": target.stat().st_size,
        })

    append_job_files(job_id, saved_files)
    update_job(job_id, status="uploaded", error=None)
    return jsonify(get_job(job_id)), 201


@app.post("/api/jobs/<job_id>/validate")
def validate_job(job_id: str):
    job = get_job(job_id)
    if not job:
        abort(404, description=f"Job '{job_id}' not found")
    if job["status"] == "running":
        return jsonify({
            "job_id": job_id,
            "status": "running",
            "target": job.get("target") or "base_station",
            "message": "Validation is already running.",
            "status_url": f"/api/jobs/{job_id}",
            "results_url": f"/api/jobs/{job_id}/results",
        }), 202

    payload = request.get_json(silent=True) or {}
    target = payload.get("target", "base_station")
    if target not in TARGETS:
        abort(400, description="target must be base_station, drone, gcp, check_point, or all.")
    try:
        validate_job_inputs(job, target)
    except ValueError as exc:
        abort(400, description=str(exc))

    update_job(job_id, status="queued", error=None, stdout=None, stderr=None)
    thread = threading.Thread(target=run_pipeline_job, args=(job_id, target), daemon=True)
    thread.start()

    return jsonify({
        "job_id": job_id,
        "status": "queued",
        "target": target,
        "status_url": f"/api/jobs/{job_id}",
        "results_url": f"/api/jobs/{job_id}/results",
    }), 202


@app.get("/api/jobs")
def list_jobs():
    with get_jobs_db() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
    return jsonify({
        "count": len(rows),
        "jobs": [job_to_dict(row) for row in rows],
    })


@app.get("/api/jobs/<job_id>")
def get_job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        abort(404, description=f"Job '{job_id}' not found")
    return jsonify(job)


@app.get("/api/jobs/<job_id>/results")
def get_job_results(job_id: str):
    job = get_job(job_id)
    if not job:
        abort(404, description=f"Job '{job_id}' not found")
    if job["status"] != "completed":
        return jsonify({
            "job_id": job_id,
            "status": job["status"],
            "message": "Results are available after the job status is completed.",
            "error": job.get("error"),
        }), 202

    with get_db() as conn:
        tables = [
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
        ]

    return jsonify({
        "job_id": job_id,
        "status": job["status"],
        "database": str(DB_PATH),
        "tables": tables,
        "endpoints": {
            "base_station": "/api/base-station",
            "gcp": "/api/gcp",
            "drone": "/api/drone",
            "check_point": "/api/check-point",
        },
    })


@app.get("/api/base-station")
def base_station_health():
    return jsonify({
        "status": "ok",
        "database": str(DB_PATH),
        "namespace": "base_station",
    })


@app.get("/api/base-station/indicators")
@app.get("/api/indicators")
def list_base_station_indicators():
    """
    Returns all indicators as a flat list, frontend-ready.
    Each item has: id, name, score, band_matched, building_block_id,
                   gate_triggered, flags_raised, input_values, weight_in_block
    """
    raw = base_station_latest_indicators()
    indicators = [
        {
            "id":               v["indicator_id"],
            "name":             v["indicator_name"],
            "score":            v["score"],
            "band_matched":     v["band_matched"],
            "building_block":   v["building_block_id"],
            "gate_triggered":   v["gate_triggered"],
            "flags_raised":     v["flags_raised"],
            "input_values":     v["input_values"],
            "weight_in_block":  v["weight_in_block"],
            "condition":        v.get("condition_evaluated"),
        }
        for v in raw.values()
    ]
    return jsonify({"count": len(indicators), "indicators": indicators})


@app.get("/api/base-station/indicators/<indicator_id>")
@app.get("/api/indicators/<indicator_id>")
def get_base_station_indicator(indicator_id: str):
    """
    Returns a single indicator by its ID (e.g. L3I_BASE_001).
    """
    raw = base_station_latest_indicators()
    # support both "L3I_BASE_001" and "L3I_BASE_001_coverage_score"
    match = None
    for key, val in raw.items():
        if val["indicator_id"] == indicator_id or key == indicator_id:
            match = val
            break
    if not match:
        abort(404, description=f"Indicator '{indicator_id}' not found")
    return jsonify(match)


@app.get("/api/base-station/indicators/flags")
@app.get("/api/indicators/flags")
def get_base_station_flags():
    """Returns flags raised during stage 3b indicator evaluation."""
    return jsonify({"flags": base_station_latest_flags()})


@app.get("/api/base-station/indicators/meta")
@app.get("/api/indicators/meta")
def get_base_station_meta():
    """Returns stage 3b metadata (band counts, scoring summary)."""
    return jsonify(base_station_latest_meta())


@app.get("/api/gcp")
def gcp_health():
    return jsonify({
        "status": "ok",
        "database": str(DB_PATH),
        "namespace": "gcp",
    })


@app.get("/api/gcp/indicators")
def get_gcp_indicators():
    row = first_row("gcp_stage3_indicators")
    if not row:
        return jsonify({"count": 0, "indicators": []})

    if "points" in row.keys():
        points = json_or_raw(row["points"])
        return jsonify(points)

    return jsonify(dict(row))


@app.get("/api/gcp/indicators/<indicator_id>")
def get_gcp_indicator(indicator_id: str):
    with get_db() as conn:
        if not table_exists(conn, "gcp_stage3_indicators"):
            abort(404, description="GCP indicators table not found")
        row = conn.execute(
            """
            SELECT *
            FROM gcp_stage3_indicators
            WHERE indicator_id = ?
            """,
            (indicator_id,),
        ).fetchone()

    if not row:
        abort(404, description=f"Indicator '{indicator_id}' not found")

    return jsonify(dict(row))


@app.get("/api/gcp/building-blocks")
def get_gcp_building_blocks():
    row = first_row("gcp_stage3_building_blocks")
    return jsonify(dict(row) if row else {})


@app.get("/api/gcp/score")
def get_gcp_score():
    row = first_row("gcp_stage3_gcp_score")
    return jsonify(dict(row) if row else {})


@app.get("/api/gcp/flags")
def get_gcp_flags():
    row = first_row("gcp_stage3_gcp_score")
    if not row or "all_flags_aggregated" not in row.keys():
        return jsonify({"count": 0, "flags": []})

    flags = json_or_raw(row["all_flags_aggregated"])
    if not isinstance(flags, list):
        flags = []

    return jsonify({"count": len(flags), "flags": flags})


@app.get("/api/gcp/meta")
def get_gcp_meta():
    row = first_row("gcp_stage3_gcp_score")
    if not row:
        return jsonify({})

    return jsonify({
        "gcp_score": row["gcp_score"] if "gcp_score" in row.keys() else None,
        "weighted_score_before_global_gate": (
            row["weighted_score_before_global_gate"]
            if "weighted_score_before_global_gate" in row.keys()
            else None
        ),
        "global_gate_triggered": (
            row["global_gate__triggered"] if "global_gate__triggered" in row.keys() else None
        ),
        "critical_flags": (
            row["flags_by_severity__CRITICAL"] if "flags_by_severity__CRITICAL" in row.keys() else None
        ),
        "major_flags": (
            row["flags_by_severity__MAJOR"] if "flags_by_severity__MAJOR" in row.keys() else None
        ),
        "minor_flags": (
            row["flags_by_severity__MINOR"] if "flags_by_severity__MINOR" in row.keys() else None
        ),
    })


@app.get("/api/drone")
def drone_health():
    return jsonify({
        "status": "ok",
        "database": str(DB_PATH),
        "namespace": "drone",
    })


@app.get("/api/drone/indicators")
def get_drone_indicators():
    indicators = row_json_field("drone_stage3_indicators", "indicators", [])
    if not isinstance(indicators, list):
        indicators = []
    return jsonify({"count": len(indicators), "indicators": indicators})


@app.get("/api/drone/indicators/<indicator_id>")
def get_drone_indicator(indicator_id: str):
    indicators = row_json_field("drone_stage3_indicators", "indicators", [])
    if not isinstance(indicators, list):
        indicators = []
    for item in indicators:
        if item.get("indicator_id") == indicator_id or item.get("id") == indicator_id:
            return jsonify(item)
    abort(404, description=f"Indicator '{indicator_id}' not found")


@app.get("/api/drone/building-blocks")
def get_drone_building_blocks():
    return jsonify(row_dict("drone_stage3_building_blocks"))


@app.get("/api/drone/score")
def get_drone_score():
    return jsonify(row_dict("drone_stage3_drone_score"))


@app.get("/api/drone/flags")
def get_drone_flags():
    flags = row_json_field("drone_stage3_drone_score", "all_flags_aggregated", None)
    if flags is None:
        flags = row_json_field("drone_stage3_indicators", "flags_raised_stage3b", [])
    if not isinstance(flags, list):
        flags = []
    return jsonify({"count": len(flags), "flags": flags})


@app.get("/api/check-point")
def check_point_health():
    return jsonify({
        "status": "ok",
        "database": str(DB_PATH),
        "namespace": "check_point",
    })


@app.get("/api/check-point/indicators")
def get_check_point_indicators():
    data = envelope_data("check_point_stage3_indicators")
    points = data.get("points", [])
    if not isinstance(points, list):
        points = []
    return jsonify({"count": len(points), "points": points})


@app.get("/api/check-point/building-blocks")
def get_check_point_building_blocks():
    return jsonify(envelope_data("check_point_stage3_building_blocks"))


@app.get("/api/check-point/score")
def get_check_point_score():
    return jsonify(envelope_data("check_point_stage3_check_point_score"))


@app.get("/api/check-point/flags")
def get_check_point_flags():
    data = envelope_data("check_point_stage3_check_point_score")
    flags = data.get("all_flags_aggregated", [])
    if not isinstance(flags, list):
        flags = []
    return jsonify({"count": len(flags), "flags": flags})


# ── run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "").lower() in {"1", "true", "yes"},
    )
