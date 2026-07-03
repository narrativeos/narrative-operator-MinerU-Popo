"""Model inference service wrapping post_processing/inference.py."""

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


def run_inference(
    doc_id: str,
    pages: Dict[str, List[Dict[str, Any]]],
    output_dir: str,
    pdf_path: Optional[str] = None,
    progress_callback: Optional[Callable[[str, str], None]] = None,
) -> List[Dict[str, Any]]:
    """
    Run MinerU-Popo inference on normalized pages data.
    
    Args:
        doc_id: Document identifier
        pages: Dict mapping page numbers to lists of blocks
        output_dir: Directory to write inference output
        pdf_path: Path to original PDF file for page image rendering.
                  Passed as input_label to inference.main(). If None,
                  falls back to doc_id (may cause inference failure
                  if the VLM needs page images).
        progress_callback: Optional callable(phase, message) for progress reporting.
            Called at each inference sub-phase boundary.
        
    Returns:
        List of processed elements with inference results
    """
    # Add post_processing to path for imports
    repo_root = Path(__file__).resolve().parents[1]
    post_processing_dir = repo_root.parent / "post_processing"
    if str(post_processing_dir) not in sys.path:
        sys.path.insert(0, str(post_processing_dir))
    
    from inference import main as run_one_document
    
    os.makedirs(output_dir, exist_ok=True)
    
    # Use pdf_path as input_label if available, otherwise fall back to doc_id
    input_label = pdf_path or doc_id
    
    # inference.main() uses asyncio.run() internally. To ensure it always
    # gets a clean event loop regardless of the calling context (FastAPI
    # handler, worker thread, etc.), run it in a dedicated thread via
    # ThreadPoolExecutor.
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            run_one_document,
            input_label,
            copy.deepcopy(pages),
            output_dir,
            raw_output_dir=None,
            progress_callback=progress_callback,
        )
        future.result()
    
    # Read the output (inference.main saves as {safe_doc_stem(input_label)}.json)
    output_path = Path(output_dir) / f"{doc_id}.json"
    if not output_path.exists():
        # Try alternative filename based on pdf_path stem
        if pdf_path:
            alt_stem = Path(pdf_path).stem
            alt_path = Path(output_dir) / f"{alt_stem}.json"
            if alt_path.exists():
                output_path = alt_path
    if not output_path.exists():
        raise FileNotFoundError(f"Inference output not found: {output_path}")
    
    result = json.loads(output_path.read_text(encoding="utf-8"))
    return result


def run_inference_from_file(input_path: str, output_dir: str) -> List[Dict[str, Any]]:
    """
    Run inference on an existing normalized JSON file.
    
    Args:
        input_path: Path to normalized JSON file
        output_dir: Directory to write inference output
        
    Returns:
        List of processed elements
    """
    input_path_obj = Path(input_path)
    doc_id = input_path_obj.stem
    
    data = json.loads(input_path_obj.read_text(encoding="utf-8"))
    pages = data.get("pages", data)
    
    return run_inference(doc_id, pages, output_dir)