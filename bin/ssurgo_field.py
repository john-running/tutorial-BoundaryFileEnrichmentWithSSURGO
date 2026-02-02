#!/usr/bin/env python3
# bin/ssurgo_field.py
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import argparse, json
from ssurgo import (
    read_field_union, field_area_eqm2, mukeys_for_bbox, area_shares_wfs_gml,
    components_muaggatt, horizons_by_cokeys, chfrags_sum_by_chkeys, textures_by_chkeys,
    corestrictions_by_cokeys, compaction_interpretation_by_cokeys,
    horizon_aggregates, compute_summary, fetch_interpretations
)
try:
    from ssurgo.r_factor import r_value_at  # optional dependency
except Exception:
    r_value_at = None

def main():
    ap = argparse.ArgumentParser(description="Field → SSURGO summary")
    ap.add_argument("--field", required=True, help="Path to field shapefile (.shp)")
    ap.add_argument("--out", required=True, help="Path to write JSON summary")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    geom, id_fields = read_field_union(args.field)
    field_area = field_area_eqm2({"geom": geom, "id_fields": id_fields})

    # Optional: RUSLE R-factor lookup from GeoTIFF at centroid
    rusle_r = None
    try:
        tif_path = os.getenv("R_FACTOR_TIF")
        if tif_path and os.path.exists(tif_path) and r_value_at is not None:
            c = geom.centroid
            lon, lat = float(c.x), float(c.y)
            rusle_r = r_value_at(lon, lat, tif_path)
            if args.debug:
                print(f"[r-factor] centroid=({lon:.6f},{lat:.6f}) value={rusle_r}")
    except Exception as e:
        if args.debug:
            print(f"[r-factor] error: {e}")
        rusle_r = None

    # Step 1: MUKEY list
    mukeys = mukeys_for_bbox(geom.bounds)

    # Step 2: MUKEY area shares via WFS-GML (may return {})
    shares = area_shares_wfs_gml(geom, debug=args.debug)

    # Step 3: tabular pulls
    rows = components_muaggatt(mukeys)
    if not rows:
        obj={"id_fields": id_fields, "mukeys": mukeys, "rows": [], "summary": {}, "qa_info":{"have_true_area_shares": bool(shares), "field_area_m2": field_area}}
        obj.setdefault("climate", {})["rusle_r"] = rusle_r
        _save(args.out, obj); 
        if not args.quiet: print("\nNo component rows returned.")
        return

    # Horizons + features
    cokeys=[str(r.get("cokey")) for r in rows if r.get("cokey") is not None]
    hz = horizons_by_cokeys(cokeys)
    chkeys=[str(h.get("chkey")) for h in hz if h.get("chkey") is not None]
    fr = chfrags_sum_by_chkeys(chkeys)
    tx = textures_by_chkeys(chkeys)
    feats = horizon_aggregates(hz, fr, tx)

    restr = corestrictions_by_cokeys(cokeys)
    compi = {}
    try:
        compi = compaction_interpretation_by_cokeys(cokeys)
    except Exception:
        compi = {}
    extras = {}
    try:
        extras = fetch_interpretations(cokeys)
    except Exception:
        extras = {}
    cart = {}
    try:
        from ssurgo.sda_client import fetch_cart_interps_by_cokey
        cart = fetch_cart_interps_by_cokey(cokeys)
    except Exception:
        cart = {}

    # augment rows
    for r in rows:
        ck=str(r.get("cokey"))
        f=feats.get(ck,{})
        r["K_surface"]=f.get("K_surface"); r["coarse_frag_pct"]=f.get("coarse_frag_pct")
        r["ksat_0_100"]=f.get("ksat_0_100"); r["bd_0_20"]=f.get("bd_0_20")
        r["om_0_20"]=f.get("om_0_20"); r["clay_0_20"]=f.get("clay_0_20")
        r["sand_0_20"]=f.get("sand_0_20"); r["silt_0_20"]=f.get("silt_0_20")
        r["surface_texcl"]=f.get("surface_texcl")
        if ck in compi: r["compaction_susceptibility"]=compi[ck].get("compaction_rating")
        if ck in restr:
            r["restrictive_depth_cm"]=restr[ck]["depth_cm"]
            r["restrictive_kind"]=restr[ck]["kind"]
        # Additive CART interpretation fields
        e = extras.get(ck, {})
        c = cart.get(ck, {})
        r["wei"] = e.get("wei")
        r["omd_rating"] = c.get("omd_rating") if c.get("omd_rating") is not None else e.get("omd_rating")
        r["aso_rating"] = c.get("aso_rating") if c.get("aso_rating") is not None else e.get("aso_rating")
        r["aggstab_rating"] = c.get("aggstab_rating") if c.get("aggstab_rating") is not None else e.get("aggstab_rating")

    if args.debug:
        total = len(rows)
        c_omd = sum(1 for rr in rows if rr.get("omd_rating"))
        c_aso = sum(1 for rr in rows if rr.get("aso_rating"))
        c_agg = sum(1 for rr in rows if rr.get("aggstab_rating"))
        print(f"[interp] OMD:{c_omd}/{total}  ASO:{c_aso}/{total}  AGG:{c_agg}/{total}")

    summary = compute_summary(rows, shares if shares else None)
    obj={"id_fields": id_fields, "mukeys": sorted(mukeys), "rows": rows, "summary": summary,
         "qa_info":{"have_true_area_shares": bool(shares), "field_area_m2": field_area,     "mukey_shares": shares}}
    obj.setdefault("climate", {})["rusle_r"] = rusle_r
    _save(args.out, obj)

    if not args.quiet:
        _print_qa(id_fields, field_area, shares, rows)

def _save(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path,"w",encoding="utf-8") as f:
        json.dump(obj,f,indent=2)
    print(f"\n[saved] {path}")

def _to_float(x):
    try:
        if x is None: return None
        s = str(x).strip()
        if not s or s.upper() == "N/A": return None
        return float(s)
    except Exception:
        return None

def _fmt(x, nd=2):
    v = _to_float(x)
    return f"{v:.{nd}f}" if v is not None else "NA"

def _print_qa(id_fields, field_area_m2, shares, rows):
    print("\n=== QA: Field Identity ===")
    for k, v in id_fields.items():
        print(f"- {k}: {v}")
    print(f"- Field area (equal-area m²): {_fmt(field_area_m2,2)}")

    print("\n=== QA: MUKEY Shares ===")
    if shares:
        for mk, s in sorted(shares.items(), key=lambda kv: -kv[1]):
            print(f"MUKEY {mk}: {_fmt(100*s,2)}%")
    else:
        uniq = sorted({int(r['mukey']) for r in rows if r.get('mukey') is not None})
        eq = 100.0/len(uniq) if uniq else 0.0
        for mk in uniq:
            print(f"MUKEY {mk}: equal-weight ~ {_fmt(eq,2)}%")

    print("\n=== QA: Dominant Component by MUKEY (by comppct_r) ===")
    by_mk = {}
    for r in rows:
        mk = int(r["mukey"]) if r.get("mukey") is not None else None
        if mk is None: continue
        by_mk.setdefault(mk, []).append(r)

    for mk, rs in by_mk.items():
        top = max(rs, key=lambda rr: (_to_float(rr.get("comppct_r")) or 0.0))
        comp = (top.get("compname") or "").strip()
        comp_pct = _fmt(top.get("comppct_r"))
        slope = _fmt(top.get("slope_r"))
        hydgrp = (top.get("hydgrpdcd") or "NA")
        mu_share = _fmt(100 * (shares.get(mk, 0.0)))
        print(f"MUKEY {mk}: {comp or 'NA'}  comp%={comp_pct}  slope%={slope}  hydgrp={hydgrp}  (MU share={mu_share}%)")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("\nFATAL:", e)
        sys.exit(1)
