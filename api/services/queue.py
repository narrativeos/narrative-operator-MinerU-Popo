"""Redis queue management for MinerU-Popo task processing."""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import redis


def get_redis_client() -> redis.Redis:
    """Get a Redis client instance."""
    from api.config import REDIS_HOST, REDIS_PORT, REDIS_DB, REDIS_PASSWORD

    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD or None,
        decode_responses=True,
    )


def is_redis_connected() -> bool:
    """Check if Redis is connected."""
    try:
        client = get_redis_client()
        return client.ping()
    except Exception:
        return False


def get_queue_length() -> int:
    """Get the number of tasks in the queue."""
    from api.config import REDIS_QUEUE_KEY
    try:
        client = get_redis_client()
        return client.llen(REDIS_QUEUE_KEY)
    except Exception:
        return -1


def get_active_workers() -> int:
    """Get the number of active workers."""
    from api.config import REDIS_WORKER_PREFIX
    try:
        client = get_redis_client()
        keys = client.keys(f"{REDIS_WORKER_PREFIX}*")
        return len(keys)
    except Exception:
        return 0


def create_task(
    task_id: str,
    doc_id: str,
    model: str,
    file_name: str,
    work_dir: str,
) -> Dict[str, Any]:
    """
    Create a new task and add it to the processing queue.
    
    Args:
        task_id: Unique task identifier
        doc_id: Document identifier
        model: OCR model name
        file_name: Original uploaded file name
        work_dir: Working directory path for this task
        
    Returns:
        Task metadata dict
    """
    from api.config import REDIS_QUEUE_KEY, REDIS_TASK_PREFIX, REDIS_TASK_TTL

    client = get_redis_client()
    now = datetime.utcnow().isoformat()

    task_data = {
        "task_id": task_id,
        "doc_id": doc_id,
        "model": model,
        "status": "pending",
        "progress": "Task queued",
        "file_name": file_name,
        "work_dir": work_dir,
        "created_at": now,
        "updated_at": now,
        "error": "",
    }

    # Store task metadata
    task_key = f"{REDIS_TASK_PREFIX}{task_id}"
    client.hset(task_key, mapping=task_data)
    client.expire(task_key, REDIS_TASK_TTL)

    # Add task to queue
    client.rpush(REDIS_QUEUE_KEY, task_id)

    return task_data


def get_task_status(task_id: str) -> Optional[Dict[str, Any]]:
    """
    Get the current status of a task.
    
    Returns:
        Task metadata dict, or None if task not found
    """
    from api.config import REDIS_TASK_PREFIX

    client = get_redis_client()
    task_key = f"{REDIS_TASK_PREFIX}{task_id}"

    if not client.exists(task_key):
        return None

    task_data = client.hgetall(task_key)
    # Convert any bytes values
    return {k: v for k, v in task_data.items()}


def update_task_status(
    task_id: str,
    status: str,
    progress: str,
    error: Optional[str] = None,
) -> None:
    """Update the status of a task."""
    from api.config import REDIS_TASK_PREFIX

    client = get_redis_client()
    task_key = f"{REDIS_TASK_PREFIX}{task_id}"
    now = datetime.utcnow().isoformat()

    updates = {
        "status": status,
        "progress": progress,
        "updated_at": now,
    }
    if error is not None:
        updates["error"] = error

    client.hset(task_key, mapping=updates)


def save_task_result(task_id: str, result: Dict[str, Any]) -> None:
    """
    Save the processing result for a task.
    
    Args:
        task_id: Task identifier
        result: Result dict containing doc_id, tree, etc.
    """
    from api.config import REDIS_RESULT_PREFIX, REDIS_TASK_TTL

    client = get_redis_client()
    result_key = f"{REDIS_RESULT_PREFIX}{task_id}"

    client.hset(result_key, mapping=result)
    client.expire(result_key, REDIS_TASK_TTL)


def get_task_result(task_id: str) -> Optional[Dict[str, Any]]:
    """
    Get the processing result for a task.
    
    Returns:
        Result dict, or None if not found
    """
    from api.config import REDIS_RESULT_PREFIX

    client = get_redis_client()
    result_key = f"{REDIS_RESULT_PREFIX}{task_id}"

    if not client.exists(result_key):
        return None

    result_data = client.hgetall(result_key)
    return {k: v for k, v in result_data.items()}


def pop_task() -> Optional[str]:
    """
    Pop a task from the queue (blocking with timeout).
    
    Returns:
        task_id or None if no task available
    """
    from api.config import REDIS_QUEUE_KEY

    client = get_redis_client()
    # BRPOP with 1 second timeout to allow graceful shutdown
    result = client.brpop(REDIS_QUEUE_KEY, timeout=1)
    if result:
        return result[1]  # Returns (key, value) tuple
    return None


def register_worker(worker_id: str) -> None:
    """Register a worker as active."""
    from api.config import REDIS_WORKER_PREFIX

    client = get_redis_client()
    worker_key = f"{REDIS_WORKER_PREFIX}{worker_id}"
    now = datetime.utcnow().isoformat()
    client.hset(worker_key, mapping={
        "worker_id": worker_id,
        "status": "idle",
        "current_task": "",
        "last_heartbeat": now,
    })
    client.expire(worker_key, 300)  # 5 minutes


def unregister_worker(worker_id: str) -> None:
    """Unregister a worker."""
    from api.config import REDIS_WORKER_PREFIX

    client = get_redis_client()
    worker_key = f"{REDIS_WORKER_PREFIX}{worker_id}"
    client.delete(worker_key)


def update_worker_status(worker_id: str, status: str, current_task: str = "") -> None:
    """Update worker status and heartbeat."""
    from api.config import REDIS_WORKER_PREFIX

    client = get_redis_client()
    worker_key = f"{REDIS_WORKER_PREFIX}{worker_id}"
    now = datetime.utcnow().isoformat()
    client.hset(worker_key, mapping={
        "status": status,
        "current_task": current_task,
        "last_heartbeat": now,
    })
    client.expire(worker_key, 300)