import json
import os
import random
import sqlite3
import threading
import time
from pathlib import Path

from .config import get_setting

DEFAULT_SHARED_CACHE_PATH = ".cache/masterdata_cache.sqlite3"


def _coerce_float(value, default):
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_ttl(value, default):
    ttl = _coerce_float(value, default)
    if ttl < 0:
        return 0.0
    return ttl


def apply_ttl_jitter(ttl_sec: float, jitter_ratio: float = 0.1) -> float:
    ttl = max(float(ttl_sec or 0.0), 0.0)
    ratio = max(float(jitter_ratio or 0.0), 0.0)
    if ttl <= 0 or ratio <= 0:
        return ttl
    spread = ttl * ratio
    value = ttl + random.uniform(-spread, spread)
    return max(value, 0.0)


class SharedSqliteCache:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._l1_lock = threading.Lock()
        self._l1_cache = {}
        self._db_init_lock = threading.Lock()
        self._initialized = False
        self._ensure_initialized()

    def _ensure_initialized(self):
        if self._initialized:
            return
        with self._db_init_lock:
            if self._initialized:
                return
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS cache_entries (
                        cache_key TEXT PRIMARY KEY,
                        payload TEXT NOT NULL,
                        fresh_until REAL NOT NULL,
                        stale_until REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS cache_locks (
                        lock_key TEXT PRIMARY KEY,
                        owner TEXT NOT NULL,
                        expires_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )
                    """
                )
            self._initialized = True

    def _connect(self):
        return sqlite3.connect(str(self.db_path), timeout=2.0, isolation_level=None)

    @staticmethod
    def _state_from_timestamps(now: float, fresh_until: float, stale_until: float):
        if now < fresh_until:
            return "fresh"
        if now < stale_until:
            return "stale"
        return "miss"

    def _set_l1_entry(self, cache_key, entry):
        with self._l1_lock:
            self._l1_cache[cache_key] = entry

    def _get_l1_entry(self, cache_key):
        with self._l1_lock:
            return self._l1_cache.get(cache_key)

    def get_cache_entry(self, cache_key: str, *, allow_stale: bool = True):
        now = time.time()
        cached = self._get_l1_entry(cache_key)
        if cached:
            state = self._state_from_timestamps(
                now, cached["fresh_until"], cached["stale_until"]
            )
            if state == "fresh" or (allow_stale and state == "stale"):
                return {"state": state, **cached}

        row = None
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT payload, fresh_until, stale_until, updated_at
                    FROM cache_entries
                    WHERE cache_key = ?
                    """,
                    (cache_key,),
                ).fetchone()
        except Exception:
            row = None

        if not row:
            return {
                "state": "miss",
                "data": None,
                "fresh_until": 0.0,
                "stale_until": 0.0,
                "updated_at": 0.0,
            }

        payload_text, fresh_until, stale_until, updated_at = row
        state = self._state_from_timestamps(now, fresh_until, stale_until)
        if state == "miss" or (state == "stale" and not allow_stale):
            return {
                "state": "miss",
                "data": None,
                "fresh_until": fresh_until,
                "stale_until": stale_until,
                "updated_at": updated_at,
            }

        try:
            payload = json.loads(payload_text)
        except Exception:
            payload = None

        entry = {
            "data": payload,
            "fresh_until": float(fresh_until or 0.0),
            "stale_until": float(stale_until or 0.0),
            "updated_at": float(updated_at or 0.0),
        }
        self._set_l1_entry(cache_key, entry)
        return {"state": state, **entry}

    def set_cache_entry(
        self,
        cache_key: str,
        data,
        *,
        soft_ttl: float,
        stale_ttl: float,
        jitter_ratio: float = 0.1,
    ):
        now = time.time()
        soft = apply_ttl_jitter(_normalize_ttl(soft_ttl, 0.0), jitter_ratio=jitter_ratio)
        stale = apply_ttl_jitter(
            _normalize_ttl(stale_ttl, max(soft, 0.0)), jitter_ratio=jitter_ratio
        )
        if stale < soft:
            stale = soft
        fresh_until = now + soft
        stale_until = now + stale
        payload_text = json.dumps(data)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO cache_entries (cache_key, payload, fresh_until, stale_until, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload=excluded.payload,
                    fresh_until=excluded.fresh_until,
                    stale_until=excluded.stale_until,
                    updated_at=excluded.updated_at
                """,
                (cache_key, payload_text, fresh_until, stale_until, now),
            )
        entry = {
            "data": data,
            "fresh_until": fresh_until,
            "stale_until": stale_until,
            "updated_at": now,
        }
        self._set_l1_entry(cache_key, entry)
        return {"state": "fresh", **entry}

    def acquire_lock(
        self,
        lock_key: str,
        *,
        owner: str | None = None,
        lock_ttl_sec: float = 30.0,
        wait_timeout_sec: float = 0.0,
        poll_interval_sec: float = 0.05,
    ):
        owner_value = owner or f"{os.getpid()}-{threading.get_ident()}-{time.time_ns()}"
        ttl = max(float(lock_ttl_sec or 0.0), 1.0)
        wait_timeout = max(float(wait_timeout_sec or 0.0), 0.0)
        poll_interval = max(float(poll_interval_sec or 0.0), 0.01)
        deadline = time.time() + wait_timeout

        while True:
            now = time.time()
            expires_at = now + ttl
            acquired = False
            try:
                with self._connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute(
                        "DELETE FROM cache_locks WHERE expires_at <= ?",
                        (now,),
                    )
                    try:
                        conn.execute(
                            """
                            INSERT INTO cache_locks (lock_key, owner, expires_at, updated_at)
                            VALUES (?, ?, ?, ?)
                            """,
                            (lock_key, owner_value, expires_at, now),
                        )
                        acquired = True
                    except sqlite3.IntegrityError:
                        acquired = False
                    conn.execute("COMMIT")
            except Exception:
                acquired = False

            if acquired:
                return True, owner_value

            if time.time() >= deadline:
                return False, owner_value

            time.sleep(poll_interval)

    def release_lock(self, lock_key: str, owner: str):
        if not owner:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM cache_locks WHERE lock_key = ? AND owner = ?",
                    (lock_key, owner),
                )
        except Exception:
            pass


_DEFAULT_CACHE = None
_DEFAULT_CACHE_LOCK = threading.Lock()


def get_default_shared_cache() -> SharedSqliteCache:
    global _DEFAULT_CACHE
    if _DEFAULT_CACHE is not None:
        return _DEFAULT_CACHE
    with _DEFAULT_CACHE_LOCK:
        if _DEFAULT_CACHE is not None:
            return _DEFAULT_CACHE
        db_path = (
            get_setting("MASTER_PRODUCTS_SHARED_CACHE_PATH") or DEFAULT_SHARED_CACHE_PATH
        )
        _DEFAULT_CACHE = SharedSqliteCache(db_path)
    return _DEFAULT_CACHE
