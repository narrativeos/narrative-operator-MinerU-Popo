"""Pydantic models for MinerU-Popo API request/response."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# --- Request Models ---

class ProcessRequest(BaseModel):
    """Request model for direct JSON input (alternative to zip upload)."""
    doc_id: str = Field(..., description="Document identifier")
    model: str = Field(..., description="OCR model name: mineru, monkeyocr, PaddleOCR-VL-1.5, dolphin, glm-ocr")
    pages: Dict[str, List[Dict[str, Any]]] = Field(..., description="Pages with blocks keyed by page number")


# --- Response Models ---

class Location(BaseModel):
    bbox: List[float]
    page: int


class TreeNode(BaseModel):
    """A node in the document tree."""
    type: str
    title: str = ""
    metadata: str = ""
    content: str = ""
    level: int = 0
    location: List[Location] = []
    block_ids: List[int] = []
    children: List["TreeNode"] = []


class ProcessResponse(BaseModel):
    """Response for document processing."""
    doc_id: str
    status: str  # "success" or "error"
    message: str = ""
    tree: Optional[TreeNode] = None


class TaskStatusResponse(BaseModel):
    """Response for task status queries."""
    task_id: str
    status: str  # "pending", "processing", "completed", "failed"
    progress: str = ""
    result: Optional[ProcessResponse] = None
    error: Optional[str] = None


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "ok"
    model_loaded: bool = False
    supported_models: List[str] = [
        "mineru",
        "monkeyocr",
        "PaddleOCR-VL-1.5",
        "dolphin",
        "glm-ocr",
    ]