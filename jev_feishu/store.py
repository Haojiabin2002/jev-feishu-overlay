from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
import hashlib
import os
import sqlite3
import time
import fcntl
from .config import Config
from .errors import CopilotError

class Store:
    """At rest: hashed request IDs, timestamps, statuses, quotas; no chat/report bodies."""
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.path = cfg.state_dir
        self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path, 0o700)
        self.db = sqlite3.connect(self.path / "state.sqlite3")
        os.chmod(self.path / "state.sqlite3", 0o600)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, status TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS quota (created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    @staticmethod
    def key(message_id: str) -> str:
        return hashlib.sha256(message_id.encode()).hexdigest()[:40]

    def reserve(self, message_id: str) -> bool:
        with self.db:
            c = self.db.execute("INSERT OR IGNORE INTO requests VALUES (?, 'processing', ?)",
                                (self.key(message_id), time.time()))
            return c.rowcount == 1

    def done(self, message_id: str, status: str = "done") -> None:
        if status not in ("done", "failed", "ignored"):
            raise ValueError("invalid status")
        with self.db:
            self.db.execute("UPDATE requests SET status=? WHERE id=?", (status, self.key(message_id)))

    def get(self, key: str, default: str = "") -> str:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return str(row[0]) if row else default

    def set(self, key: str, value: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))

    def consume_analysis(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        day_start = int(now // 86400) * 86400  # Daily cap resets at 00:00 UTC.
        with self.db:
            daily = self.db.execute("SELECT count(*) FROM quota WHERE created>=?", (day_start,)).fetchone()[0]
            minute = self.db.execute("SELECT count(*) FROM quota WHERE created>=?", (now-60,)).fetchone()[0]
            if daily >= self.cfg.daily_analysis_limit:
                raise CopilotError("已达到每日分析次数上限（UTC 日界），未调用模型。")
            if minute >= self.cfg.per_minute_limit:
                raise CopilotError("已达到每分钟分析次数上限，请稍后重试。")
            self.db.execute("INSERT INTO quota VALUES (?)", (now,))

    def prune(self) -> None:
        # Keep metadata for seven days; no message text is stored.
        cutoff = time.time() - 7*86400
        with self.db:
            self.db.execute("DELETE FROM requests WHERE created<?", (cutoff,))
            self.db.execute("DELETE FROM quota WHERE created<?", (cutoff,))

@contextmanager
def instance_lock(cfg: Config):
    cfg.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(cfg.state_dir, 0o700)
    with (cfg.state_dir / "service.lock").open("a+") as handle:
        os.chmod(cfg.state_dir / "service.lock", 0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CopilotError("服务已在运行；未启动第二个实例。") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
