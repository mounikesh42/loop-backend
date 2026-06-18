#!/usr/bin/env python3
"""Test client for the drone pipeline API."""
import json
import requests
from pathlib import Path


BASE_URL = "http://localhost:5000/api"


def health_check():
    """Test health endpoint."""
    resp = requests.get(f"{BASE_URL}/health")
    print("Health:", resp.json())


def upload_and_process(survey_id: str, paths_json_path: Path):
    """Upload and process a survey."""
    files = {}
    
    # Prepare paths.json
    with open(paths_json_path) as f:
        paths_data = json.load(f)
    
    # Upload paths.json
    files["paths.json"] = (
        "paths.json",
        json.dumps(paths_data),
        "application/json",
    )
    
    data = {"survey_id": survey_id}
    resp = requests.post(f"{BASE_URL}/process", files=files, data=data)
    print(f"Process result ({resp.status_code}):", resp.json())
    return resp.json() if resp.status_code == 201 else None


def list_surveys():
    """List all surveys."""
    resp = requests.get(f"{BASE_URL}/results")
    print("Surveys:", resp.json())


def get_survey_info(survey_id: str):
    """Get survey metadata."""
    resp = requests.get(f"{BASE_URL}/results/{survey_id}")
    print(f"Survey {survey_id}:", resp.json())


def query_table(survey_id: str, table: str, path_filter: str = None, limit: int = 10):
    """Query a table."""
    params = {"limit": limit}
    if path_filter:
        params["path_filter"] = path_filter
    
    # For backwards compatibility: if table doesn't have prefix, add it
    if not table.startswith(f"{survey_id}__"):
        table = f"{survey_id}__{table}"
    
    resp = requests.get(f"{BASE_URL}/results/{survey_id}/{table}", params=params)
    result = resp.json()
    print(f"Query {survey_id}/{table}:")
    if "rows" in result:
        for row in result["rows"]:
            print(f"  {row['path']}: {row['value']}")
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python api_client.py health")
        print("  python api_client.py list")
        print("  python api_client.py info <survey_id>")
        print("  python api_client.py query <survey_id> <table> [path_filter]")
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    try:
        if cmd == "health":
            health_check()
        elif cmd == "list":
            list_surveys()
        elif cmd == "info":
            get_survey_info(sys.argv[2])
        elif cmd == "query":
            survey_id = sys.argv[2]
            table = sys.argv[3]
            path_filter = sys.argv[4] if len(sys.argv) > 4 else None
            query_table(survey_id, table, path_filter)
    except requests.exceptions.ConnectionError:
        print("Error: Could not connect to API. Is the server running?")
        sys.exit(1)
    except Exception as exc:
        print(f"Error: {exc}")
        sys.exit(1)
