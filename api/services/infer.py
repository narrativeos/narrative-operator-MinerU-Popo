"""Model inference service wrapping post_processing/inference.py."""

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List


def run_inference(doc_id: str, pages: Dict[str, List[Dict[str, Any]]], output_dir: str) -> List[Dict[str, Any]]:
    """
    Run MinerU-Popo inference on normalized pages data.
    
    Args:
        doc_id: Document identifier
        pages: Dict mapping page numbers to lists of blocks
        output_dir: Directory to write inference output
        
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
    
    # Run inference
    run_one_document(
        doc_id=doc_id,
        pages=copy.deepcopy(pages),
        output_dir=output_dir,
        raw_output_dir=None,
    )
    
    # Read the output
    output_path = Path(output_dir) / f"{doc_id}.json"
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