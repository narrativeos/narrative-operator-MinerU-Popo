# MinerU-Popo FastAPI Service

FastAPI wrapper for the MinerU-Popo document post-processing pipeline.

## Installation

```bash
pip install -r api/requirements.txt
```

Make sure the main project dependencies are also installed:

```bash
pip install -r requirements.txt
```

## Configuration

Set environment variables before starting:

```bash
# Model path (required for inference)
export POPO_MODEL_PATH=/path/to/Mineru-Popo

# Server settings (optional)
export POPO_API_HOST=0.0.0.0
export POPO_API_PORT=8000
```

## Running the Server

```bash
# Development
python -m api.main

# Production with uvicorn
uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 4
```

## API Endpoints

### Health Check

```
GET /health
```

Returns service status and supported models.

### Process OCR Output (ZIP Upload)

```
POST /process
Content-Type: multipart/form-data

- file: ZIP archive containing OCR output files
- model: OCR model name (mineru, monkeyocr, PaddleOCR-VL-1.5, dolphin, glm-ocr)
- doc_id: (optional) Document identifier, inferred from filename if not provided
```

**Example:**

```bash
curl -X POST http://localhost:8000/process \
  -F "file=@ocr_output.zip" \
  -F "model=mineru" \
  -F "doc_id=my_document"
```

**ZIP Structure (MinerU example):**

```
my_document_model.json
```

or

```
vlm/
  my_document_model.json
  my_document_middle.json
```

### Process OCR Output (JSON Input)

```
POST /process/json
Content-Type: application/json

{
  "doc_id": "my_document",
  "model": "mineru",
  "pages": {
    "1": [
      {
        "type": "title",
        "content": "Chapter 1",
        "bbox": [0.1, 0.1, 0.5, 0.15],
        "source_id": "doc:0"
      }
    ]
  }
}
```

### Async Task Processing

For long documents, use async endpoints:

```
POST /tasks           # Create async task, returns task_id
GET  /tasks/{task_id} # Check task status and result
```

**Example:**

```bash
# Create task
curl -X POST http://localhost:8000/tasks \
  -F "file=@large_doc.zip" \
  -F "model=mineru"

# Check status
curl http://localhost:8000/tasks/<task_id>
```

## Response Format

```json
{
  "doc_id": "my_document",
  "status": "success",
  "message": "Document processed successfully",
  "tree": {
    "type": "root",
    "title": "",
    "content": "",
    "level": 0,
    "location": [],
    "block_ids": [],
    "children": [
      {
        "type": "text",
        "title": "Chapter 1",
        "content": "Chapter 1",
        "level": 1,
        "location": [
          {
            "bbox": [0.1, 0.1, 0.5, 0.15],
            "page": 1
          }
        ],
        "block_ids": [0],
        "children": []
      }
    ]
  }
}
```

## Supported OCR Models

| Model | ZIP Contents |
|-------|-------------|
| `mineru` | `{doc_id}_model.json` or `{doc_id}_middle.json` or `{doc_id}_content_list.json` |
| `monkeyocr` | `{doc_id}_middle.json` |
| `PaddleOCR-VL-1.5` | `layout_parsing.json` or `{doc_id}_*_res.json` files |
| `dolphin` | `recognition_json/{doc_id}.json` |
| `glm-ocr` | `{doc_id}_model.json` or `page_*.json` files |

## Production Deployment

For production use, consider:

1. **Task Queue**: Replace in-memory task store with Redis + Celery
2. **File Storage**: Use cloud storage (S3, OSS) instead of temp files
3. **Reverse Proxy**: Nginx for load balancing and static file serving
4. **Containerization**: Docker with GPU support for model inference