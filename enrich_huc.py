#!/usr/bin/env python3
# enrich_huc.py
import os, argparse, json, time
from typing import Any, Dict, Optional, List

import requests

# NOTE: This file originally included a Supabase-backed CLI to write HUC values
# into a remote database. This project version is Supabase-free; the CLI portion
# has been removed. The helper functions below (`query_wbd_point` and
# `check_surface_water_proximity`) remain and are used by the ingestion pipeline.

WBD_BASE = "https://hydro.nationalmap.gov/arcgis/rest/services/wbd/MapServer"
# Common WBD MapServer layout:
# 0 = WBDLine (non-polygon, won't intersect with point queries)
# 1 = HU2 (Regions), 2 = HU4 (Subregions), 3 = HU6 (Basins),
# 4 = HU8 (Subbasins), 5 = HU10 (Watersheds), 6 = HU12 (Subwatersheds)
WBD_LAYERS = [
    (1, "huc2", 2),
    (2, "huc4", 4),
    (3, "huc6", 6),
    (4, "huc8", 8),
    (5, "huc10", 10),
    (6, "huc12", 12),
]

def vprint(enabled: bool, *args, **kwargs):
    if enabled:
        print(*args, **kwargs)


def _guess_huc_code_key(attrs: Dict[str, Any], digits: int) -> Optional[str]:
    # Prefer exact like HUC12/HUC10; fallback to HUC_12/HUC_10 or case variants
    for k in attrs.keys():
        k_lower = k.lower()
        if k_lower == f"huc{digits}":
            return k
    for k in attrs.keys():
        k_lower = k.lower()
        if k_lower == f"huc_{digits}":
            return k
    for k in attrs.keys():
        if "huc" in k.lower() and str(digits) in k:
            return k
    return None


def _guess_name_key(attrs: Dict[str, Any]) -> Optional[str]:
    for k in attrs.keys():
        if k.lower() == "name":
            return k
    for k in attrs.keys():
        if "name" in k.lower():
            return k
    return None


def _guess_states(attrs: Dict[str, Any]) -> Optional[str]:
    for k in attrs.keys():
        if k.lower() == "states":
            return str(attrs[k]) if attrs.get(k) is not None else None
    return None


def _get_area_acres(attrs: Dict[str, Any]) -> Optional[float]:
    # Common fields: AreaAcres, AREAACRES, AreaSqKm (convert), Shape_Area (m^2, convert)
    for k in attrs.keys():
        if k.lower() == "areaacres":
            try:
                return float(attrs[k])
            except Exception:
                pass
    for k in attrs.keys():
        if k.lower() in ("areasqkm", "areasq_km", "area_sq_km"):
            try:
                return float(attrs[k]) * 247.10538146717
            except Exception:
                pass
    for k in attrs.keys():
        if k.lower() in ("shape_area", "area_m2"):
            try:
                return float(attrs[k]) * 0.00024710538146717
            except Exception:
                pass
    return None


def query_wbd_point(lon: float, lat: float, verbose: bool = False) -> Dict[str, Any]:
    """
    Query WBD layers 0..5 for the point. Returns dict with per-level entries.
    """
    result: Dict[str, Any] = {}
    geom = json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}})

    def _wbd_request(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        # Retry with exponential backoff on timeouts/5xx
        max_attempts = 4
        base_delay = 0.4
        for attempt in range(1, max_attempts + 1):
            try:
                r = requests.get(url, params=params, timeout=30)
                if 500 <= r.status_code < 600:
                    raise requests.HTTPError(f"{r.status_code} Server Error")
                r.raise_for_status()
                return r.json()
            except Exception as e:
                if attempt == max_attempts:
                    vprint(verbose, f"[wbd] request failed after {attempt} attempts: {e}")
                    return {}
                delay = base_delay * (2 ** (attempt - 1))
                vprint(verbose, f"[wbd] attempt {attempt} failed: {e}; retrying in {delay:.2f}s")
                time.sleep(delay)

    for layer_id, level_key, digits in WBD_LAYERS:
        url = f"{WBD_BASE}/{layer_id}/query"
        params = {
            "f": "json",
            "geometry": geom,
            "geometryType": "esriGeometryPoint",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "*",
            "returnGeometry": "false",
        }
        data = _wbd_request(url, params)
        if not data:
            vprint(verbose, f"[wbd] layer={layer_id} query error: empty/failed response")

        feats = (data.get("features") or []) if isinstance(data, dict) else []
        if not feats:
            vprint(verbose, f"[wbd] layer={layer_id} no features for point.")
            continue

        attrs = feats[0].get("attributes") or {}
        code_key = _guess_huc_code_key(attrs, digits)
        name_key = _guess_name_key(attrs)
        code = str(attrs.get(code_key)) if code_key else None
        name = str(attrs.get(name_key)) if name_key and attrs.get(name_key) is not None else None
        states_str = _guess_states(attrs)
        area_acres = _get_area_acres(attrs)
        if not code_key:
            vprint(verbose, f"[wbd] layer={layer_id} missing code field for HUC{digits}; keys={list(attrs.keys())}")

        entry: Dict[str, Any] = {}
        if code:
            entry["code"] = code
        if name:
            entry["name"] = name
        if states_str:
            states = [s.strip().upper() for s in str(states_str).replace(";", ",").split(",")]
            entry["states"] = [s for s in states if s]
        if area_acres is not None:
            entry["area_acres"] = area_acres

        if entry:
            result[level_key] = entry

        time.sleep(0.08)

    finest = result.get("huc12") or result.get("huc10") or result.get("huc8")
    if finest:
        if "states" in finest:
            seen = set()
            states: List[str] = []
            for s in finest["states"]:
                if s not in seen:
                    states.append(s)
                    seen.add(s)
            result["states"] = states
        if "area_acres" in finest:
            result["area_acres"] = finest["area_acres"]

    result["method"] = "centroid"
    result["source"] = "USGS WBD (National Map MapServer)"
    result["source_url"] = WBD_BASE
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="HUC and surface-water helper utilities (Supabase-free).")
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    print(json.dumps(query_wbd_point(args.lon, args.lat, verbose=args.verbose), indent=2))


def check_surface_water_proximity(lon: float, lat: float, buffer_m: Optional[int] = None, verbose: bool = False) -> Dict[str, Any]:
    """
    Query USGS NHDPlus HR around a point buffer to determine proximity to
    surface-water features. Returns a dict under the shape expected for
    boundary_meta.surface_water_proximity.
    """
    try:
        buf = int(buffer_m or int(os.environ.get("SURFACE_WATER_BUFFER_M", "120")))
    except Exception:
        buf = 120

    base = "https://hydro.nationalmap.gov/arcgis/rest/services/NHDPlus_HR/MapServer"

    def _count(layer: int) -> int:
        url = f"{base}/{layer}/query"
        params = {
            "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "distance": buf,
            "units": "esriSRUnit_Meter",
            "returnCountOnly": "true",
            "f": "json",
        }
        last_err: Optional[Exception] = None
        # Retry with exponential backoff on timeouts/5xx (similar to WBD)
        max_attempts = 4
        for attempt in range(1, max_attempts + 1):
            try:
                r = requests.get(url, params=params, timeout=30)
                if 500 <= r.status_code < 600:
                    raise requests.HTTPError(f"{r.status_code} Server Error")
                r.raise_for_status()
                j = r.json()
                return int((j or {}).get("count", 0))
            except Exception as e:
                last_err = e
                if attempt == max_attempts:
                    break
                time.sleep(0.4 * (2 ** (attempt - 1)))
        if verbose and last_err:
            print(f"[nhd] layer={layer} count error: {last_err}")
        return 0

    try:
        c_flow = _count(2)
        c_water = _count(3)
        proximal = (c_flow > 0 or c_water > 0)
        return {
            "surface_water_proximal": proximal,
            "method": "USGS ArcGIS REST Flowline(2)+Waterbody(3)",
            "buffer_m": buf,
            "counts": {"flowlines": c_flow, "waterbodies": c_water},
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    except Exception as e:
        if verbose:
            print(f"[nhd] proximity error: {e}")
        return {
            "surface_water_proximal": False,
            "method": "USGS ArcGIS REST Flowline(2)+Waterbody(3) (error)",
            "buffer_m": buf,
            "counts": {"flowlines": 0, "waterbodies": 0},
            "error": str(e),
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }


