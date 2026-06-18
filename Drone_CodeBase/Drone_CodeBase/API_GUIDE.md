# Drone Pipeline API

REST API for processing drone survey data and retrieving results.

## Starting the Server

```powershell
.\.venv\Scripts\python.exe scripts\api_server.py
```

Server runs on `http://localhost:5000`

## API Endpoints

### Health Check
```
GET /api/health
```
Response:
```json
{
  "status": "ok",
  "timestamp": "2026-06-06T10:30:45.123456"
}
```

### Process Survey (Upload & Run)
```
POST /api/process
Content-Type: multipart/form-data

Form data:
  - survey_id: string (required, unique identifier)
  - files: multipart (required, must include paths.json)
```

Example with curl:
```bash
curl -X POST http://localhost:5000/api/process \
  -F "survey_id=test_survey_001" \
  -F "paths.json=@paths.json"
```

Response (201):
```json
{
  "survey_id": "test_survey_001",
  "status": "completed",
  "database": "databases/test_survey_001.db",
  "timestamp": "2026-06-06T10:30:45.123456"
}
```

### List All Surveys
```
GET /api/results
```
Response:
```json
{
  "surveys": [
    {
      "survey_id": "test_survey_001",
      "db_file": "test_survey_001.db",
      "size_bytes": 1048576
    }
  ],
  "count": 1
}
```

### Get Survey Metadata
```
GET /api/results/<survey_id>
```
Response:
```json
{
  "survey_id": "test_survey_001",
  "db_file": "/path/to/test_survey_001.db",
  "tables": [
    "t_01_inventory",
    "t_02_source_fields",
    "t_03_derived_fields",
    "t_04_indicators",
    "t_05_building_blocks",
    "t_05b_cal_conf",
    "t_06_drone_score"
  ],
  "table_count": 7
}
```

### Query Survey Table
```
GET /api/results/<survey_id>/<table>
```

Query parameters:
- `path_filter` (optional): LIKE pattern for JSON paths (e.g., `data.L1F_IMG_%`)
- `limit` (optional): max rows (default 100, max 1000)

Example:
```
GET /api/results/test_survey_001/t_02_source_fields?path_filter=data.L1F_API_%&limit=10
```

Response:
```json
{
  "survey_id": "test_survey_001",
  "table": "t_02_source_fields",
  "rows": [
    {
      "path": "data.L1F_API_001",
      "value": "7.0"
    }
  ],
  "count": 1,
  "limit": 10
}
```

## Using the Test Client

```powershell
# Health check
.\.venv\Scripts\python.exe scripts\api_client.py health

# List surveys
.\.venv\Scripts\python.exe scripts\api_client.py list

# Get survey info
.\.venv\Scripts\python.exe scripts\api_client.py info test_survey_001

# Query table
.\.venv\Scripts\python.exe scripts\api_client.py query test_survey_001 t_02_source_fields "data.L1F_API_%"
```

## Directory Structure

```
├── scripts/
│   ├── api_server.py           # Flask API server
│   ├── api_client.py           # Test client
│   ├── db_sqlite.py            # Database loader
│   └── run_pipeline.py         # Main pipeline
├── uploads/
│   └── <survey_id>/            # Uploaded files and outputs
└── databases/
    └── <survey_id>.db          # SQLite database per survey
```

## Workflow

1. **Upload & Process**: POST `/api/process` with survey files → runs pipeline → loads to SQLite
2. **List Results**: GET `/api/results` → see all processed surveys
3. **Query Results**: GET `/api/results/<id>/<table>` → retrieve specific data

## Notes

- Each survey gets a unique database file: `<survey_id>.db`
- Pipeline output files are stored in `uploads/<survey_id>/outputs/`
- Maximum query limit is 1000 rows
- All timestamps are in UTC
