import math
import os
import sqlite3
import threading
import time
import uuid
from typing import Any

from app.config import get_db_path
from app.logger import logger


class UsageStore:
    def __init__(self, db_path: str | None = None):
        self.db_path = str(db_path or get_db_path())
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._get_conn() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS usage_events (
                        id TEXT PRIMARY KEY,
                        created_at INTEGER NOT NULL,
                        request_id TEXT NOT NULL,
                        request_type TEXT NOT NULL,
                        stream INTEGER NOT NULL DEFAULT 0,
                        model TEXT NOT NULL,
                        account_id TEXT,
                        conversation_id TEXT,
                        prompt_tokens INTEGER NOT NULL DEFAULT 0,
                        completion_tokens INTEGER NOT NULL DEFAULT 0,
                        total_tokens INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_usage_events_created_at
                    ON usage_events(created_at)
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_usage_events_model_created_at
                    ON usage_events(model, created_at)
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_usage_events_account_created_at
                    ON usage_events(account_id, created_at)
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_usage_events_request_type_created_at
                    ON usage_events(request_type, created_at)
                    """
                )
                conn.commit()

    def record_event(
        self,
        *,
        request_id: str,
        request_type: str,
        stream: bool,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        account_id: str = "",
        conversation_id: str = "",
        created_at: int | None = None,
    ) -> dict[str, Any]:
        created_ts = int(created_at or time.time())
        event = {
            "id": f"usage_{request_id}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}",
            "created_at": created_ts,
            "request_id": str(request_id or "").strip(),
            "request_type": str(request_type or "chat.completions").strip(),
            "stream": 1 if stream else 0,
            "model": str(model or "").strip(),
            "account_id": str(account_id or "").strip(),
            "conversation_id": str(conversation_id or "").strip(),
            "prompt_tokens": max(0, int(prompt_tokens or 0)),
            "completion_tokens": max(0, int(completion_tokens or 0)),
            "total_tokens": max(0, int(total_tokens or 0)),
        }
        with self._lock:
            with self._get_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO usage_events (
                        id,
                        created_at,
                        request_id,
                        request_type,
                        stream,
                        model,
                        account_id,
                        conversation_id,
                        prompt_tokens,
                        completion_tokens,
                        total_tokens
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event["id"],
                        event["created_at"],
                        event["request_id"],
                        event["request_type"],
                        event["stream"],
                        event["model"],
                        event["account_id"] or None,
                        event["conversation_id"] or None,
                        event["prompt_tokens"],
                        event["completion_tokens"],
                        event["total_tokens"],
                    ),
                )
                conn.commit()
        return event

    def query_summary(
        self,
        *,
        start_ts: int | None = None,
        end_ts: int | None = None,
        model: str | None = None,
        account_id: str | None = None,
        request_type: str | None = None,
    ) -> dict[str, Any]:
        where_sql, params = self._build_filters(
            start_ts=start_ts,
            end_ts=end_ts,
            model=model,
            account_id=account_id,
            request_type=request_type,
        )
        with self._get_conn() as conn:
            summary_row = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS request_count,
                    COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    COALESCE(SUM(stream), 0) AS stream_request_count,
                    MIN(created_at) AS first_event_at,
                    MAX(created_at) AS last_event_at,
                    COUNT(DISTINCT model) AS distinct_models,
                    COUNT(DISTINCT COALESCE(NULLIF(account_id, ''), '__none__')) AS distinct_accounts
                FROM usage_events
                {where_sql}
                """,
                params,
            ).fetchone()
            by_model = [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT
                        model,
                        COUNT(*) AS request_count,
                        COALESCE(SUM(total_tokens), 0) AS total_tokens
                    FROM usage_events
                    {where_sql}
                    GROUP BY model
                    ORDER BY total_tokens DESC, request_count DESC, model ASC
                    LIMIT 20
                    """,
                    params,
                ).fetchall()
            ]
            by_account = [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT
                        COALESCE(NULLIF(account_id, ''), 'unknown') AS account_id,
                        COUNT(*) AS request_count,
                        COALESCE(SUM(total_tokens), 0) AS total_tokens
                    FROM usage_events
                    {where_sql}
                    GROUP BY COALESCE(NULLIF(account_id, ''), 'unknown')
                    ORDER BY total_tokens DESC, request_count DESC, account_id ASC
                    LIMIT 20
                    """,
                    params,
                ).fetchall()
            ]
        request_count = int(summary_row["request_count"] or 0)
        return {
            "request_count": request_count,
            "prompt_tokens": int(summary_row["prompt_tokens"] or 0),
            "completion_tokens": int(summary_row["completion_tokens"] or 0),
            "total_tokens": int(summary_row["total_tokens"] or 0),
            "stream_request_count": int(summary_row["stream_request_count"] or 0),
            "non_stream_request_count": max(
                0,
                request_count - int(summary_row["stream_request_count"] or 0),
            ),
            "first_event_at": int(summary_row["first_event_at"] or 0),
            "last_event_at": int(summary_row["last_event_at"] or 0),
            "distinct_models": int(summary_row["distinct_models"] or 0),
            "distinct_accounts": int(summary_row["distinct_accounts"] or 0),
            "avg_total_tokens": (
                round(int(summary_row["total_tokens"] or 0) / request_count, 2)
                if request_count
                else 0
            ),
            "by_model": by_model,
            "by_account": by_account,
        }

    def query_events(
        self,
        *,
        start_ts: int | None = None,
        end_ts: int | None = None,
        model: str | None = None,
        account_id: str | None = None,
        request_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        safe_limit = min(max(1, int(limit or 100)), 500)
        safe_offset = max(0, int(offset or 0))
        where_sql, params = self._build_filters(
            start_ts=start_ts,
            end_ts=end_ts,
            model=model,
            account_id=account_id,
            request_type=request_type,
        )
        with self._get_conn() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS count FROM usage_events {where_sql}", params
                ).fetchone()["count"]
                or 0
            )
            rows = conn.execute(
                f"""
                SELECT
                    id,
                    created_at,
                    request_id,
                    request_type,
                    stream,
                    model,
                    COALESCE(account_id, '') AS account_id,
                    COALESCE(conversation_id, '') AS conversation_id,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens
                FROM usage_events
                {where_sql}
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, safe_limit, safe_offset],
            ).fetchall()
        events = []
        for row in rows:
            item = dict(row)
            item["stream"] = bool(item.get("stream"))
            events.append(item)
        return {
            "total": total,
            "limit": safe_limit,
            "offset": safe_offset,
            "has_more": safe_offset + len(events) < total,
            "events": events,
        }

    def _build_filters(
        self,
        *,
        start_ts: int | None = None,
        end_ts: int | None = None,
        model: str | None = None,
        account_id: str | None = None,
        request_type: str | None = None,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if start_ts is not None:
            clauses.append("created_at >= ?")
            params.append(int(start_ts))
        if end_ts is not None:
            clauses.append("created_at <= ?")
            params.append(int(end_ts))
        normalized_model = str(model or "").strip()
        if normalized_model:
            clauses.append("model = ?")
            params.append(normalized_model)
        normalized_account_id = str(account_id or "").strip()
        if normalized_account_id:
            clauses.append("COALESCE(account_id, '') = ?")
            params.append(normalized_account_id)
        normalized_request_type = str(request_type or "").strip()
        if normalized_request_type:
            clauses.append("request_type = ?")
            params.append(normalized_request_type)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where_sql, params


def estimate_token_count(text: Any) -> int:
    raw = str(text or "").strip()
    if not raw:
        return 0
    return max(1, int(math.ceil(len(raw) / 4)))
