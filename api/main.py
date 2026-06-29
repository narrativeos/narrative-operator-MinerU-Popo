"""FastAPI application for MinerU-Popo post-processing service."""

import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any, Dict

import zipfile
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from api.config import SUPPORTED_MODELS, get_temp_dir, HOST, PORT
from api.models import HealthResponse, ProcessResponse, TreeNode, Location, TaskStatusResponse


app = FastAPI(
    title="MinerU-Popo API",
    description="Universal Post-Processing Model for Structured Document Parsing",
    version="1.0.0",
)


# In-memory task store (for production, use Redis or a database)
tasks: Dict[str, Dict[str, Any]] = {}


def _build_tree_response(tree: Dict[str, Any]) -> TreeNode:
    """Convert raw tree dict to TreeNode model."""
    return TreeNode.model_validate(tree)


def _process_pipeline(doc_id: str, model_name: str, extract_dir: Path) -> Dict[str, Any]:
    """
    Run the full processing pipeline: normalize -> infer -> build tree.
    
    Returns the final document tree dict.
    """
    # Ensure post_processing is in path
    post_processing_dir = PROJECT_ROOT / "post_processing"
    if str(post_processing_dir) not in sys.path:
        sys.path.insert(0, str(post_processing_dir))
    
    data_engine_dir = PROJECT_ROOT / "data_engine"
    if str(data_engine_dir) not in sys.path:
        sys.path.insert(0, str(data_engine_dir))
    
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


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return HealthResponse(status="ok")


@app.post("/process", response_model=ProcessResponse)
async def process_ocr_zip(
    file: UploadFile = File(..., description="ZIP file containing OCR output files"),
    model: str = Form(..., description="OCR model name: mineru, monkeyocr, PaddleOCR-VL-1.5, dolphin, glm-ocr"),
    doc_id: str = Form(None, description="Optional document ID, inferred from filename if not provided"),
):
    """
    Process OCR output from a ZIP archive.
    
    Upload a ZIP file containing OCR/layout parsing output (e.g., from MinerU, MonkeyOCR, etc.).
    The service will normalize labels, run inference, and build a structured document tree.
    """
    # Validate model
    if model not in SUPPORTED_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported model: {model}. Supported: {SUPPORTED_MODELS}"
        )
    
    # Generate task ID
    task_id = uuid.uuid4().hex[:16]
    
    if not doc_id:
        # Infer doc_id from filename
        doc_id = Path(file.filename).stem
        # Remove common suffixes
        for suffix in ["_model", "_middle", "_content_list"]:
            if doc_id.endswith(suffix):
                doc_id = doc_id[: -len(suffix)]
    
    # Create working directory
    work_dir = get_temp_dir() / task_id
    work_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # Save and extract ZIP
        zip_path = work_dir / file.filename
        content = await file.read()
        zip_path.write_bytes(content)
        
        extract_dir = work_dir / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)
        
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
        
        # Run processing pipeline
        tree = _process_pipeline(doc_id, model, extract_dir)
        
        return ProcessResponse(
            doc_id=doc_id,
            status="success",
            message="Document processed successfully",
            tree=_build_tree_response(tree),
        )
        
    except ValueError as e:
        return ProcessResponse(
            doc_id=doc_id,
            status="error",
            message=str(e),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")
    finally:
        # Clean up temp directory after a delay (in production, use a cleanup task)
        # For now, clean up immediately
        shutil.rmtree(work_dir, ignore_errors=True)


@app.post("/process/json", response_model=ProcessResponse)
async def process_json_data(request: "ProcessRequest"):
    """
    Process OCR output from direct JSON input.
    
    Submit already-normalized pages data for inference and tree building.
    """
    from api.services.infer import run_inference
    from api.services.tree_builder import build_tree
    
    if request.model not in SUPPORTED_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported model: {request.model}. Supported: {SUPPORTED_MODELS}"
        )
    
    task_id = uuid.uuid4().hex[:16]
    work_dir = get_temp_dir() / task_id
    work_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # Step 1: Save normalized data
        from api.services.normalize import normalize_from_json
        
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
            tree=_build_tree_response(tree),
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@app.post("/tasks", response_model=TaskStatusResponse)
async def create_async_task(
    file: UploadFile = File(..., description="ZIP file containing OCR output files"),
    model: str = Form(..., description="OCR model name"),
    doc_id: str = Form(None, description="Optional document ID"),
):
    """
    Create an asynchronous processing task.
    
    Returns a task_id that can be used to check progress.
    """
    import asyncio
    
    task_id = uuid.uuid4().hex[:16]
    
    if not doc_id:
        doc_id = Path(file.filename).stem
        for suffix in ["_model", "_middle", "_content_list"]:
            if doc_id.endswith(suffix):
                doc_id = doc_id[: -len(suffix)]
    
    # Save file for async processing
    work_dir = get_temp_dir() / task_id
    work_dir.mkdir(parents=True, exist_ok=True)
    
    zip_path = work_dir / file.filename
    content = await file.read()
    zip_path.write_bytes(content)
    
    extract_dir = work_dir / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)
    
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)
    
    # Store task info
    tasks[task_id] = {
        "task_id": task_id,
        "doc_id": doc_id,
        "model": model,
        "status": "pending",
        "progress": "Task queued",
        "work_dir": str(work_dir),
        "extract_dir": str(extract_dir),
    }
    
    # Start async processing
    asyncio.create_task(_async_process(task_id))
    
    return TaskStatusResponse(
        task_id=task_id,
        status="pending",
        progress="Task queued for processing",
    )


@app.get("/tasks/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    """Check the status of an async processing task."""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    
    task = tasks[task_id]
    
    return TaskStatusResponse(
        task_id=task_id,
        status=task["status"],
        progress=task.get("progress", ""),
        result=task.get("result"),
        error=task.get("error"),
    )


async def _async_process(task_id: str):
    """Background task for async processing."""
    task = tasks.get(task_id)
    if not task:
        return
    
    task["status"] = "processing"
    task["progress"] = "Normalizing labels..."
    
    try:
        doc_id = task["doc_id"]
        model = task["model"]
        extract_dir = Path(task["extract_dir"])
        
        tree = _process_pipeline(doc_id, model, extract_dir)
        
        task["status"] = "completed"
        task["progress"] = "Processing completed"
        task["result"] = ProcessResponse(
            doc_id=doc_id,
            status="success",
            message="Document processed successfully",
            tree=_build_tree_response(tree),
        )
        
    except Exception as e:
        task["status"] = "failed"
        task["progress"] = f"Processing failed: {str(e)}"
        task["error"] = str(e)


# Import for type annotation
from api.models import ProcessRequest


def run_server():
    """Run the FastAPI server with uvicorn."""
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    run_server()