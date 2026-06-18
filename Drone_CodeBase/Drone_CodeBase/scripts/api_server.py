#!/usr/bin/env python3
"""REST API for drone pipeline processing and result retrieval.

Endpoints:
  POST   /api/process              — upload and process survey data
  GET    /api/results              — list all surveys
  GET    /api/results/<survey_id>  — get survey metadata
  GET    /api/results/<survey_id>/<table> — query specific table
  GET    /api/health               — health check
"""
import json
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request

app = Flask(__name__)
BASE_DIR = Path(__file__).parent.parent
UPLOADS_DIR = BASE_DIR / "uploads"
DB_FILE = BASE_DIR / "pipeline_results.db"
UPLOADS_DIR.mkdir(exist_ok=True)


def run_pipeline(survey_dir: Path) -> dict:
    """Run the pipeline on a survey directory."""
    try:
        paths_json = survey_dir / "paths.json"
        if not paths_json.exists():
            return {"error": f"paths.json not found in {survey_dir}"}

        result = subprocess.run(
            [str(BASE_DIR / ".venv" / "Scripts" / "python.exe"), 
             str(BASE_DIR / "scripts" / "run_pipeline.py"),
             str(paths_json)],
            cwd=str(survey_dir),
            capture_output=True,
            text=True,
            timeout=3600,
        )
        if result.returncode != 0:
            return {"error": f"Pipeline failed: {result.stderr}"}
        return {"success": True, "stdout": result.stdout}
    except Exception as exc:
        return {"error": str(exc)}


def load_to_database(survey_id: str, outputs_dir: Path) -> dict:
    """Load outputs JSON to SQLite (one database for all surveys)."""
    try:
        conn = sqlite3.connect(str(DB_FILE))
        conn.execute("PRAGMA journal_mode=WAL")
        
        from db_sqlite import flatten_json, sanitize_table_name
        
        table_count = 0
        for json_path in sorted(outputs_dir.glob("*.json")):
            base_table = sanitize_table_name(json_path.stem)
            # Create table with survey_id prefix: survey_001__01_inventory
            table_name = f"{survey_id}__{base_table}"
            
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {table_name} (path TEXT PRIMARY KEY, value TEXT)"
            )
            with json_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            rows = list(flatten_json(data))
            conn.executemany(
                f"INSERT INTO {table_name} (path, value) VALUES (?, ?)",
                rows,
            )
            table_count += 1
        
        conn.commit()
        conn.close()
        return {"success": True, "db_path": str(DB_FILE), "tables": table_count}
    except Exception as exc:
        return {"error": str(exc)}


@app.route("/api/health", methods=["GET"])
def health():
    """Health check."""
    return jsonify({"status": "ok", "timestamp": datetime.utcnow().isoformat()})


@app.route("/api/process", methods=["POST"])
def process_survey():
    """Upload and process survey data.
    
    Expected: multipart/form-data with files and paths.json
    Returns: survey_id and processing status
    """
    try:
        if "survey_id" not in request.form:
            return jsonify({"error": "survey_id required in form"}), 400

        survey_id = request.form["survey_id"]
        survey_dir = UPLOADS_DIR / survey_id
        survey_dir.mkdir(exist_ok=True)

        # Save uploaded files
        for key in request.files:
            file = request.files[key]
            if file and file.filename:
                file_path = survey_dir / file.filename
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file.save(str(file_path))

        # Ensure paths.json exists
        if not (survey_dir / "paths.json").exists():
            return jsonify({"error": "paths.json not uploaded"}), 400

        # Run pipeline
        pipeline_result = run_pipeline(survey_dir)
        if "error" in pipeline_result:
            return jsonify(pipeline_result), 500

        # Load to database
        outputs_dir = survey_dir / "outputs"
        if not outputs_dir.exists():
            return jsonify({"error": "Pipeline did not generate outputs"}), 500

        db_result = load_to_database(survey_id, outputs_dir)
        if "error" in db_result:
            return jsonify(db_result), 500

        return jsonify({
            "survey_id": survey_id,
            "status": "completed",
            "database": str(DB_FILE),
            "timestamp": datetime.utcnow().isoformat(),
        }), 201

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/results", methods=["GET"])
def list_surveys():
    """List all completed surveys."""
    try:
        if not DB_FILE.exists():
            return jsonify({"surveys": [], "count": 0})
        
        conn = sqlite3.connect(str(DB_FILE))
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor]
        conn.close()
        
        # Extract unique survey_ids from table names (format: survey_id__table_name)
        survey_ids = set()
        for table in tables:
            if "__" in table:
                survey_id = table.split("__")[0]
                survey_ids.add(survey_id)
        
        surveys = [{"survey_id": sid} for sid in sorted(survey_ids)]
        return jsonify({"surveys": surveys, "count": len(surveys)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/results/<survey_id>", methods=["GET"])
def get_survey_metadata(survey_id: str):
    """Get survey metadata and available tables."""
    try:
        if not DB_FILE.exists():
            return jsonify({"error": f"Survey {survey_id} not found"}), 404

        conn = sqlite3.connect(str(DB_FILE))
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        all_tables = [row[0] for row in cursor]
        conn.close()

        # Filter tables for this survey_id (format: survey_id__table_name)
        tables = [t for t in all_tables if t.startswith(f"{survey_id}__")]
        
        if not tables:
            return jsonify({"error": f"Survey {survey_id} not found"}), 404

        return jsonify({
            "survey_id": survey_id,
            "database": str(DB_FILE),
            "tables": tables,
            "table_count": len(tables),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/results/<survey_id>/<table>", methods=["GET"])
def query_survey_table(survey_id: str, table: str):
    """Query a specific table from a survey.
    
    Query params:
      - path_filter: LIKE pattern for JSON paths (e.g., 'data.L1F_IMG_%')
      - limit: max rows (default 100)
    """
    try:
        if not DB_FILE.exists():
            return jsonify({"error": f"Database not found"}), 404

        # Build full table name with survey_id prefix
        full_table_name = f"{survey_id}__{table}"
        
        path_filter = request.args.get("path_filter", "")
        limit = min(int(request.args.get("limit", 100)), 1000)

        conn = sqlite3.connect(str(DB_FILE))
        if path_filter:
            cursor = conn.execute(
                f"SELECT path, value FROM {full_table_name} WHERE path LIKE ? ORDER BY path LIMIT ?",
                (path_filter, limit),
            )
        else:
            cursor = conn.execute(
                f"SELECT path, value FROM {full_table_name} ORDER BY path LIMIT ?",
                (limit,),
            )

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return jsonify({"survey_id": survey_id, "table": table, "rows": [], "count": 0})

        result_rows = [{"path": path, "value": value} for path, value in rows]
        return jsonify({
            "survey_id": survey_id,
            "table": table,
            "rows": result_rows,
            "count": len(result_rows),
            "limit": limit,
        })

    except sqlite3.OperationalError as exc:
        return jsonify({"error": f"Table {table} not found: {exc}"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found"}), 404


@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
