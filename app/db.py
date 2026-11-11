"""SQLite persistence: schema and a small repository layer.

SQLite is accessed through one shared connection guarded by a lock. Queries are
tiny and local, so this is simpler and cheaper than a pool and is more than
enough for a LAN-sized deployment.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT    NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    prefs         TEXT    NOT NULL DEFAULT '{}',   -- JSON, see schemas.Prefs
    created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,                   -- HMAC-SHA256 of the cookie token
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT    NOT NULL,
    expires_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- Only finished games are stored; games in progress live in server memory.
CREATE TABLE IF NOT EXISTS games (
    id                TEXT PRIMARY KEY,
    white_id          INTEGER REFERENCES users(id) ON DELETE SET NULL,  -- NULL = engine
    black_id          INTEGER REFERENCES users(id) ON DELETE SET NULL,
    white_name        TEXT    NOT NULL,
    black_name        TEXT    NOT NULL,
    vs_computer       INTEGER NOT NULL DEFAULT 0,
    engine_level      INTEGER,
    base_seconds      INTEGER NOT NULL,            -- 0 = no clock
    increment_seconds INTEGER NOT NULL,
    result            TEXT    NOT NULL,            -- 1-0 | 0-1 | 1/2-1/2
    termination       TEXT    NOT NULL,            -- checkmate, timeout, resignation, ...
    moves             TEXT    NOT NULL,            -- JSON [{uci, san, white_ms, black_ms}]
    ply_count         INTEGER NOT NULL,
    started_at        TEXT    NOT NULL,
    ended_at          TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_games_white ON games(white_id);
CREATE INDEX IF NOT EXISTS idx_games_black ON games(black_id);
CREATE INDEX IF NOT EXISTS idx_games_ended ON games(ended_at);
"""


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class Database:
    """Thin, thread-safe wrapper around a single SQLite connection."""

    def __init__(self, path: Path | str) -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- low-level helpers -------------------------------------------------

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def one(self, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # -- users -------------------------------------------------------------

    def create_user(self, username: str, password_hash: str, is_admin: bool = False) -> int:
        cur = self.execute(
            "INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?,?,?,?)",
            (username, password_hash, int(is_admin), iso(utc_now())),
        )
        return int(cur.lastrowid)

    def user_by_name(self, username: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM users WHERE username = ?", (username,))

    def user_by_id(self, user_id: int) -> dict[str, Any] | None:
        return self.one("SELECT * FROM users WHERE id = ?", (user_id,))

    def set_password(self, user_id: int, password_hash: str, is_admin: bool | None = None) -> None:
        if is_admin is None:
            self.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))
        else:
            self.execute(
                "UPDATE users SET password_hash=?, is_admin=? WHERE id=?",
                (password_hash, int(is_admin), user_id),
            )

    def set_prefs(self, user_id: int, prefs: dict[str, Any]) -> None:
        self.execute("UPDATE users SET prefs=? WHERE id=?", (json.dumps(prefs), user_id))

    # -- sessions ----------------------------------------------------------

    def create_session(self, token_hash: str, user_id: int, days: int) -> None:
        now = utc_now()
        self.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (token_hash, user_id, iso(now), iso(now + timedelta(days=days))),
        )

    def session_user(self, token_hash: str) -> dict[str, Any] | None:
        return self.one(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.expires_at > ?",
            (token_hash, iso(utc_now())),
        )

    def delete_session(self, token_hash: str) -> None:
        self.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def purge_expired_sessions(self) -> None:
        self.execute("DELETE FROM sessions WHERE expires_at <= ?", (iso(utc_now()),))

    # -- games -------------------------------------------------------------

    def save_game(self, record: dict[str, Any]) -> None:
        """Insert a finished game (see LiveGame.to_record for the shape)."""
        row = dict(record)
        row["moves"] = json.dumps(record["moves"])
        row["vs_computer"] = int(record["vs_computer"])
        self.execute(
            "INSERT OR REPLACE INTO games (id, white_id, black_id, white_name, black_name, "
            "vs_computer, engine_level, base_seconds, increment_seconds, result, termination, "
            "moves, ply_count, started_at, ended_at) VALUES (:id, :white_id, :black_id, "
            ":white_name, :black_name, :vs_computer, :engine_level, :base_seconds, "
            ":increment_seconds, :result, :termination, :moves, :ply_count, :started_at, "
            ":ended_at)",
            row,
        )

    @staticmethod
    def _decode_game(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        row["moves"] = json.loads(row["moves"])
        row["vs_computer"] = bool(row["vs_computer"])
        return row

    def game(self, game_id: str) -> dict[str, Any] | None:
        return self._decode_game(self.one("SELECT * FROM games WHERE id = ?", (game_id,)))

    def delete_game(self, game_id: str) -> bool:
        return self.execute("DELETE FROM games WHERE id = ?", (game_id,)).rowcount > 0

    def list_games(
        self, user_id: int | None = None, limit: int = 20, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        """Finished games, newest first, optionally restricted to one player."""
        where, params = "", []
        if user_id is not None:
            where, params = "WHERE white_id = ? OR black_id = ?", [user_id, user_id]
        total = self.one(f"SELECT COUNT(*) AS n FROM games {where}", tuple(params))["n"]
        rows = self.all(
            "SELECT id, white_id, black_id, white_name, black_name, vs_computer, engine_level, "
            "base_seconds, increment_seconds, result, termination, ply_count, started_at, "
            f"ended_at FROM games {where} ORDER BY ended_at DESC, id LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        for row in rows:
            row["vs_computer"] = bool(row["vs_computer"])
        return rows, int(total)

    def all_games_of(self, user_id: int) -> list[dict[str, Any]]:
        rows = self.all(
            "SELECT * FROM games WHERE white_id = ? OR black_id = ? ORDER BY ended_at, id",
            (user_id, user_id),
        )
        return [self._decode_game(r) for r in rows]

    def user_stats(self, user_id: int) -> dict[str, int]:
        row = self.one(
            "SELECT "
            "COALESCE(SUM((white_id = :u AND result = '1-0') OR "
            "             (black_id = :u AND result = '0-1')), 0) AS wins, "
            "COALESCE(SUM((white_id = :u AND result = '0-1') OR "
            "             (black_id = :u AND result = '1-0')), 0) AS losses, "
            "COALESCE(SUM(result = '1/2-1/2'), 0) AS draws, "
            "COUNT(*) AS games "
            "FROM games WHERE white_id = :u OR black_id = :u",
            {"u": user_id},
        )
        return {k: int(v) for k, v in row.items()}
