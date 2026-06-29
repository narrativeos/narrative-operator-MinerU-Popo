"""Label normalization service wrapping post_processing/label_normalization.py."""

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List


def normalize_ocr_output(model_name: str, input_dir: str, output_dir: str, doc_id: str) -> Dict[str, Any]:
    """
    Run label normalization on OCR output.
    
    Args:
        model_name: One of "mineru", "monkeyocr", "PaddleOCR-VL-1.5", "dolphin", "glm-ocr"
        input_dir: Path to the extracted OCR output directory
        output_dir: Path to write normalized output
        doc_id: Document identifier
        
    Returns:
        Dict with normalized pages data
    """
    # Add post_processing to path for imports
    repo_root = Path(__file__).resolve().parents[1]
    post_processing_dir = repo_root.parent / "post_processing"
    if str(post_processing_dir) not in sys.path:
        sys.path.insert(0, str(post_processing_dir))
    
    from label_normalization import build_reader_from_input_dir, to_popo_pages
    
    reader = build_reader_from_input_dir(model_name, input_dir, bbox_scale="source")
    result = reader.read_doc(doc_id)
    
    if result.status != "ok":
        raise ValueError(f"Normalization failed for {doc_id}: {result.message}")
    
    pages = to_popo_pages(result.blocks)
    
    os.makedirs(output_dir, exist_ok=True)
    output_path = Path(output_dir) / f"{doc_id}.json"
    payload = {
        "model": model_name,
        "doc_id": doc_id,
        "input_label": doc_id,
        "pages": pages,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    
    return pages


def normalize_from_json(model_name: str, pages_data: Dict[str, Any], output_dir: str, doc_id: str) -> Dict[str, Any]:
    """
    Normalize already-parsed JSON data directly (from zip with pre-normalized format).
    
    Args:
        model_name: OCR model name
        pages_data: Pages dict with blocks
        output_dir: Output directory
        doc_id: Document identifier
        
    Returns:
        Normalized pages data
    """
    os.makedirs(output_dir, exist_ok=True)
    output_path = Path(output_dir) / f"{doc_id}.json"
    payload = {
        "model": model_name,
        "doc_id": doc_id,
        "input_label": doc_id,
        "pages": pages_data,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    
    return pages_data