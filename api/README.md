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

Returns service status, database connectivity, queue length, and active worker count.

**Example:**
```bash
curl -s http://localhost:8440/health | python -m json.tool
```

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

---

### 2. Synchronous Processing

```
POST /process
Content-Type: multipart/form-data
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | File | Yes | ZIP archive containing OCR output |
| `model` | String | Yes | OCR model: `mineru`, `monkeyocr`, `PaddleOCR-VL-1.5`, `dolphin`, `glm-ocr` |
| `doc_id` | String | No | Document ID, auto-detected from ZIP structure if omitted |

Uploads a ZIP, **blocks until the full pipeline completes**, then returns the document tree.

> 适合小文档或调试。大文档建议用异步接口 `POST /tasks`。

**Example:**
```bash
curl -X POST http://localhost:8440/process \
  -F "file=@page_2_mineru.zip" \
  -F "model=mineru" \
  -F "doc_id=page_2" \
  -o result.json
```

**Response (200):**
```json
{
  "doc_id": "page_2",
  "status": "success",
  "message": "Document processed successfully",
  "tree": {
    "type": "root",
    "title": "",
    "level": 0,
    "children": [
      {
        "type": "text",
        "title": "Default Title",
        "level": 1,
        "content": "...<|txt_split|>...",
        "location": [{"bbox": [0.196, 0.866, 0.298, 0.91], "page": 1}],
        "block_ids": [1, 2, 3]
      },
      {
        "type": "page_number",
        "title": "Page 4 - page_number",
        "content": "006"
      }
    ]
  }
}
```

---

### 3. Submit Async Task (POST /tasks)

```
POST /tasks
Content-Type: multipart/form-data
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | File | Yes | ZIP archive containing OCR output |
| `model` | String | Yes | OCR model name |
| `doc_id` | String | No | Document ID, auto-detected if omitted |

Uploads a ZIP and **returns immediately** with a `task_id`. The task is queued in SQLite and processed by a background worker.

**Example:**
```bash
curl -X POST http://localhost:8440/tasks \
  -F "file=@page_2_mineru.zip" \
  -F "model=mineru" \
  -F "doc_id=page_2"
```

**Response (202 Accepted):**
```json
{
  "task_id": "13a6fe195a844709",
  "status": "pending",
  "message": "Task submitted successfully"
}
```

---

### 4. Query Task Status & Progress (GET /tasks/{task_id})

```
GET /tasks/{task_id}
```

Returns the current status and progress of an async task. **Poll this endpoint** to track real-time progress.

**Example:**
```bash
# Single query
curl -s http://localhost:8440/tasks/13a6fe195a844709 | python -m json.tool

# Polling loop (bash)
TASK_ID="13a6fe195a844709"
while true; do
  RESP=$(curl -s "http://localhost:8440/tasks/$TASK_ID")
  echo "$RESP" | python -c "import sys,json; d=json.load(sys.stdin); print(d['status'], d['progress'])"
  st=$(echo "$RESP" | python -c "import sys,json; print(json.load(sys.stdin)['status'])")
  [ "$st" = "completed" ] || [ "$st" = "failed" ] && break
  sleep 5
done
```

**Response (processing):**
```json
{
  "task_id": "13a6fe195a844709",
  "status": "processing",
  "progress": "[60%] Image-text association (1 chunks)",
  "created_at": "2026-07-02T15:10:37",
  "updated_at": "2026-07-02T15:13:00",
  "doc_id": "page_2",
  "model": "mineru",
  "error": null
}
```

**Response (completed):**
```json
{
  "task_id": "13a6fe195a844709",
  "status": "completed",
  "progress": "[100%] Processing completed (4 pages)",
  "created_at": "2026-07-02T15:10:37",
  "updated_at": "2026-07-02T15:14:00",
  "doc_id": "page_2",
  "model": "mineru",
  "error": null
}
```

#### Progress Lifecycle

| 百分比 | progress 示例 | 阶段 | 耗时特征 |
|--------|-------------|------|---------|
| — | `Task queued` | 入队等待 | 瞬间 |
| `[5%]` | `Normalizing labels...` | 标签归一化 | 1-2s |
| `[15%]` | `Labels normalized (4 pages), starting inference...` | 归一化完成 | 可观测 |
| `[20%]` | `Text truncation analysis (3 chunks)` | 文本截断分析 | 取决于 chunk 数 |
| `[40%]` | `Title hierarchy analysis (2 chunks)` | 标题层级分析 | 取决于 chunk 数 |
| `[60%]` | `Image-text association (1 chunks)` | 图文关联分析 | **最耗时阶段** |
| `[75%]` | `Image-text association complete` | 关联完成 | 瞬间 |
| `[85%]` | `Inference done (4 pages, 48 elements), building tree...` | 推理完成 | 可观测 |
| `[95%]` | `Saving result...` | 构建树 + 保存 | 瞬间 |
| `[100%]` | `Processing completed (4 pages)` | 完成 | 终态 |

> **注意**：快速阶段（<5s）可能在轮询间隔内被跳过，属于正常现象。

**Status 状态机：**
```
pending → processing → completed
                     → failed
```

---

### 5. Get Task Result (GET /tasks/{task_id}/result)

```
GET /tasks/{task_id}/result
```

Returns the final document tree. Only available after the task reaches `completed` status.

**Example:**
```bash
curl -s http://localhost:8440/tasks/13a6fe195a844709/result | python -m json.tool
```

**Response (completed):**
```json
{
  "task_id": "13a6fe195a844709",
  "status": "completed",
  "result": {
    "doc_id": "page_2",
    "status": "success",
    "message": "Document processed successfully",
    "tree": {
      "type": "root",
      "level": 0,
      "children": [
        {
          "type": "text",
          "title": "Default Title",
          "content": "京沪高铁 ↑ 股票代码 601816<|txt_split|>秋意漫卷...",
          "level": 1,
          "location": [{"bbox": [0.196, 0.866, 0.298, 0.91], "page": 1}],
          "block_ids": [1, 2, 3]
        }
      ]
    }
  },
  "error": null
}
```

**Response (still processing):**
```json
{
  "task_id": "13a6fe195a844709",
  "status": "processing",
  "error": "Task is still processing"
}
```

**Response (pending):**
```json
{
  "task_id": "13a6fe195a844709",
  "status": "pending",
  "error": "Task is still pending"
}
```

---

### 6. JSON Input (Synchronous)

```
POST /process/json
Content-Type: application/json
```

Submit pre-normalized pages data directly (skip label normalization step).

**Example:**
```bash
curl -X POST http://localhost:8440/process/json \
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

---

## Document Tree Node Structure

Each node in the `tree` has 8 fields:

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Node type: `root`, `text`, `table`, `image`, `page_number`, `header`, `footer`, ... |
| `title` | string | Section title, or `"Default Title"` if none detected |
| `metadata` | string | Footnotes, supplementary info |
| `content` | string | Body text, segments separated by `<\|txt_split\|>` or `<\|txt_contd\|>` |
| `level` | int | Heading level (1=H1, 2=H2, ...), -1 for non-headings |
| `location` | array | `[{bbox: [x1,y1,x2,y2], page: N}]` — normalized coordinates (0-1) |
| `block_ids` | array | Traceable back to original OCR output block IDs |
| `children` | array | Recursive child nodes of the same structure |

## ZIP File Structure

The ZIP should contain OCR output in the format expected by each model. The `doc_id` is auto-detected from the top-level directory inside the ZIP (falls back to ZIP filename stem).

### mineru

Newer MinerU uses `hybrid_auto/`, older versions use `vlm/`. Both are auto-detected.

```
{zip_root}/
└── {doc_id}/
    └── hybrid_auto/          ← or vlm/ (auto-detected)
        ├── {doc_id}_model.json     ← preferred
        ├── {doc_id}_middle.json    ← fallback
        ├── {doc_id}_content_list.json
        ├── {doc_id}_origin.pdf     ← VLM page rendering (optional)
        ├── {doc_id}_layout.pdf
        └── images/
            └── *.jpg
```

**Example** (what we tested):
```
page_2_mineru.zip
└── page_2/
    └── hybrid_auto/
        ├── page_2_model.json
        ├── page_2_middle.json
        ├── page_2_content_list.json
        ├── page_2_origin.pdf
        ├── page_2_layout.pdf
        └── images/ (27 jpg files)
```

### Other Models

| Model | Key Files |
|-------|----------|
| `monkeyocr` | `{doc_id}_middle.json` |
| `PaddleOCR-VL-1.5` | `layout_parsing.json` or `{doc_id}_*_res.json` |
| `dolphin` | `recognition_json/{doc_id}.json` |
| `glm-ocr` | `{doc_id}_model.json` or `page_*.json` |

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