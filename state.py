"""Small, persistent delivery journal. It contains no webhook credentials."""
import json
import os
from pathlib import Path
import sqlite3
import time

from schedule import JOB_TIMEOUT_SECONDS


def state_path():
    configured = os.environ.get("MARKET_SENTIMENT_STATE_DIR")
    mount = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
    if os.environ.get("RAILWAY_ENVIRONMENT_ID") and not mount:
        raise RuntimeError("A Railway volume is required for persistent delivery state")
    return Path(configured or mount or ".state") / "sentiment.sqlite3"


class State:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, status TEXT NOT NULL,
                updated REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                message_id TEXT, error TEXT);
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        with self.db:
            self.db.execute("DELETE FROM runs WHERE updated < ?", (time.time() - 35*86400,))

    def close(self):
        self.db.close()

    def run(self, key):
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (key,)).fetchone()
        return dict(row) if row else None

    def can_run(self, key):
        row = self.run(key)
        if row is None:
            return True
        if row["status"] in ("sent", "sending", "uncertain") or row["attempts"] >= 3:
            return False
        return row["status"] == "failed" or time.time() - row["updated"] > JOB_TIMEOUT_SECONDS + 30

    def claim(self, key):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if not self.can_run(key):
                return False
            self.db.execute("""
                INSERT INTO runs(id,status,updated,attempts) VALUES(?,'running',?,1)
                ON CONFLICT(id) DO UPDATE SET status='running',updated=excluded.updated,
                    attempts=runs.attempts+1,error=NULL
            """, (key, time.time()))
        return True

    def before_send(self, key):
        with self.db:
            result = self.db.execute(
                "UPDATE runs SET status='sending',updated=? WHERE id=? AND status='running'",
                (time.time(), key))
            if result.rowcount != 1:
                raise RuntimeError("Delivery slot is already claimed or sent")

    def sent(self, key, message_id, updates):
        with self.db:
            self.db.execute("UPDATE runs SET status='sent',updated=?,message_id=? WHERE id=?",
                            (time.time(), message_id, key))
            for name, value in updates.items():
                self.db.execute("INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) "
                                "DO UPDATE SET value=excluded.value", (name, json.dumps(value)))

    def fail(self, key, error, *, rejected=False):
        with self.db:
            self.db.execute("""UPDATE runs SET status=CASE
                WHEN status='sending' AND ?=0 THEN 'uncertain' ELSE 'failed' END,
                updated=?,error=? WHERE id=? AND status IN ('running','sending')""",
                (int(rejected), time.time(), error[:200], key))

    def get(self, name):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (name,)).fetchone()
        return json.loads(row[0]) if row else None
