import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from typing import Optional

import requests

REPO_ROOT = pathlib.Path(__file__).resolve().parent
INGEST_SCRIPT = str(REPO_ROOT / "ssurgo_ingest_to_sqlite.py")


def download(url: str, dest_path: str) -> None:
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                if chunk:
                    f.write(chunk)


def _allowed_member(p: pathlib.Path) -> bool:
    # Skip macOS resource fork files and __MACOSX directory entries
    if p.name.startswith("._") or "__MACOSX" in p.parts:
        return False
    # Include .cpg when present (encoding sidecar)
    return (p.suffix.lower() in {".shp", ".shx", ".dbf", ".prj", ".cpg"})


def safe_unzip(zip_path: str, dest_dir: str) -> None:
    with zipfile.ZipFile(zip_path) as zf:
        for m in zf.infolist():
            p = pathlib.Path(m.filename)
            if p.is_absolute() or ".." in p.parts:
                continue
            if not _allowed_member(p):
                continue
            target = pathlib.Path(dest_dir) / p
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(m) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)


def run_ingest(db_path: str, farm_id: str, run_id: str, root_dir: str, verbose: bool) -> int:
    cmd: list[str] = [
        sys.executable,
        "-u",  # unbuffered child for realtime logs
        INGEST_SCRIPT,
        "--farm-id",
        farm_id,
        "--run-id",
        run_id,
        "--root",
        root_dir,
        "--db-path",
        db_path,
    ]
    if verbose:
        cmd.append("--verbose")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="", flush=True)
    return proc.wait()


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch a ZIP of shapefiles, extract, and run SSURGO ingestion.")
    ap.add_argument("--farm-id", required=True, help="Farm UUID (from /farms).")
    ap.add_argument("--run-id", help="Optional run UUID; if omitted, one is created in SQLite.")
    ap.add_argument("--db-path", help="Path to SQLite db file (or set SQLITE_PATH).")
    ap.add_argument("--zip-url", required=True, help="HTTP(S) URL of the ZIP.")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--keep-tmp", action="store_true", help="Keep temp directory (debug)")
    args = ap.parse_args()

    from db.sqlite_store import create_run, ensure_schema, get_sqlite_path

    db_path = get_sqlite_path(args.db_path)
    ensure_schema(db_path)
    run_id = args.run_id or create_run(db_path, farm_id=args.farm_id, original_zip_name="bundle.zip", status="queued")["id"]

    tmpdir = tempfile.mkdtemp(prefix="ingest_zip_")
    try:
        zpath = os.path.join(tmpdir, "bundle.zip")
        if args.verbose:
            print(f"[download] {args.zip_url} -> {zpath}", flush=True)
        download(args.zip_url, zpath)
        if args.verbose:
            print(f"[extract] {zpath} -> {tmpdir}", flush=True)
        safe_unzip(zpath, tmpdir)
        if args.verbose:
            print(f"[ingest] farm_id={args.farm_id} run_id={run_id} root={tmpdir}", flush=True)
        code = run_ingest(db_path, args.farm_id, run_id, tmpdir, args.verbose)
        if code != 0:
            sys.exit(code)
    finally:
        if not args.keep_tmp:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()


