import os
import sys
import time
import uuid
import shutil
import tempfile
import subprocess
import pathlib
import threading
import json
from flask import Flask, request, jsonify, render_template, send_file

# Load .env into process env if present (optional)
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

from db.sqlite_store import (
    append_run_log,
    create_farm,
    create_run,
    ensure_schema,
    get_farm,
    get_run,
    get_sqlite_path,
    list_farms,
    list_results_for_run,
    list_runs_for_farm,
    update_run_status,
)

from fetch_zip_and_ingest import safe_unzip

APP_ROOT = pathlib.Path(__file__).resolve().parent
INGEST = str(APP_ROOT / "ssurgo_ingest_to_sqlite.py")

PORT = int(os.environ.get("PORT", "8080"))
SQLITE_PATH = get_sqlite_path()

app = Flask(__name__)
ensure_schema(SQLITE_PATH)

# simple in-memory job store (ephemeral)
JOBS: dict[str, dict] = {}


def _require_auth_if_configured() -> None:
    # Auth removed: no-op
    return


def _append_job_log(job_id: str, line: str) -> None:
    line2 = (line or "").rstrip("\n")
    buf = JOBS[job_id]["log"]
    buf.append(line2)
    if len(buf) > 2000:
        del buf[: len(buf) - 2000]

    run_id = JOBS[job_id].get("run_id")
    if run_id:
        try:
            append_run_log(SQLITE_PATH, run_id, line2)
        except Exception:
            pass


def _run_upload_job(job_id: str, run_id: str, farm_id: str, zip_path: str, work_dir: str, verbose: bool) -> None:
    JOBS[job_id].update(status="running", started_at=time.time())
    update_run_status(SQLITE_PATH, run_id, "running")
    _append_job_log(job_id, f"[server] run_id={run_id} farm_id={farm_id} db='{SQLITE_PATH}'")
    try:
        safe_unzip(zip_path, work_dir)

        cmd = [
            sys.executable,
            "-u",
            INGEST,
            "--farm-id",
            farm_id,
            "--run-id",
            run_id,
            "--root",
            work_dir,
            "--db-path",
            SQLITE_PATH,
        ]
        if verbose:
            cmd.append("--verbose")

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["SQLITE_PATH"] = SQLITE_PATH

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        assert proc.stdout is not None
        for line in proc.stdout:
            _append_job_log(job_id, line)
        code = proc.wait()

        if code == 0:
            update_run_status(SQLITE_PATH, run_id, "succeeded")
        else:
            update_run_status(SQLITE_PATH, run_id, "failed")
        JOBS[job_id].update(status=("succeeded" if code == 0 else "failed"), exit_code=code, ended_at=time.time())
    except Exception as e:
        _append_job_log(job_id, f"[server] worker error: {e}")
        update_run_status(SQLITE_PATH, run_id, "failed")
        JOBS[job_id].update(status="failed", exit_code=-1, ended_at=time.time())
    finally:
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return "ok", 200


@app.get("/db")
def download_db():
    # Download the SQLite DB so users can inspect records locally.
    p = pathlib.Path(SQLITE_PATH).resolve()
    if not p.exists():
        return jsonify(error="db file not found"), 404
    return send_file(str(p), as_attachment=True, download_name=p.name)


@app.get("/farms")
def farms_list():
    return jsonify(farms=list_farms(SQLITE_PATH)), 200


@app.get("/farms/<farm_id>")
def farm_page(farm_id: str):
    farm = get_farm(SQLITE_PATH, farm_id)
    if not farm:
        return "Farm not found", 404
    return render_template("farm.html", farm=farm)


@app.post("/farms")
def farms_create():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify(error="name is required"), 400
    try:
        farm = create_farm(SQLITE_PATH, name)
    except Exception as e:
        return jsonify(error=f"could not create farm: {e}"), 400
    return jsonify(farm=farm), 201


@app.get("/farms/<farm_id>/runs")
def runs_list(farm_id: str):
    return jsonify(farm_id=farm_id, runs=list_runs_for_farm(SQLITE_PATH, farm_id)), 200


def _maybe_parse_results(rows, parse_json: bool):
    if not parse_json:
        return rows
    out = []
    for r in rows:
        d = dict(r)
        for k in ("boundary_meta_json", "huc_json", "summary_json", "snapshot_json"):
            if k in d and isinstance(d.get(k), str):
                try:
                    d[k] = json.loads(d[k])
                except Exception:
                    pass
        out.append(d)
    return out


@app.get("/farms/<farm_id>/results")
def farm_results(farm_id: str):
    """
    Convenience endpoint: fetch results for a farm.
    - By default returns latest run's results.
    - Optional query params:
      - run_id=<uuid> to choose a specific run
      - include_snapshot=1 to include snapshot_json
      - parse=1 to JSON-decode *_json fields
    """
    run_id = (request.args.get("run_id") or "").strip() or None
    include_snapshot = (request.args.get("include_snapshot") or "").strip().lower() in {"1", "true", "yes", "y"}
    parse_json = (request.args.get("parse") or "").strip().lower() in {"1", "true", "yes", "y"}

    runs = list_runs_for_farm(SQLITE_PATH, farm_id)
    if not runs:
        return jsonify(error="no runs for farm", farm_id=farm_id), 404

    if run_id is None:
        run_id = runs[0]["id"]
    rows = list_results_for_run(SQLITE_PATH, run_id, include_snapshot=include_snapshot)
    rows = _maybe_parse_results(rows, parse_json=parse_json)
    return jsonify(farm_id=farm_id, run_id=run_id, results=rows, runs=runs), 200


@app.get("/runs/<run_id>")
def run_get(run_id: str):
    r = get_run(SQLITE_PATH, run_id)
    if not r:
        return jsonify(error="not found"), 404
    return jsonify(run=r), 200


@app.get("/runs/<run_id>/results")
def run_results(run_id: str):
    include_snapshot = (request.args.get("include_snapshot") or "").strip().lower() in {"1", "true", "yes", "y"}
    parse_json = (request.args.get("parse") or "").strip().lower() in {"1", "true", "yes", "y"}
    rows = list_results_for_run(SQLITE_PATH, run_id, include_snapshot=include_snapshot)
    rows = _maybe_parse_results(rows, parse_json=parse_json)
    return jsonify(run_id=run_id, results=rows), 200


@app.post("/farms/<farm_id>/ingest-upload")
def ingest_upload(farm_id: str):
    verbose = (request.form.get("verbose") or "").strip().lower() in {"1", "true", "yes", "y"}
    if "file" not in request.files:
        return jsonify(error="missing file field"), 400
    f = request.files["file"]
    if not f or not f.filename:
        return jsonify(error="missing filename"), 400
    if not f.filename.lower().endswith(".zip"):
        return jsonify(error="file must be a .zip"), 400

    # Create DB run record
    run = create_run(SQLITE_PATH, farm_id=farm_id, original_zip_name=f.filename, status="queued")
    run_id = run["id"]

    # Temp work dir per job, always deleted when done
    work_dir = tempfile.mkdtemp(prefix="upload_zip_")
    zip_path = os.path.join(work_dir, "upload.zip")
    f.save(zip_path)

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {
        "status": "queued",
        "exit_code": None,
        "log": [],
        "started_at": None,
        "ended_at": None,
        "farm_id": farm_id,
        "run_id": run_id,
    }
    t = threading.Thread(
        target=_run_upload_job,
        args=(job_id, run_id, farm_id, zip_path, work_dir, verbose),
        daemon=True,
    )
    t.start()
    return jsonify(job_id=job_id, run_id=run_id, status="queued"), 202


@app.get("/jobs/<job_id>")
def job_status(job_id: str):
    j = JOBS.get(job_id)
    if not j:
        return jsonify(error="not found"), 404
    return jsonify(job_id=job_id, **j), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)


