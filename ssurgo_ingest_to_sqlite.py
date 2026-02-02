import argparse
import os
import time
from typing import Any, Dict, List, Optional

from soil_pipeline_runner import compute_ssurgo_snapshot_for_shapefile

from enrich_huc import query_wbd_point, check_surface_water_proximity

try:
    from ssurgo.r_factor import r_value_at  # type: ignore
except Exception:
    r_value_at = None  # type: ignore

from db.sqlite_store import (
    append_run_log,
    ensure_schema,
    get_sqlite_path,
    insert_result,
    update_run_status,
)


DEFAULT_R_FACTOR_TIF = "/app/data/R-Factor_CONUS.tif"


def vprint(enabled: bool, *args, **kwargs) -> None:
    if enabled:
        print(*args, **kwargs)


def discover_shapefiles(root: str) -> List[str]:
    shp_files: List[str] = []
    for dirpath, _, files in os.walk(root):
        for f in files:
            if f.lower().endswith(".shp"):
                shp_files.append(os.path.join(dirpath, f))
    shp_files.sort()
    return shp_files


def compute_huc_from_snapshot(snapshot: Dict[str, Any], verbose: bool = False) -> Optional[Dict[str, Any]]:
    try:
        bm = snapshot.get("boundary_meta") or {}
        if not isinstance(bm, dict):
            return None
        cen = bm.get("centroid_lonlat")
        if not (isinstance(cen, (list, tuple)) and len(cen) == 2):
            return None
        lon, lat = cen[0], cen[1]
        if lon is None or lat is None:
            return None
        return query_wbd_point(float(lon), float(lat), verbose=verbose)
    except Exception:
        return None


def compute_surface_water_from_snapshot(snapshot: Dict[str, Any], verbose: bool = False) -> Optional[Dict[str, Any]]:
    try:
        bm = snapshot.get("boundary_meta") or {}
        if not isinstance(bm, dict):
            return None
        cen = bm.get("centroid_lonlat")
        if not (isinstance(cen, (list, tuple)) and len(cen) == 2):
            return None
        lon, lat = cen[0], cen[1]
        if lon is None or lat is None:
            return None
        return check_surface_water_proximity(float(lon), float(lat), verbose=verbose)
    except Exception:
        return None


def get_r_factor_tif_path() -> Optional[str]:
    # For student/local runs, simplest: use env path if set; else fall back to Docker-bundled path.
    p = os.environ.get("R_FACTOR_TIF")
    if p and os.path.exists(p):
        return p
    if os.path.exists(DEFAULT_R_FACTOR_TIF):
        return DEFAULT_R_FACTOR_TIF
    return None


def _log(db_path: str, run_id: str, line: str) -> None:
    print(line, flush=True)
    try:
        append_run_log(db_path, run_id, line)
    except Exception:
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest shapefiles into SQLite (stores snapshot+summary per shapefile).")
    ap.add_argument("--farm-id", required=True, help="Farm UUID (from /farms).")
    ap.add_argument("--run-id", required=True, help="Run UUID (from /farms/<farm_id>/ingest-upload).")
    ap.add_argument("--root", required=True, help="Directory to search for .shp files (recursively).")
    ap.add_argument("--db-path", help="Path to SQLite db file (or set SQLITE_PATH).")
    ap.add_argument("--limit", type=int, help="Process at most N shapefiles (pre-filter slice).")
    ap.add_argument("--process-limit", type=int, help="Stop after inserting at most N results.")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    db_path = get_sqlite_path(args.db_path)
    ensure_schema(db_path)

    update_run_status(db_path, args.run_id, "running")
    _log(db_path, args.run_id, f"[info] run_id={args.run_id} farm_id={args.farm_id} db='{db_path}' root='{args.root}'")

    shp_files = discover_shapefiles(args.root)
    if args.limit:
        shp_files = shp_files[: args.limit]
    _log(db_path, args.run_id, f"[info] discovered {len(shp_files)} shapefile(s)")

    processed = 0
    tif_path = get_r_factor_tif_path()
    have_r = bool(tif_path and r_value_at is not None and os.path.exists(tif_path))
    if tif_path:
        _log(db_path, args.run_id, f"[r-factor] tif_path='{tif_path}' enabled={have_r}")
    else:
        _log(db_path, args.run_id, "[r-factor] no tif configured; skipping R-factor")

    for shp in shp_files:
        base = os.path.basename(shp)
        _log(db_path, args.run_id, f"[compute] {base} ...")
        try:
            snapshot = compute_ssurgo_snapshot_for_shapefile(shp)
        except Exception as e:
            _log(db_path, args.run_id, f"[skip] {base} compute error: {e}")
            continue

        # HUC
        huc_payload = compute_huc_from_snapshot(snapshot, verbose=args.verbose)
        # surface water proximity stored under boundary_meta
        try:
            sw = compute_surface_water_from_snapshot(snapshot, verbose=args.verbose)
            if sw is not None and isinstance(snapshot.get("boundary_meta"), dict):
                snapshot["boundary_meta"]["surface_water_proximity"] = sw
        except Exception:
            pass

        # Optional R-factor enrich
        if have_r:
            try:
                bm = snapshot.get("boundary_meta") or {}
                cen = bm.get("centroid_lonlat") if isinstance(bm, dict) else None
                if isinstance(cen, (list, tuple)) and len(cen) == 2 and cen[0] is not None and cen[1] is not None:
                    lon = float(cen[0])
                    lat = float(cen[1])
                    r_val = r_value_at(lon, lat, tif_path)  # type: ignore
                    snapshot.setdefault("climate", {})["rusle_r"] = r_val
                    vprint(args.verbose, f"[r-factor] {base} centroid=({lon:.6f},{lat:.6f}) value={r_val}")
            except Exception as e:
                vprint(args.verbose, f"[r-factor] {base} error: {e}")

        bm2 = snapshot.get("boundary_meta") if isinstance(snapshot.get("boundary_meta"), dict) else None
        summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else None

        try:
            insert_result(
                db_path=db_path,
                run_id=args.run_id,
                shape_file=(bm2.get("file_name") if isinstance(bm2, dict) else None) or base,
                boundary_meta=bm2,
                huc=huc_payload,
                snapshot=snapshot,
                summary=summary,
            )
            _log(db_path, args.run_id, f"[stored] {base}")
        except Exception as e:
            _log(db_path, args.run_id, f"[error] {base} sqlite insert failed: {e}")
            continue

        processed += 1
        if args.process_limit and processed >= args.process_limit:
            _log(db_path, args.run_id, f"[stop] reached process-limit={args.process_limit}")
            break
        time.sleep(0.03)

    update_run_status(db_path, args.run_id, "succeeded")
    _log(db_path, args.run_id, f"[done] stored results for {processed} shapefile(s)")


if __name__ == "__main__":
    main()

