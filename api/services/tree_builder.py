"""Document tree building service wrapping post_processing/get_json_tree.py."""

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict


def build_tree(elements: list[Dict[str, Any]], output_dir: str, txt_dir: str, doc_id: str) -> Dict[str, Any]:
    """
    Build a structured document tree from inference results.
    
    Args:
        elements: List of processed elements from inference
        output_dir: Directory to write tree JSON
        txt_dir: Directory to write text preview
        doc_id: Document identifier
        
    Returns:
        The document tree as a nested dict
    """
    # Add post_processing to path for imports
    repo_root = Path(__file__).resolve().parents[1]
    post_processing_dir = repo_root.parent / "post_processing"
    if str(post_processing_dir) not in sys.path:
        sys.path.insert(0, str(post_processing_dir))
    
    # Temporarily save elements to a file for the existing function
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(txt_dir, exist_ok=True)
    
    temp_input = Path(output_dir) / f"{doc_id}_input.json"
    temp_input.write_text(json.dumps(elements, ensure_ascii=False, indent=2), encoding="utf-8")
    
    # Import and use the tree construction function
    from get_json_tree import construct_json_tree
    
    construct_json_tree(str(temp_input), output_dir, txt_dir)
    
    # Read and return the result
    tree_path = Path(output_dir) / f"{doc_id}.json"
    if not tree_path.exists():
        raise FileNotFoundError(f"Tree output not found: {tree_path}")
    
    tree = json.loads(tree_path.read_text(encoding="utf-8"))
    
    # Clean up temp input file
    temp_input.unlink(missing_ok=True)
    
    return tree


def build_tree_from_file(input_path: str, output_dir: str, txt_dir: str) -> Dict[str, Any]:
    """
    Build tree from an existing inference output file.
    
    Args:
        input_path: Path to inference output JSON
        output_dir: Directory to write tree JSON
        txt_dir: Directory to write text preview
        
    Returns:
        The document tree
    """
    input_path_obj = Path(input_path)
    doc_id = input_path_obj.stem
    
    elements = json.loads(input_path_obj.read_text(encoding="utf-8"))
    return build_tree(elements, output_dir, txt_dir, doc_id)