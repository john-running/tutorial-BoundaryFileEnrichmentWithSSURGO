# soil_pipeline_runner.py
import os
from typing import Dict, Any

from ssurgo.field_io import read_field_union, read_field_metadata
from ssurgo.mukey_lookup import mukeys_for_bbox
from ssurgo.mukey_weights import area_shares_wfs_gml
from ssurgo.sda_client import (
    components_muaggatt, horizons_by_cokeys, chfrags_sum_by_chkeys,
    textures_by_chkeys, corestrictions_by_cokeys, compaction_interpretation_by_cokeys,
    fetch_interpretations, fetch_cart_interps_by_cokey, freshness_by_mukeys
)
from ssurgo.featurize import horizon_aggregates
from ssurgo.aggregate import compute_summary


def compute_ssurgo_snapshot_for_shapefile(shp_path: str) -> Dict[str, Any]:
    """
    Compute a SSURGO snapshot for one shapefile.

    Returns a dict with:
      - id_fields (attrs from the shapefile)
      - boundary_meta (file/CRS/centroid/bbox/area/feature_count/id_fields)
      - mukeys (all MUKEYs touching the bbox)
      - rows (component+muaggatt joined and horizon-engineered fields)
      - summary (nested area-weighted summary)
      - qa_info (flags + mukey_shares if overlaps were computed)
    """
    # Field geometry + attributes
    geom, id_fields = read_field_union(shp_path)
    boundary_meta = read_field_metadata(shp_path, geom, id_fields)

    # Step 1: MUKEY set for the bbox
    mukeys = mukeys_for_bbox(geom.bounds)

    # Step 2: True MUKEY area shares via WFS GML (robust parser with axis flip)
    mukey_shares = area_shares_wfs_gml(geom, debug=False)  # dict[int,float], may be {}

    # Step 3: Tabular pulls + horizon engineering
    rows = components_muaggatt(mukeys)
    cokeys = [str(r.get("cokey")) for r in rows if r.get("cokey") is not None]

    hz = horizons_by_cokeys(cokeys)
    chkeys = [str(h.get("chkey")) for h in hz if h.get("chkey") is not None]
    fr = chfrags_sum_by_chkeys(chkeys)
    tx = textures_by_chkeys(chkeys)
    feats = horizon_aggregates(hz, fr, tx)

    restr = corestrictions_by_cokeys(cokeys)
    try:
        compi = compaction_interpretation_by_cokeys(cokeys)
    except Exception:
        compi = {}
    try:
        extras = fetch_interpretations(cokeys)
    except Exception:
        extras = {}
    try:
        cart = fetch_cart_interps_by_cokey(cokeys)
    except Exception:
        cart = {}

    # Augment rows
    for r in rows:
        ck = str(r.get("cokey"))
        f = feats.get(ck, {})
        r["K_surface"]       = f.get("K_surface")
        r["coarse_frag_pct"] = f.get("coarse_frag_pct")
        r["ksat_0_100"]      = f.get("ksat_0_100")
        r["bd_0_20"]         = f.get("bd_0_20")
        r["om_0_20"]         = f.get("om_0_20")
        r["clay_0_20"]       = f.get("clay_0_20")
        r["sand_0_20"]       = f.get("sand_0_20")
        r["silt_0_20"]       = f.get("silt_0_20")
        r["surface_texcl"]   = f.get("surface_texcl")
        if ck in compi:
            r["compaction_susceptibility"] = compi[ck].get("compaction_rating")
        if ck in restr:
            r["restrictive_depth_cm"] = restr[ck]["depth_cm"]
            r["restrictive_kind"]     = restr[ck]["kind"]
        # New additive fields
        e = extras.get(ck, {})
        r["wei"] = e.get("wei")
        # Merge CART-specific interpretations (priority over heuristic fetch, if any)
        c = cart.get(ck, {})
        r["omd_rating"] = c.get("omd_rating") if c.get("omd_rating") is not None else e.get("omd_rating")
        r["aso_rating"] = c.get("aso_rating") if c.get("aso_rating") is not None else e.get("aso_rating")
        r["aggstab_rating"] = c.get("aggstab_rating") if c.get("aggstab_rating") is not None else e.get("aggstab_rating")

    # Step 4: Weighted summary (falls back to equal MU weights if shares empty)
    summary = compute_summary(rows, mukey_shares if mukey_shares else None)

    # Step 5: Freshness metadata by MUKEY (optional)
    disable_fresh = os.environ.get("DISABLE_SSA_FRESHNESS") == "1"
    if disable_fresh:
        freshness = {}
    else:
        try:
            freshness = freshness_by_mukeys(mukeys)
        except Exception:
            freshness = {}
    # Compute field-level soil_data_date and supporting fields
    def _to_date(s):
        try:
            return str(s) if s else None
        except Exception:
            return None
    dates_all = []
    freshness_rollup = {
        "survey_area_version_date": None,
        "tabular_data_date": None,
        "legend_correlation_date": None,
        "mapunit_text_latest_recdate": None,
        "component_text_latest_recdate": None,
        "horizon_text_latest_recdate": None,
        "soil_data_date": None,
        "oldest_date": None,
        "soil_data_age_years": None,
        "soil_data_date_source": None,
    }
    # Collect candidate dates across MUs
    for mk in mukeys:
        f = freshness.get(int(mk)) or {}
        for k_src, k_dst in [
            ("saverest", "survey_area_version_date"),
            ("tabularverest", "tabular_data_date"),
            ("legend_cordate", "legend_correlation_date"),
            ("mapunittext_latest_recdate", "mapunit_text_latest_recdate"),
            ("componenttext_latest_recdate", "component_text_latest_recdate"),
            ("chtext_latest_recdate", "horizon_text_latest_recdate"),
        ]:
            v = _to_date(f.get(k_src))
            if v:
                # keep the max per field for each category
                cur = freshness_rollup.get(k_dst)
                if not cur or v > cur:
                    freshness_rollup[k_dst] = v
                dates_all.append((k_dst, v))
    if dates_all:
        # compute max/min across all collected dates
        vals = [d for _, d in dates_all]
        soil_date = max(vals)
        oldest = min(vals)
        freshness_rollup["soil_data_date"] = soil_date
        freshness_rollup["oldest_date"] = oldest
        freshness_rollup["soil_data_date_source"] = next((k for k, v in dates_all if v == soil_date), None)
        # naive age in years using 365-day years; strings compare lexicographically if ISO yyyy-mm-dd
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(soil_date.replace("Z", "+00:00")).astimezone(timezone.utc)
            now = datetime.now(timezone.utc)
            freshness_rollup["soil_data_age_years"] = round((now - dt).days / 365.0, 2)
        except Exception:
            pass

    return {
        "id_fields": id_fields,
        "boundary_meta": boundary_meta,            # <-- NEW
        "mukeys": mukeys,
        "rows": rows,
        "summary": summary,
        "soil_data_freshness": freshness_rollup,
        "qa_info": {
            "have_true_area_shares": bool(mukey_shares),
            "mukey_shares": mukey_shares,         # dict[int,float]; sums ~1 if present
        },
    }
