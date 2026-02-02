# ssurgo/aggregate.py

def _to_float(x):
    try:
        if x is None:
            return None
        s = str(x).strip()
        if not s or s.upper() == "N/A":
            return None
        return float(s)
    except Exception:
        return None

# tokens to detect “fine-ish” textural families from taxonomic class name
FINE_TOKENS = {
    "fine", "fine-loamy", "fine-silty",
    "loam", "silt", "clay",
    "silty clay", "sandy clay",
    "clay loam", "silty clay loam", "sandy clay loam",
    "silt loam"
}

def _equal_shares_from_rows(rows):
    """Fallback if MUKEY area shares are missing."""
    mukeys = sorted({int(r["mukey"]) for r in rows if r.get("mukey") is not None})
    if not mukeys:
        return {}
    share = 1.0 / len(mukeys)
    return {mk: share for mk in mukeys}

def compute_summary(rows, mukey_shares=None):
    """
    Nested weighting:
      - within each MUKEY, weight components by comppct_r
      - across MUKEYs, weight by mukey_shares (or equal-share fallback)
    Returns a dict with area_weighted metrics and QA fields.
    """
    area_share = mukey_shares if (mukey_shares and sum(mukey_shares.values()) > 0) else _equal_shares_from_rows(rows)

    total_w = 0.0
    w_sum_for_wt = 0.0
    ann_num = spr_num = 0.0
    fine_w = 0.0

    acc = {
        "slope": 0.0,
        "K": 0.0,
        "T": 0.0,
        "frag": 0.0,
        "ksat": 0.0,
        "bd": 0.0,
        "om": 0.0,
    }
    w_sum = 0.0
    tex_buckets = {}

    for r in rows:
        comp = (_to_float(r.get("comppct_r")) or 0.0) / 100.0
        mk = int(r.get("mukey")) if r.get("mukey") is not None else None
        mu = area_share.get(mk, 0.0)
        w = comp * mu
        if w <= 0:
            continue

        total_w += w
        w_sum += w

        v = _to_float(r.get("slope_r"));         acc["slope"] += w * v if v is not None else 0.0
        v = _to_float(r.get("K_surface"));       acc["K"]     += w * v if v is not None else 0.0
        v = _to_float(r.get("tfact"));           acc["T"]     += w * v if v is not None else 0.0
        v = _to_float(r.get("coarse_frag_pct")); acc["frag"]  += w * v if v is not None else 0.0
        v = _to_float(r.get("ksat_0_100"));      acc["ksat"]  += w * v if v is not None else 0.0
        v = _to_float(r.get("bd_0_20"));         acc["bd"]    += w * v if v is not None else 0.0
        v = _to_float(r.get("om_0_20"));         acc["om"]    += w * v if v is not None else 0.0

        ann = _to_float(r.get("wtdepannmin"))
        spr = _to_float(r.get("wtdepaprjunmin"))
        if ann is not None or spr is not None:
            w_sum_for_wt += w
            if ann is not None: ann_num += w * ann
            if spr is not None: spr_num += w * spr

        tx = (r.get("taxclname") or "").lower()
        matched = next((tok for tok in FINE_TOKENS if tok in tx), None)
        if matched:
            fine_w += w
            tex_buckets[matched] = tex_buckets.get(matched, 0.0) + w

    wt_ann = (ann_num / w_sum_for_wt) if w_sum_for_wt else None
    wt_spr = (spr_num / w_sum_for_wt) if w_sum_for_wt else None
    wt_min = None
    if wt_ann is not None and wt_spr is not None:
        wt_min = min(wt_ann, wt_spr)
    elif wt_ann is not None:
        wt_min = wt_ann
    elif wt_spr is not None:
        wt_min = wt_spr

    area_weighted = {
        "slope_pct":   (acc["slope"] / w_sum) if w_sum else None,
        "K_surface":   (acc["K"]     / w_sum) if w_sum else None,
        "T":           (acc["T"]     / w_sum) if w_sum else None,
        "coarse_frag_pct": (acc["frag"] / w_sum) if w_sum else None,
        "ksat_0_100":  (acc["ksat"]  / w_sum) if w_sum else None,
        "bd_0_20":     (acc["bd"]    / w_sum) if w_sum else None,
        "om_0_20":     (acc["om"]    / w_sum) if w_sum else None,
    }

    tex_mode = max(tex_buckets, key=tex_buckets.get) if tex_buckets else None
    fine_pct = (100.0 * fine_w / total_w) if total_w else None
    shallow = (wt_min is not None) and (wt_min < 30.0)  # inches

    return {
        "weighted_wt_annual_in": wt_ann,
        "weighted_wt_aprjun_in": wt_spr,
        "wt_min_in_used_for_gate": wt_min,
        "texture_mode_weighted": tex_mode,
        "fine_or_loam_taxonomy_pct": fine_pct,
        "assumed_tillage_present": True,
        "compaction_relevant": bool(tex_mode) and shallow,
        "area_weighted": area_weighted,
        "qa": {
            "sum_weight": w_sum,
            "sum_mukey_area_share": sum(area_share.values()) if area_share else 0.0,
            "units": {"wt_depth": "inches", "slope": "percent"},
        },
    }
