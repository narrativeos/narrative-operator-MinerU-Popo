"""SQLite-based task queue for MinerU-Popo task processing."""

import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from api.config import SQLITE_DB_PATH, TASK_TTL_SECONDS


def _get_conn() -> sqlite3.Connection:
    """Get a SQLite connection."""
    db_path = Path(SQLITE_DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db() -> None:
    """Initialize the database schema."""
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                progress TEXT DEFAULT '',
                file_name TEXT DEFAULT '',
                work_dir TEXT DEFAULT '',
                result TEXT DEFAULT '',
                error TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_updated ON tasks(updated_at)")
        conn.commit()
    finally:
        conn.close()


def is_db_available() -> bool:
    """Check if the database is accessible."""
    try:
        conn = _get_conn()
        conn.execute("SELECT 1")
        conn.close()
        return True
    except Exception:
        return False


def get_queue_length() -> int:
    """Get the number of pending tasks."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM tasks WHERE status = 'pending'"
        ).fetchone()
        return row["cnt"] if row else 0
    finally:
        conn.close()


def get_active_workers() -> int:
    """Get the number of tasks currently being processed."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM tasks WHERE status = 'processing'"
        ).fetchone()
        return row["cnt"] if row else 0
    finally:
        conn.close()


def create_task(
    task_id: str,
    doc_id: str,
    model: str,
    file_name: str,
    work_dir: str,
) -> Dict[str, Any]:
    """Create a new task."""
    conn = _get_conn()
    try:
        now = datetime.utcnow().isoformat()
        conn.execute(
            """
            INSERT INTO tasks (task_id, doc_id, model, status, progress, file_name, work_dir, created_at, updated_at)
            VALUES (?, ?, ?, 'pending', 'Task queued', ?, ?, ?, ?)
            """,
            (task_id, doc_id, model, file_name, work_dir, now, now),
        )
        conn.commit()
        return {
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
    finally:
        conn.close()


def get_task_status(task_id: str) -> Optional[Dict[str, Any]]:
    """Get the current status of a task."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if not row:
            return None
        return dict(row)
    finally:
        conn.close()


def update_task_status(
    task_id: str,
    status: str,
    progress: str,
    error: Optional[str] = None,
) -> None:
    """Update the status of a task."""
    conn = _get_conn()
    try:
        now = datetime.utcnow().isoformat()
        if error is not None:
            conn.execute(
                """
                UPDATE tasks SET status = ?, progress = ?, error = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (status, progress, error, now, task_id),
            )
        else:
            conn.execute(
                """
                UPDATE tasks SET status = ?, progress = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (status, progress, now, task_id),
            )
        conn.commit()
    finally:
        conn.close()


def save_task_result(task_id: str, result: Dict[str, Any]) -> None:
    """Save the processing result for a task."""
    conn = _get_conn()
    try:
        result_json = json.dumps(result, ensure_ascii=False)
        conn.execute(
            "UPDATE tasks SET result = ? WHERE task_id = ?",
            (result_json, task_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_task_result(task_id: str) -> Optional[Dict[str, Any]]:
    """Get the processing result for a task."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT result FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if not row or not row["result"]:
            return None
        return json.loads(row["result"])
    finally:
        conn.close()


def pop_task() -> Optional[str]:
    """
    Atomically claim the next pending task.
    Returns task_id or None if no task available.
    """
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT task_id FROM tasks WHERE status = 'pending' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if not row:
            return None
        task_id = row["task_id"]
        now = datetime.utcnow().isoformat()
        conn.execute(
            """
            UPDATE tasks SET status = 'processing', progress = 'Claimed by worker', updated_at = ?
            WHERE task_id = ?
            """,
            (now, task_id),
        )
        conn.commit()
        return task_id
    finally:
        conn.close()


def cleanup_old_tasks() -> int:
    """Delete tasks older than TTL. Returns count of deleted tasks."""
    conn = _get_conn()
    try:
        cutoff = datetime.utcnow().isoformat()
        # Keep completed/failed tasks for TTL, delete old ones
        conn.execute(
            """
            DELETE FROM tasks
            WHERE status IN ('completed', 'failed')
            AND datetime(updated_at) < datetime(?, '-' || ? || ' seconds')
            """,
            (cutoff, TASK_TTL_SECONDS),
        )
        count = conn.rowcount
        conn.commit()
        return count
    finally:
        conn.close()