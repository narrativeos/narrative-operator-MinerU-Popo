"""Background worker for processing MinerU-Popo tasks from SQLite queue."""

import json
import os
import shutil
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from api.config import (
    get_model_path,
    get_temp_dir,
    SYNC_TIMEOUT,
)
from api.services.queue import (
    init_db,
    pop_task,
    update_task_status,
    get_task_status,
    save_task_result,
)


def _process_pipeline(doc_id: str, model_name: str, extract_dir: Path) -> Dict[str, Any]:
    """
    Run the full processing pipeline: normalize -> infer -> build tree.

    Returns the final document tree dict.
    """
    # Add project paths
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    post_processing_dir = PROJECT_ROOT / "post_processing"
    data_engine_dir = PROJECT_ROOT / "data_engine"

    for d in [str(PROJECT_ROOT), str(post_processing_dir), str(data_engine_dir)]:
        if d not in sys.path:
            sys.path.insert(0, d)

    # Step 1: Normalize labels
    from api.services.normalize import normalize_ocr_output

    normalize_dir = extract_dir / "normalized"
    pages = normalize_ocr_output(model_name, str(extract_dir), str(normalize_dir), doc_id)

    # Step 2: Locate PDF inside extracted ZIP (required for VLM page rendering)
    # Prefer *_origin.pdf over *_layout.pdf
    pdf_files = sorted(extract_dir.rglob("*.pdf"))
    pdf_path = None
    if pdf_files:
        origin = [p for p in pdf_files if "_origin" in p.stem]
        pdf_path = str(origin[0]) if origin else str(pdf_files[0])
    if not pdf_path:
        raise ValueError(
            "No PDF file found in the uploaded ZIP. "
            "A PDF is required for VLM page rendering during inference."
        )
    print(f"[worker:pipeline] Found PDF: {pdf_path}")

    # Step 3: Run inference
    from api.services.infer import run_inference

    infer_dir = extract_dir / "inferred"
    elements = run_inference(doc_id, pages, str(infer_dir), pdf_path=pdf_path)

    # Step 4: Build tree
    from api.services.tree_builder import build_tree

    tree_dir = extract_dir / "tree"
    txt_dir = extract_dir / "txt"
    tree = build_tree(elements, str(tree_dir), str(txt_dir), doc_id)

    return tree


def process_task(task_id: str, worker_id: str) -> None:
    """
    Process a single task from the queue.

    Args:
        task_id: The task identifier
        worker_id: The worker identifier
    """
    task = get_task_status(task_id)
    if not task:
        return

    doc_id = task.get("doc_id", "")
    model = task.get("model", "")
    work_dir = task.get("work_dir", "")

    try:
        update_task_status(task_id, "processing", "Extracting files...")

        extract_dir = Path(work_dir) / "extracted"

        update_task_status(task_id, "processing", "Normalizing labels...")

        update_task_status(task_id, "processing", "Running inference...")

        tree = _process_pipeline(doc_id, model, extract_dir)

        update_task_status(task_id, "processing", "Building document tree...")

        # Save result
        result = {
            "doc_id": doc_id,
            "status": "success",
            "message": "Document processed successfully",
            "tree": json.dumps(tree, ensure_ascii=False),
        }
        save_task_result(task_id, result)

        update_task_status(task_id, "completed", "Processing completed")

    except Exception as e:
        error_msg = str(e)
        save_task_result(task_id, {
            "doc_id": doc_id,
            "status": "error",
            "message": error_msg,
            "tree": "",
        })
        update_task_status(task_id, "failed", f"Processing failed: {error_msg}", error=error_msg)


def run_worker(worker_id: Optional[str] = None) -> None:
    """
    Run the worker loop, continuously processing tasks from the queue.

    Args:
        worker_id: Optional worker identifier, generated if not provided
    """
    if not worker_id:
        worker_id = f"worker-{uuid.uuid4().hex[:8]}"

    # Ensure database is initialized (important when running standalone)
    init_db()

    print(f"[{worker_id}] Starting worker...")

    try:
        while True:
            task_id = pop_task()
            if task_id:
                print(f"[{worker_id}] Processing task: {task_id}")
                process_task(task_id, worker_id)
                print(f"[{worker_id}] Task {task_id} completed")
            else:
                # No task available, sleep briefly before polling again
                time.sleep(1)
    except KeyboardInterrupt:
        print(f"[{worker_id}] Shutting down...")
    finally:
        print(f"[{worker_id}] Worker stopped.")


def run_worker_async() -> None:
    """
    Run worker in a background thread.

    This is used when starting the worker alongside the FastAPI server.
    """
    import threading

    worker_id = f"worker-{uuid.uuid4().hex[:8]}"
    thread = threading.Thread(target=run_worker, args=(worker_id,), daemon=True)
    thread.start()
    print(f"Background worker {worker_id} started in thread.")