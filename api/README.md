# MinerU-Popo FastAPI Service

FastAPI wrapper for the MinerU-Popo document post-processing pipeline with SQLite-based task queue.

## Installation

```bash
pip install -r api/requirements.txt
```

Make sure the main project dependencies are also installed:

```bash
pip install -r requirements.txt
```

## Prerequisites

No external services required. The task queue uses SQLite (built into Python) for persistence.

## Configuration

Set environment variables before starting:

```bash
# Model path (required for inference)
export POPO_MODEL_PATH=/path/to/Mineru-Popo

# SQLite database path (optional, defaults to ./data/popo_tasks.db)
export POPO_SQLITE_PATH=./data/popo_tasks.db

# Server settings (optional)
export POPO_API_HOST=0.0.0.0
export POPO_API_PORT=8000

# Worker settings (optional)
export POPO_WORKER_CONCURRENCY=4
export POPO_SYNC_TIMEOUT=300
export POPO_TASK_TTL=86400
```

## Running the Server

```bash
# Development (starts API server + background worker)
python -m api.main

# Production with uvicorn
uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 4

# Worker only (separate process)
python -c "from api.services.worker import run_worker; run_worker()"
```

On startup, the server automatically:
1. Initializes the SQLite database
2. Starts a background worker thread that processes tasks from the queue

## API Endpoints

### 1. Health Check

```
GET /health
```

Returns service status, database connection status, queue length, and active workers.

**Response:**
```json
{
  "status": "ok",
  "db_connected": true,
  "queue_length": 0,
  "workers_active": 1,
  "supported_models": ["mineru", "monkeyocr", "PaddleOCR-VL-1.5", "dolphin", "glm-ocr"]
}
```

### 2. Synchronous Processing

```
POST /process
Content-Type: multipart/form-data

- file: ZIP archive containing OCR output files
- model: OCR model name (mineru, monkeyocr, PaddleOCR-VL-1.5, dolphin, glm-ocr)
- doc_id: (optional) Document identifier, inferred from filename if not provided
```

Submits a ZIP with OCR output, **waits for the full pipeline to finish**, and returns the final document tree in the same response. Suitable for small documents.

**Example:**
```bash
curl -X POST http://localhost:8000/process \
  -F "file=@ocr_output.zip" \
  -F "model=mineru" \
  -F "doc_id=my_document"
```

**Response:**
```json
{
  "doc_id": "my_document",
  "status": "success",
  "message": "Document processed successfully",
  "tree": { ... }
}
```

### 3. Submit Async Task

```
POST /tasks
Content-Type: multipart/form-data

- file: ZIP archive containing OCR output files
- model: OCR model name
- doc_id: (optional) Document identifier
```

Uploads a ZIP with OCR output and **returns immediately** with a task ID. The task is stored in SQLite and processed by a background worker.

**Example:**
```bash
curl -X POST http://localhost:8000/tasks \
  -F "file=@ocr_output.zip" \
  -F "model=mineru"
```

**Response (202 Accepted):**
```json
{
  "task_id": "a1b2c3d4e5f6",
  "status": "pending",
  "message": "Task submitted successfully"
}
```

### 4. Get Task Status

```
GET /tasks/{task_id}
```

Returns the current status of an async task.

**Example:**
```bash
curl http://localhost:8000/tasks/a1b2c3d4e5f6
```

**Response:**
```json
{
  "task_id": "a1b2c3d4e5f6",
  "status": "processing",
  "progress": "Running inference...",
  "created_at": "2024-01-01T00:00:00",
  "updated_at": "2024-01-01T00:05:00",
  "doc_id": "my_document",
  "model": "mineru",
  "error": null
}
```

**Status values:** `pending`, `processing`, `completed`, `failed`

### 5. Get Task Result

```
GET /tasks/{task_id}/result
```

Returns the final processing result (document tree). If the task is still pending or processing, returns the current status.

**Example:**
```bash
curl http://localhost:8000/tasks/a1b2c3d4e5f6/result
```

**Response (completed):**
```json
{
  "task_id": "a1b2c3d4e5f6",
  "status": "completed",
  "result": {
    "doc_id": "my_document",
    "status": "success",
    "message": "Document processed successfully",
    "tree": { ... }
  },
  "error": null
}
```

**Response (still processing):**
```json
{
  "task_id": "a1b2c3d4e5f6",
  "status": "processing",
  "error": "Task is still processing"
}
```

### 6. JSON Input (Synchronous)

```
POST /process/json
Content-Type: application/json
```

Submit already-normalized pages data directly for inference and tree building.

**Example:**
```bash
curl -X POST http://localhost:8000/process/json \
  -H "Content-Type: application/json" \
  -d '{
    "doc_id": "mydoc",
    "model": "mineru",
    "pages": {
      "1": [
        {"type": "title", "content": "Chapter 1", "bbox": [0.1, 0.1, 0.5, 0.15]}
      ]
    }
  }'
```

## ZIP File Structure

The ZIP file should contain OCR output files in the format expected by the specified model:

| Model | ZIP Contents |
|-------|-------------|
| `mineru` | `{doc_id}_model.json` or `{doc_id}_middle.json` or `{doc_id}_content_list.json` |
| `monkeyocr` | `{doc_id}_middle.json` |
| `PaddleOCR-VL-1.5` | `layout_parsing.json` or `{doc_id}_*_res.json` files |
| `dolphin` | `recognition_json/{doc_id}.json` |
| `glm-ocr` | `{doc_id}_model.json` or `page_*.json` files |

## Architecture

```
Client → FastAPI → SQLite Queue → Worker → SQLite Result
     │            │              │
     │         Task Status    Processing
     │         Task Result    Pipeline
     │
     └→ Sync Response (for /process)
```

## Database Schema

The SQLite database (`popo_tasks.db`) contains a single `tasks` table:

```sql
CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    progress TEXT DEFAULT '',
    file_name TEXT DEFAULT '',
    work_dir TEXT DEFAULT '',
    result TEXT DEFAULT '',       -- JSON string of the processing result
    error TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

You can inspect the database directly:
```bash
sqlite3 data/popo_tasks.db "SELECT task_id, status, progress FROM tasks;"
```

## Production Deployment

For production use, consider:

1. **Separate Worker Processes**: Run workers as separate processes:
   ```bash
   python -c "from api.services.worker import run_worker; run_worker('worker-1')"
   ```

2. **Task Cleanup**: Old completed/failed tasks are automatically cleaned up based on `POPO_TASK_TTL`

3. **Database Backup**: The SQLite file is a single file, easy to backup:
   ```bash
   cp data/popo_tasks.db data/popo_tasks.db.backup
   ```

4. **Containerization**: Docker with GPU support for model inference