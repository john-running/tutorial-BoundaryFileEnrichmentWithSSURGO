# SSURGO ZIP Ingest (Local-first)

This project ingests field boundary shapefiles and computes soil + hydrologic context using USDA **SSURGO** and USGS services. It’s designed so Purdue students can **clone the repo and run it locally** with minimal setup.

- **Frontend**: a simple HTML page served by Flask (no Node toolchain)
- **Persistence**: **ephemeral SQLite** (default: `./data/app.db`)
- **Workflow**: create a **farm**, upload a **ZIP** of shapefiles, watch logs, view results

---

## Quickstart (Docker)

```bash
docker build -t ssurgo-local .
docker run --rm -p 8080:8080 ssurgo-local
```

Then open `http://localhost:8080/`.

Optional: persist the SQLite file on your host during local development:

```bash
mkdir -p data
docker run --rm -p 8080:8080 -v "$(pwd)/data:/app/data" ssurgo-local
```

---

## Quickstart (Python)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python server.py
```

Open `http://localhost:8080/`.

---

## Using the app

1. **Create a farm** (any name).
2. **Upload a ZIP** that contains shapefile components:
   - required: `.shp`, `.shx`, `.dbf` (and usually `.prj`)
3. The server will:
   - save the upload to a temp directory
   - extract only shapefile files (it ignores `__MACOSX` and similar junk)
   - run the SSURGO pipeline for each `.shp`
   - store results in SQLite
   - **delete the temp upload directory** when the job completes (success or failure)

---

## Output storage (SQLite)

By default the server writes to:

- `./data/app.db`

Override with:

- `SQLITE_PATH=/path/to/app.db`

To download the DB from a running server, open:

- `http://localhost:8080/db`

To get the latest results for a farm as JSON, use:

- `http://localhost:8080/farms/<farm_id>/results?parse=true`

To view a simple “farm page” UI in the browser:

- `http://localhost:8080/farms/<farm_id>`

Schema (3 tables):

- `farms`: farm records
- `runs`: an ingest run per upload, includes `status` and a capped `log` text field
- `results`: one row per shapefile processed, storing:
  - full snapshot JSON (includes `summary`)
  - summary JSON (duplicated for convenience)
  - boundary metadata JSON
  - HUC payload JSON

---

## Optional configuration

### R-factor (optional)

If you have a GeoTIFF for RUSLE R-factor, set:

- `R_FACTOR_TIF=/absolute/path/to/R-Factor_CONUS.tif`

If not set, R-factor enrichment is skipped (everything else still runs).

Note: the default install does **not** include `rasterio` to keep installs simple across machines. If you want R-factor enabled in a local Python environment, install it separately:

```bash
pip install rasterio
```

---

## Project layout (key files)

```
.
├── server.py                   # Flask web service + upload UI + job queue
├── templates/index.html         # Simple in-browser UI
├── soil_pipeline_runner.py      # Computes SSURGO snapshot for one shapefile
├── ssurgo_ingest_to_sqlite.py   # Ingest loop → writes results to SQLite
├── fetch_zip_and_ingest.py      # Optional CLI: download ZIP by URL then ingest into SQLite
├── db/sqlite_store.py           # SQLite schema + helpers
└── ssurgo/                      # Core pipeline modules
```

---

## Fly.io deployment (kept)

This repo still includes `Dockerfile`, `fly.toml`, and a GitHub Action workflow for Fly deployments. SQLite is **ephemeral** unless you mount a Fly volume; for the student use-case this is usually fine.

