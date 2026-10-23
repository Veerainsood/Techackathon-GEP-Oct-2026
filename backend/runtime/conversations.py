"""Conversations in PostgreSQL (schema ``backend_runtime``): each question and answer is a message in a conversation,
so follow-ups can use earlier turns and the UI can reopen past conversations.

A conversation also remembers its attached files. Workspaces live in memory, so after a restart the conversation's
workspace is recreated from those files (the files themselves are kept in the file store).
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from psycopg2.extras import Json
from psycopg2.pool import ThreadedConnectionPool

DDL = """
CREATE SCHEMA IF NOT EXISTS backend_runtime;
CREATE TABLE IF NOT EXISTS backend_runtime.conversations (
    conversation_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    file_ids JSONB NOT NULL DEFAULT '[]',
    files JSONB NOT NULL DEFAULT '[]',
    workspace_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS conversations_owner_idx ON backend_runtime.conversations (tenant_id, user_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS backend_runtime.messages (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES backend_runtime.conversations(conversation_id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    meta JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS messages_conversation_idx ON backend_runtime.messages (conversation_id, created_at);
"""
HISTORY_CHARS = 1200  # per message, when passed to the intent agent


class ConversationNotFound(KeyError):
    pass


class ConversationStore:
    def __init__(self, uri: str):
        self._pool = ThreadedConnectionPool(1, 8, uri, connect_timeout=5)
        self._exec(DDL)

    def _exec(self, sql: str, params: Any = None, fetch: str = "") -> Any:
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                out = None
                if fetch == "one":
                    out = cur.fetchone()
                elif fetch == "all":
                    out = cur.fetchall()
                if fetch and cur.description:
                    cols = [d[0] for d in cur.description]
                    out = (dict(zip(cols, out)) if out else None) if fetch == "one" else [dict(zip(cols, r)) for r in out]
            conn.commit()
            return out
        except Exception:
            conn.rollback()
            raise
        finally:
            self._pool.putconn(conn)

    def create(self, tenant_id: str, user_id: str, title: str) -> str:
        cid = f"conv-{uuid.uuid4().hex[:12]}"
        self._exec("INSERT INTO backend_runtime.conversations (conversation_id, tenant_id, user_id, title) VALUES (%s, %s, %s, %s)",
                   (cid, tenant_id, user_id, title[:120]))
        return cid

    def get(self, tenant_id: str, user_id: str, conversation_id: str) -> Dict[str, Any]:
        row = self._exec("SELECT * FROM backend_runtime.conversations WHERE conversation_id = %s AND tenant_id = %s AND user_id = %s",
                         (conversation_id, tenant_id, user_id), fetch="one")
        if row is None:
            raise ConversationNotFound(conversation_id)  # same error for another user's conversation
        return row

    def list(self, tenant_id: str, user_id: str, limit: int = 30) -> List[Dict[str, Any]]:
        return self._exec("""SELECT c.conversation_id, c.title, c.files, c.updated_at,
                                    (SELECT count(*) FROM backend_runtime.messages m WHERE m.conversation_id = c.conversation_id) AS messages
                             FROM backend_runtime.conversations c WHERE c.tenant_id = %s AND c.user_id = %s
                             ORDER BY c.updated_at DESC LIMIT %s""", (tenant_id, user_id, limit), fetch="all")

    def set_workspace(self, conversation_id: str, workspace_id: str, files: List[Dict[str, Any]]) -> None:
        self._exec("""UPDATE backend_runtime.conversations SET workspace_id = %s, files = %s, file_ids = %s, updated_at = now()
                      WHERE conversation_id = %s""",
                   (workspace_id, Json(files), Json([f["file_id"] for f in files]), conversation_id))

    def add_message(self, conversation_id: str, role: str, content: str, meta: Optional[Dict[str, Any]] = None) -> str:
        mid = f"msg-{uuid.uuid4().hex[:12]}"
        self._exec("INSERT INTO backend_runtime.messages (message_id, conversation_id, role, content, meta) VALUES (%s, %s, %s, %s, %s)",
                   (mid, conversation_id, role, content, Json(meta or {}, dumps=lambda o: json.dumps(o, default=str))))
        self._exec("UPDATE backend_runtime.conversations SET updated_at = now() WHERE conversation_id = %s", (conversation_id,))
        return mid

    def messages(self, conversation_id: str) -> List[Dict[str, Any]]:
        return self._exec("SELECT message_id, role, content, meta, created_at FROM backend_runtime.messages "
                          "WHERE conversation_id = %s ORDER BY created_at", (conversation_id,), fetch="all")

    def history(self, conversation_id: str, turns: int = 6) -> List[Dict[str, str]]:
        """The last ``turns`` question/answer pairs, oldest first, trimmed for a prompt."""
        rows = self._exec("""SELECT role, content FROM (SELECT role, content, created_at FROM backend_runtime.messages
                             WHERE conversation_id = %s ORDER BY created_at DESC LIMIT %s) x ORDER BY created_at""",
                          (conversation_id, turns * 2), fetch="all")
        return [{"role": r["role"], "content": r["content"][:HISTORY_CHARS]} for r in rows]

    def delete(self, tenant_id: str, user_id: str, conversation_id: str) -> None:
        self.get(tenant_id, user_id, conversation_id)
        self._exec("DELETE FROM backend_runtime.conversations WHERE conversation_id = %s", (conversation_id,))
