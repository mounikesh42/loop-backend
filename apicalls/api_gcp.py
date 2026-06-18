
import sqlite3
import json
from pathlib import Path
from flask import Flask, jsonify, abort
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DB_PATH = Path(__file__).parent / "pipeline.db"


# ------------------------------------------------------------------
# Database
# ------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ------------------------------------------------------------------
# Indicators
# ------------------------------------------------------------------

@app.get("/api/indicators")
def get_indicators():

    with get_db() as conn:
        row = conn.execute(
            "SELECT points FROM gcp_stage3_indicators LIMIT 1"
        ).fetchone()

    if not row:
        return jsonify({
            "count": 0,
            "indicators": []
        })

    try:
        points = json.loads(row["points"])
    except Exception:
        points = row["points"]

    return jsonify(points)

@app.get("/api/indicators/<indicator_id>")
def get_indicator(indicator_id):

    with get_db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM gcp_stage3_indicators
            WHERE indicator_id = ?
            """,
            (indicator_id,)
        ).fetchone()

    if not row:
        abort(404, description=f"Indicator '{indicator_id}' not found")

    return jsonify(dict(row))


# ------------------------------------------------------------------
# Building Blocks
# ------------------------------------------------------------------

@app.get("/api/building-blocks")
def get_building_blocks():

    with get_db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM gcp_stage3_building_blocks
            LIMIT 1
            """
        ).fetchone()

    if not row:
        return jsonify({})

    return jsonify(dict(row))


# ------------------------------------------------------------------
# Final GCP Score
# ------------------------------------------------------------------

@app.get("/api/gcp-score")
def get_gcp_score():

    with get_db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM gcp_stage3_gcp_score
            LIMIT 1
            """
        ).fetchone()

    if not row:
        return jsonify({})

    return jsonify(dict(row))


# ------------------------------------------------------------------
# Flags
# ------------------------------------------------------------------

@app.get("/api/flags")
def get_flags():

    with get_db() as conn:
        row = conn.execute(
            """
            SELECT all_flags_aggregated
            FROM gcp_stage3_gcp_score
            LIMIT 1
            """
        ).fetchone()

    if not row:
        return jsonify({
            "count": 0,
            "flags": []
        })

    try:
        flags = json.loads(row["all_flags_aggregated"])
    except Exception:
        flags = []

    return jsonify({
        "count": len(flags),
        "flags": flags
    })


# ------------------------------------------------------------------
# Metadata
# ------------------------------------------------------------------

@app.get("/api/meta")
def get_meta():

    with get_db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM gcp_stage3_gcp_score
            LIMIT 1
            """
        ).fetchone()

    if not row:
        return jsonify({})

    return jsonify({
        "gcp_score": row["gcp_score"],
        "weighted_score_before_global_gate":
            row["weighted_score_before_global_gate"],
        "global_gate_triggered":
            row["global_gate__triggered"],
        "critical_flags":
            row["flags_by_severity__CRITICAL"],
        "major_flags":
            row["flags_by_severity__MAJOR"],
        "minor_flags":
            row["flags_by_severity__MINOR"]
    })


# ------------------------------------------------------------------
# Health
# ------------------------------------------------------------------

@app.get("/")
def health():
    return jsonify({
        "status": "ok",
        "database": str(DB_PATH)
    })


# ------------------------------------------------------------------
# Run
# ------------------------------------------------------------------

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )
