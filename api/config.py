"""Configuration management for MinerU-Popo API."""

import os
from pathlib import Path


def get_model_path() -> str:
    """Get the Popo model path from environment or default."""
    return os.environ.get("POPO_MODEL_PATH", str(Path(__file__).resolve().parents[1] / "models" / "Mineru-Popo"))


def get_temp_dir() -> Path:
    """Get temporary directory for processing."""
    temp_dir = Path("/tmp/popo_api")
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


# Redis configuration
REDIS_HOST = os.environ.get("POPO_REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("POPO_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("POPO_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("POPO_REDIS_PASSWORD", "")
REDIS_TASK_TTL = int(os.environ.get("POPO_TASK_TTL", "86400"))  # 24 hours in seconds

# Redis key prefixes
REDIS_TASK_PREFIX = "popo:task:"
REDIS_RESULT_PREFIX = "popo:result:"
REDIS_QUEUE_KEY = "popo:queue"
REDIS_WORKER_PREFIX = "popo:worker:"

# Supported OCR models
SUPPORTED_MODELS = [
    "mineru",
    "monkeyocr",
    "PaddleOCR-VL-1.5",
    "dolphin",
    "glm-ocr",
]

# Server settings
HOST = os.environ.get("POPO_API_HOST", "0.0.0.0")
PORT = int(os.environ.get("POPO_API_PORT", "8000"))
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB

# Worker settings
WORKER_CONCURRENCY = int(os.environ.get("POPO_WORKER_CONCURRENCY", "4"))
SYNC_TIMEOUT = int(os.environ.get("POPO_SYNC_TIMEOUT", "300"))  # 5 minutes
