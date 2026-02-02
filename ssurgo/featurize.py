# ssurgo/featurize.py

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

def horizon_aggregates(hz_rows, frag_by_chkey, tex_by_chkey):
    """
    Build per-cokey engineered features from chorizon + chfrags + textures.

    Returns: dict[cokey] -> {
        "K_surface", "coarse_frag_pct",
        "ksat_0_100","bd_0_20","om_0_20",
        "clay_0_20","sand_0_20","silt_0_20",
        "surface_texcl"
    }
    """
    by_ck = {}
    for h in hz_rows:
        ck = str(h.get("cokey"))
        if ck is None:
            continue
        by_ck.setdefault(ck, []).append({
            "top": _to_float(h.get("hzdept_r")),
            "bot": _to_float(h.get("hzdepb_r")),
            "kffact": _to_float(h.get("kffact")),
            "kwfact": _to_float(h.get("kwfact")),
            "ksat_r": _to_float(h.get("ksat_r")),
            "db_r": _to_float(h.get("dbthirdbar_r")),
            "om_r": _to_float(h.get("om_r")),
            "clay": _to_float(h.get("claytotal_r")),
            "sand": _to_float(h.get("sandtotal_r")),
            "silt": _to_float(h.get("silttotal_r")),
            "frag": float(frag_by_chkey.get(str(h.get("chkey")), 0.0) or 0.0),
            "texcl": tex_by_chkey.get(str(h.get("chkey")))
        })

    out = {}
    for ck, hzs in by_ck.items():
        # keep only valid, nonzero-thickness horizons
        hzs = [h for h in hzs if h["top"] is not None and h["bot"] is not None and h["bot"] > h["top"]]
        if not hzs:
            continue

        def tw_mean(key, a, b):
            num = den = 0.0
            for hh in hzs:
                top = max(hh["top"], a)
                bot = min(hh["bot"], b)
                ov = max(0.0, bot - top)
                if ov <= 0:
                    continue
                v = hh.get(key)
                if v is not None:
                    num += v * ov
                    den += ov
            return (num / den) if den > 0 else None

        # K_surface over 0–20 cm using kffact/kwfact fallback
        k_num = k_den = 0.0
        for hh in hzs:
            top = max(hh["top"], 0.0)
            bot = min(hh["bot"], 20.0)
            ov = max(0.0, bot - top)
            if ov <= 0:
                continue
            K = hh["kffact"] if hh["kffact"] is not None else hh["kwfact"]
            if K is not None:
                k_num += K * ov
                k_den += ov
        K_surface = (k_num / k_den) if k_den > 0 else None

        # coarse fragments 0–100 cm
        f_num = f_den = 0.0
        for hh in hzs:
            top = max(hh["top"], 0.0)
            bot = min(hh["bot"], 100.0)
            ov = max(0.0, bot - top)
            if ov <= 0:
                continue
            f_num += (hh["frag"] or 0.0) * ov
            f_den += ov
        coarse = (f_num / f_den) if f_den > 0 else 0.0

        # earliest (shallowest) texture class present
        surface_tex = None
        shallow_tex = sorted([(hh["top"], hh.get("texcl")) for hh in hzs if hh.get("texcl")], key=lambda t: t[0])
        if shallow_tex:
            surface_tex = shallow_tex[0][1]

        out[ck] = {
            "K_surface": K_surface,
            "coarse_frag_pct": coarse,
            "ksat_0_100": tw_mean("ksat_r", 0.0, 100.0),
            "bd_0_20":   tw_mean("db_r",   0.0, 20.0),
            "om_0_20":   tw_mean("om_r",   0.0, 20.0),
            "clay_0_20": tw_mean("clay",   0.0, 20.0),
            "sand_0_20": tw_mean("sand",   0.0, 20.0),
            "silt_0_20": tw_mean("silt",   0.0, 20.0),
            "surface_texcl": surface_tex,
        }
    return out
