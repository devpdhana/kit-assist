"""Conversation history in SQLite. Every read is filtered by owner
(`user:<login_id>` or `visitor:<id>`), so nobody sees another person's history."""

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from src.configs.config import settings

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, owner TEXT NOT NULL, role TEXT NOT NULL, title TEXT,
  started_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL REFERENCES conversations(id),
  sender TEXT NOT NULL CHECK (sender IN ('user','assistant')), text TEXT NOT NULL, meta TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_conv_owner ON conversations(owner, updated_at);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, id);
"""


def init(path: str | None = None) -> None:
    global _conn
    path = path or settings.DB_PATH
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.executescript(SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_turn(conversation_id: str, owner: str, role: str, user_text: str, reply: str, meta: dict) -> None:
    with _lock:
        row = _conn.execute("SELECT owner FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        now = _now()
        if row is None:
            _conn.execute("INSERT INTO conversations VALUES (?,?,?,?,?,?)", (conversation_id, owner, role, user_text[:80], now, now))
        elif row["owner"] != owner:
            raise PermissionError("conversation owner mismatch")
        _conn.execute("INSERT INTO messages (conversation_id, sender, text, meta, created_at) VALUES (?,?,?,?,?)", (conversation_id, "user", user_text[:4000], None, now))
        _conn.execute("INSERT INTO messages (conversation_id, sender, text, meta, created_at) VALUES (?,?,?,?,?)", (conversation_id, "assistant", reply[:4000], json.dumps(meta, default=str), now))
        _conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
        _conn.commit()


def list_conversations(owners: list[str], limit: int = 50) -> list[dict]:
    q = f"""SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count
            FROM conversations c WHERE c.owner IN ({",".join("?" * len(owners))}) ORDER BY c.updated_at DESC LIMIT ?"""
    with _lock:
        return [dict(r) | {"scope": "account" if r["owner"].startswith("user:") else "public"} for r in _conn.execute(q, (*owners, limit))]


def get_conversation(owners: list[str], cid: str) -> dict | None:
    with _lock:
        conv = _conn.execute(f"SELECT * FROM conversations WHERE id = ? AND owner IN ({','.join('?' * len(owners))})", (cid, *owners)).fetchone()
        if not conv:
            return None
        msgs = [
            {"sender": m["sender"], "text": m["text"], "created_at": m["created_at"], "meta": json.loads(m["meta"]) if m["meta"] else None}
            for m in _conn.execute("SELECT * FROM messages WHERE conversation_id = ? ORDER BY id", (cid,))
        ]
    return {k: conv[k] for k in conv.keys() if k != "owner"} | {"messages": msgs}


def delete_conversation(owners: list[str], cid: str) -> bool:
    with _lock:
        conv = _conn.execute(f"SELECT id FROM conversations WHERE id = ? AND owner IN ({','.join('?' * len(owners))})", (cid, *owners)).fetchone()
        if not conv:
            return False
        _conn.execute("DELETE FROM messages WHERE conversation_id = ?", (cid,))
        _conn.execute("DELETE FROM conversations WHERE id = ?", (cid,))
        _conn.commit()
        return True
