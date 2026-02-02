import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_sqlite_path(explicit_path: Optional[str] = None) -> str:
    return explicit_path or os.environ.get("SQLITE_PATH") or os.environ.get("SQLITE_DB") or "./data/app.db"


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(db_path: str) -> None:
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys = ON;")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS farms (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
              id TEXT PRIMARY KEY,
              farm_id TEXT NOT NULL,
              created_at TEXT NOT NULL,
              status TEXT NOT NULL,
              original_zip_name TEXT,
              log TEXT NOT NULL DEFAULT '',
              FOREIGN KEY(farm_id) REFERENCES farms(id)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS results (
              id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              created_at TEXT NOT NULL,
              shape_file TEXT,
              boundary_meta_json TEXT,
              huc_json TEXT,
              snapshot_json TEXT,
              summary_json TEXT,
              FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_runs_farm_id ON runs(farm_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_results_run_id ON results(run_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_results_shape_file ON results(shape_file);")
        conn.commit()
    finally:
        conn.close()


def create_farm(db_path: str, name: str) -> Dict[str, Any]:
    ensure_schema(db_path)
    farm_id = str(uuid.uuid4())
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO farms (id, name, created_at) VALUES (?, ?, ?);",
            (farm_id, name.strip(), _now_iso()),
        )
        conn.commit()
        return {"id": farm_id, "name": name.strip(), "created_at": _now_iso()}
    finally:
        conn.close()


def get_or_create_farm(db_path: str, name: str) -> Dict[str, Any]:
    ensure_schema(db_path)
    name2 = name.strip()
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT id,name,created_at FROM farms WHERE name = ? LIMIT 1;", (name2,))
        row = cur.fetchone()
        if row:
            return dict(row)
        farm_id = str(uuid.uuid4())
        created = _now_iso()
        cur.execute("INSERT INTO farms (id,name,created_at) VALUES (?,?,?);", (farm_id, name2, created))
        conn.commit()
        return {"id": farm_id, "name": name2, "created_at": created}
    finally:
        conn.close()


def list_farms(db_path: str) -> List[Dict[str, Any]]:
    ensure_schema(db_path)
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT id,name,created_at FROM farms ORDER BY created_at DESC;")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_farm(db_path: str, farm_id: str) -> Optional[Dict[str, Any]]:
    ensure_schema(db_path)
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT id,name,created_at FROM farms WHERE id = ? LIMIT 1;", (farm_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_run(db_path: str, farm_id: str, original_zip_name: Optional[str] = None, status: str = "queued") -> Dict[str, Any]:
    ensure_schema(db_path)
    run_id = str(uuid.uuid4())
    created = _now_iso()
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO runs (id,farm_id,created_at,status,original_zip_name,log) VALUES (?,?,?,?,?,?);",
            (run_id, farm_id, created, status, original_zip_name, ""),
        )
        conn.commit()
        return {
            "id": run_id,
            "farm_id": farm_id,
            "created_at": created,
            "status": status,
            "original_zip_name": original_zip_name,
            "log": "",
        }
    finally:
        conn.close()


def update_run_status(db_path: str, run_id: str, status: str) -> None:
    ensure_schema(db_path)
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("UPDATE runs SET status = ? WHERE id = ?;", (status, run_id))
        conn.commit()
    finally:
        conn.close()


def append_run_log(db_path: str, run_id: str, line: str, max_chars: int = 200_000) -> None:
    ensure_schema(db_path)
    clean = (line or "").rstrip("\n") + "\n"
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT log FROM runs WHERE id = ? LIMIT 1;", (run_id,))
        row = cur.fetchone()
        existing = (row["log"] if row and row["log"] is not None else "")
        new_log = existing + clean
        if len(new_log) > max_chars:
            new_log = new_log[-max_chars:]
        cur.execute("UPDATE runs SET log = ? WHERE id = ?;", (new_log, run_id))
        conn.commit()
    finally:
        conn.close()


def get_run(db_path: str, run_id: str) -> Optional[Dict[str, Any]]:
    ensure_schema(db_path)
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT id,farm_id,created_at,status,original_zip_name,log FROM runs WHERE id = ? LIMIT 1;", (run_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_runs_for_farm(db_path: str, farm_id: str) -> List[Dict[str, Any]]:
    ensure_schema(db_path)
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id,farm_id,created_at,status,original_zip_name FROM runs WHERE farm_id = ? ORDER BY created_at DESC;",
            (farm_id,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def insert_result(
    db_path: str,
    run_id: str,
    shape_file: Optional[str],
    boundary_meta: Optional[Dict[str, Any]],
    huc: Optional[Dict[str, Any]],
    snapshot: Optional[Dict[str, Any]],
    summary: Optional[Dict[str, Any]],
) -> str:
    ensure_schema(db_path)
    rid = str(uuid.uuid4())
    created = _now_iso()
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO results
              (id, run_id, created_at, shape_file, boundary_meta_json, huc_json, snapshot_json, summary_json)
            VALUES
              (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                rid,
                run_id,
                created,
                shape_file,
                json.dumps(boundary_meta) if boundary_meta is not None else None,
                json.dumps(huc) if huc is not None else None,
                json.dumps(snapshot) if snapshot is not None else None,
                json.dumps(summary) if summary is not None else None,
            ),
        )
        conn.commit()
        return rid
    finally:
        conn.close()


def list_results_for_run(db_path: str, run_id: str, include_snapshot: bool = False) -> List[Dict[str, Any]]:
    ensure_schema(db_path)
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        if include_snapshot:
            cur.execute(
                """
                SELECT id, run_id, created_at, shape_file, boundary_meta_json, huc_json, summary_json, snapshot_json
                FROM results
                WHERE run_id = ?
                ORDER BY created_at ASC;
                """,
                (run_id,),
            )
        else:
            cur.execute(
                """
                SELECT id, run_id, created_at, shape_file, boundary_meta_json, huc_json, summary_json
                FROM results
                WHERE run_id = ?
                ORDER BY created_at ASC;
                """,
                (run_id,),
            )
        rows = []
        for r in cur.fetchall():
            d = dict(r)
            # keep JSON as strings for API payload size control; callers can json.loads if desired
            rows.append(d)
        return rows
    finally:
        conn.close()

