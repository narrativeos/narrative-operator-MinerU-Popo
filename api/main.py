"""FastAPI application for MinerU-Popo post-processing service with Redis queue."""

import json
import os
import shutil
import sys
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from api.config import (
    SUPPORTED_MODELS,
    get_temp_dir,
    HOST,
    PORT,
    SYNC_TIMEOUT,
)
from api.models import (
    HealthResponse,
    ProcessRequest,
    ProcessResponse,
    TaskCreateResponse,
    TaskResultResponse,
    TaskStatusResponse,
    TreeNode,
)
from api.services.queue import (
    create_task,
    get_active_workers,
    get_queue_length,
    get_task_result,
    get_task_status,
    is_redis_connected,
    pop_task,
    save_task_result,
    update_task_status,
)


app = FastAPI(
    title="MinerU-Popo API",
    description="Universal Post-Processing Model for Structured Document Parsing",
    version="1.0.0",
)


def _process_pipeline(doc_id: str, model_name: str, extract_dir: Path) -> Dict[str, Any]:
    """
    Run the full processing pipeline: normalize -> infer -> build tree.

    Returns the final document tree dict.
    """
    # Ensure post_processing is in path
    post_processing_dir = PROJECT_ROOT / "post_processing"
    data_engine_dir = PROJECT_ROOT / "data_engine"

    for d in [str(post_processing_dir), str(data_engine_dir)]:
        if d not in sys.path:
            sys.path.insert(0, d)

    # Step 1: Normalize labels
    from api.services.normalize import normalize_ocr_output

    normalize_dir = extract_dir / "normalized"
    pages = normalize_ocr_output(model_name, str(extract_dir), str(normalize_dir), doc_id)

    # Step 2: Run inference
    from api.services.infer import run_inference

    infer_dir = extract_dir / "inferred"
    elements = run_inference(doc_id, pages, str(infer_dir))

    # Step 3: Build tree
    from api.services.tree_builder import build_tree

    tree_dir = extract_dir / "tree"
    txt_dir = extract_dir / "txt"
    tree = build_tree(elements, str(tree_dir), str(txt_dir), doc_id)

    return tree


def _extract_and_prepare(file: UploadFile, doc_id: Optional[str] = None) -> tuple:
    """
    Upload file, extract zip, return (task_id, work_dir, extract_dir, final_doc_id).
    """
    task_id = uuid.uuid4().hex[:16]

    if not doc_id:
        doc_id = Path(file.filename).stem
        for suffix in ["_model", "_middle", "_content_list"]:
            if doc_id.endswith(suffix):
                doc_id = doc_id[: -len(suffix)]

    work_dir = get_temp_dir() / task_id
    work_dir.mkdir(parents=True, exist_ok=True)

    # Save and extract ZIP
    zip_path = work_dir / file.filename
    content = file.file.read()
    zip_path.write_bytes(content)

    extract_dir = work_dir / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)

    return task_id, work_dir, extract_dir, doc_id


# ========================
# Health Check
# ========================

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    redis_ok = is_redis_connected()
    queue_len = get_queue_length() if redis_ok else 0
    workers = get_active_workers() if redis_ok else 0

    return HealthResponse(
        status="ok" if redis_ok else "degraded",
        redis_connected=redis_ok,
        queue_length=queue_len,
        workers_active=workers,
    )


# ========================
# Synchronous Processing
# ========================

@app.post("/process")
async def process_ocr_zip(
    file: UploadFile = File(..., description="ZIP file containing OCR output files"),
    model: str = Form(..., description="OCR model name: mineru, monkeyocr, PaddleOCR-VL-1.5, dolphin, glm-ocr"),
    doc_id: str = Form(None, description="Optional document ID, inferred from filename if not provided"),
):
    """
    Synchronously parse uploaded files.

    Submit a ZIP with OCR output, wait for the full pipeline to finish,
    and return the final document tree in the same response.
    """
    if model not in SUPPORTED_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported model: {model}. Supported: {SUPPORTED_MODELS}",
        )

    task_id, work_dir, extract_dir, final_doc_id = _extract_and_prepare(file, doc_id)

    try:
        tree = _process_pipeline(final_doc_id, model, extract_dir)

        return ProcessResponse(
            doc_id=final_doc_id,
            status="success",
            message="Document processed successfully",
            tree=TreeNode.model_validate(tree),
        )
    except Exception as e:
        return ProcessResponse(
            doc_id=final_doc_id,
            status="error",
            message=str(e),
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ========================
# Asynchronous Task Management
# ========================

@app.post("/tasks", status_code=202)
async def submit_parse_task(
    file: UploadFile = File(..., description="ZIP file containing OCR output files"),
    model: str = Form(..., description="OCR model name"),
    doc_id: str = Form(None, description="Optional document ID"),
):
    """
    Submit an asynchronous parse task.

    Upload a ZIP with OCR output and receive a task ID immediately.
    Use the task status and result endpoints to check progress and retrieve results.
    """
    if model not in SUPPORTED_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported model: {model}. Supported: {SUPPORTED_MODELS}",
        )

    # Check Redis connection
    if not is_redis_connected():
        raise HTTPException(
            status_code=503,
            detail="Redis connection failed. Cannot submit async tasks.",
        )

    task_id, work_dir, extract_dir, final_doc_id = _extract_and_prepare(file, doc_id)

    # Create task in Redis queue
    create_task(
        task_id=task_id,
        doc_id=final_doc_id,
        model=model,
        file_name=file.filename or "unknown",
        work_dir=str(work_dir),
    )

    return TaskCreateResponse(
        task_id=task_id,
        status="pending",
        message="Task submitted successfully",
    )


@app.get("/tasks/{task_id}")
async def get_async_task_status(task_id: str):
    """Get async task status."""
    task = get_task_status(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    return TaskStatusResponse(
        task_id=task.get("task_id", task_id),
        status=task.get("status", "unknown"),
        progress=task.get("progress", ""),
        created_at=task.get("created_at", ""),
        updated_at=task.get("updated_at", ""),
        doc_id=task.get("doc_id", ""),
        model=task.get("model", ""),
        error=task.get("error") or None,
    )


@app.get("/tasks/{task_id}/result")
async def get_async_task_result(task_id: str):
    """Get async task result."""
    task = get_task_status(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    status = task.get("status", "")

    if status == "pending":
        return TaskResultResponse(
            task_id=task_id,
            status="pending",
            error="Task is still pending",
        )
    elif status == "processing":
        return TaskResultResponse(
            task_id=task_id,
            status="processing",
            error="Task is still processing",
        )
    elif status == "failed":
        result_data = get_task_result(task_id)
        return TaskResultResponse(
            task_id=task_id,
            status="failed",
            result=ProcessResponse(
                doc_id=task.get("doc_id", ""),
                status="error",
                message=result_data.get("message", task.get("error", "")) if result_data else task.get("error", ""),
            ),
            error=task.get("error"),
        )
    elif status == "completed":
        result_data = get_task_result(task_id)
        if not result_data:
            raise HTTPException(status_code=500, detail="Result data missing")

        tree_json = result_data.get("tree", "{}")
        try:
            tree = json.loads(tree_json)
            tree_node = TreeNode.model_validate(tree)
        except Exception:
            tree_node = None

        return TaskResultResponse(
            task_id=task_id,
            status="completed",
            result=ProcessResponse(
                doc_id=result_data.get("doc_id", task.get("doc_id", "")),
                status=result_data.get("status", "success"),
                message=result_data.get("message", "Document processed successfully"),
                tree=tree_node,
            ),
        )
    else:
        raise HTTPException(status_code=500, detail=f"Unknown task status: {status}")


# ========================
# JSON Input (Synchronous)
# ========================

@app.post("/process/json")
async def process_json_data(request: ProcessRequest):
    """
    Process OCR output from direct JSON input.

    Submit already-normalized pages data for inference and tree building.
    """
    if request.model not in SUPPORTED_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported model: {request.model}. Supported: {SUPPORTED_MODELS}",
        )

    task_id = uuid.uuid4().hex[:16]
    work_dir = get_temp_dir() / task_id
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        from api.services.normalize import normalize_from_json
        from api.services.infer import run_inference
        from api.services.tree_builder import build_tree

        # Step 1: Save normalized data
        normalize_dir = work_dir / "normalized"
        pages = normalize_from_json(
            request.model, request.pages, str(normalize_dir), request.doc_id
        )

        # Step 2: Run inference
        infer_dir = work_dir / "inferred"
        elements = run_inference(request.doc_id, pages, str(infer_dir))

        # Step 3: Build tree
        tree_dir = work_dir / "tree"
        txt_dir = work_dir / "txt"
        tree = build_tree(elements, str(tree_dir), str(txt_dir), request.doc_id)

        return ProcessResponse(
            doc_id=request.doc_id,
            status="success",
            message="Document processed successfully",
            tree=TreeNode.model_validate(tree),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ========================
# Startup / Shutdown
# ========================

@app.on_event("startup")
async def startup_event():
    """Start background workers on startup."""
    from api.services.worker import run_worker_async
    run_worker_async()


def run_server():
    """Run the FastAPI server with uvicorn."""
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    run_server()