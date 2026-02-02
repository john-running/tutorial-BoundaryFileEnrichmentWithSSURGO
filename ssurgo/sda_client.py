# ssurgo/sda_client.py
import time
import os
import textwrap
import requests

SDA_URL = "https://sdmdataaccess.nrcs.usda.gov/Tabular/post.rest"

def _sda(sql: str) -> dict:
    sql = textwrap.dedent(sql).strip()
    if os.environ.get("SDA_DEBUG") == "1":
        print("[SDA] SQL:\n" + sql)
    r = requests.post(
        SDA_URL,
        json={"service": "query", "request": "query", "format": "JSON", "query": sql},
        timeout=90,
    )
    r.raise_for_status()
    return r.json()

def _map_table(j: dict, expected_cols: list[str]) -> list[dict]:
    tbl = j.get("Table") or []
    if not tbl:
        return []
    if isinstance(tbl[0], dict):
        return tbl
    fields = j.get("TableFields") or j.get("Fields") or []
    if fields and all(isinstance(r, list) for r in tbl):
        return [dict(zip(fields, r)) for r in tbl]
    if expected_cols and all(isinstance(r, list) for r in tbl):
        out = []
        for row in tbl:
            d = {}
            for i, v in enumerate(row):
                d[expected_cols[i] if i < len(expected_cols) else f"col_{i}"] = v
            out.append(d)
        return out
    return [{f"col_{i}": v for i, v in enumerate(row)} for row in tbl]

# -------------------- Component + muaggatt --------------------

JOIN_COLS = [
    "c.mukey",
    "c.cokey",
    "c.compname",
    "c.comppct_r",
    "c.taxclname",
    "c.taxorder",
    "c.taxsubgrp",
    "c.slope_r",
    "c.tfact",
    "c.wei",
    "m.wtdepannmin",
    "m.wtdepaprjunmin",
    "m.drclassdcd",
    "m.hydgrpdcd",
]
_EXPECTED = [x.split(".")[1] for x in JOIN_COLS]

def components_muaggatt(mukeys: list[int]) -> list[dict]:
    """Return component rows joined with muaggatt for the given MUKEYs."""
    out: list[dict] = []
    if not mukeys:
        return out
    for i in range(0, len(mukeys), 200):
        subset = ",".join(map(str, mukeys[i:i+200]))
        sql = (
            f"SELECT {', '.join(JOIN_COLS)} "
            "FROM component c "
            "JOIN muaggatt m ON m.mukey = c.mukey "
            f"WHERE c.mukey IN ({subset})"
        )
        out.extend(_map_table(_sda(sql), _EXPECTED))
        time.sleep(0.03)
    return out

# -------------------- Horizons + fragments + textures --------------------

def horizons_by_cokeys(cokeys: list[str]) -> list[dict]:
    out: list[dict] = []
    if not cokeys:
        return out
    for i in range(0, len(cokeys), 200):
        subset = ",".join(map(str, cokeys[i:i+200]))
        sql = f"""
        SELECT chkey, cokey, hzdept_r, hzdepb_r, kffact, kwfact, ksat_r,
               dbthirdbar_r, om_r, claytotal_r, sandtotal_r, silttotal_r
        FROM chorizon
        WHERE cokey IN ({subset})
        """
        out.extend(
            _map_table(
                _sda(sql),
                ["chkey","cokey","hzdept_r","hzdepb_r","kffact","kwfact","ksat_r",
                 "dbthirdbar_r","om_r","claytotal_r","sandtotal_r","silttotal_r"]
            )
        )
    return out

def chfrags_sum_by_chkeys(chkeys: list[str]) -> dict:
    out: dict[str, float] = {}
    if not chkeys:
        return out
    for i in range(0, len(chkeys), 500):
        subset = ",".join(map(str, chkeys[i:i+500]))
        sql = f"""
        SELECT chkey, SUM(fragvol_r) AS fragvol_sum
        FROM chfrags
        WHERE chkey IN ({subset})
        GROUP BY chkey
        """
        for r in _map_table(_sda(sql), ["chkey","fragvol_sum"]):
            out[str(r.get("chkey"))] = r.get("fragvol_sum")
    return out

def textures_by_chkeys(chkeys: list[str]) -> dict:
    out: dict[str, str] = {}
    if not chkeys:
        return out
    for i in range(0, len(chkeys), 500):
        subset = ",".join(map(str, chkeys[i:i+500]))
        sql = f"""
        SELECT g.chkey, t.texcl
        FROM chtexturegrp g
        JOIN chtexture t ON t.chtgkey = g.chtgkey
        WHERE g.chkey IN ({subset})
          AND (g.rvindicator = 'Yes' OR g.rvindicator = 'yes')
        """
        for r in _map_table(_sda(sql), ["chkey","texcl"]):
            out[str(r.get("chkey"))] = (r.get("texcl") or "").strip()
    return out

# -------------------- Restrictions + interpretations --------------------

def corestrictions_by_cokeys(cokeys: list[str]) -> dict:
    kinds = (
        "Fragipan","Duripan","Lithic bedrock","Paralithic bedrock",
        "Cemented pan","Densic material","Petrocalcic"
    )
    out: dict[str, dict] = {}
    if not cokeys:
        return out
    for i in range(0, len(cokeys), 300):
        subset = ",".join(map(str, cokeys[i:i+300]))
        sql = f"""
        SELECT cokey, reskind, resdept_r
        FROM corestrictions
        WHERE cokey IN ({subset})
          AND reskind IN ({",".join("'" + k + "'" for k in kinds)})
        """
        rows = _map_table(_sda(sql), ["cokey","reskind","resdept_r"])
        for r in rows:
            ck = str(r.get("cokey"))
            d  = r.get("resdept_r")
            k  = (r.get("reskind") or "").strip()
            if d is None:
                continue
            cur = out.get(ck)
            if cur is None or d < cur["depth_cm"]:
                out[ck] = {"depth_cm": d, "kind": k}
    return out

def compaction_interpretation_by_cokeys(cokeys: list[str]) -> dict:
    out: dict[str, dict] = {}
    if not cokeys:
        return out
    for i in range(0, len(cokeys), 200):
        subset = ",".join(map(str, cokeys[i:i+200]))
        sql = f"""
        SELECT
          cokey,
          COALESCE(mrulename, rulename) AS rule_name,
          interphrc, interphr,
          interplrc, interplr
        FROM cointerp
        WHERE cokey IN ({subset})
          AND UPPER(COALESCE(mrulename, rulename)) LIKE '%COMPACTION%'
        """
        rows = _map_table(_sda(sql), ["cokey","rule_name","interphrc","interphr","interplrc","interplr"])
        scored = []
        for r in rows:
            name = (r.get("rule_name") or "").upper()
            score = 3 if ("SUSCEPTIBILITY" in name and "COMPACTION" in name) else \
                    2 if ("SOH" in name and "COMPACTION" in name) else \
                    1 if ("COMPACTION" in name) else 0
            scored.append((str(r.get("cokey")), score, r))
        for ck, _, r in sorted(scored, key=lambda t: -t[1]):
            if ck in out:
                continue
            cls = (r.get("interphrc") or r.get("interplrc") or "").strip()
            out[ck] = {
                "compaction_rating": cls if cls else "Not rated",
                "interphr": r.get("interphr") or r.get("interplr"),
            }
    return out


# -------------------- Additional interpretations for CART RCs --------------------

def fetch_interpretations(cokeys: list[str]) -> dict:
    """
    Fetch extra interpretation fields per component (cokey):
      - wei (component.wei numeric)
      - omd_rating (Organic Matter Depletion interpretation)
      - aso_rating (Aerobic Soil Organisms / Soil Organism Habitat)
      - aggstab_rating (Aggregate Stability interpretation)

    Returns: dict[cokey] = {"wei": float|None, "omd_rating": str|None, "aso_rating": str|None, "aggstab_rating": str|None}
    """
    out: dict[str, dict] = {}
    if not cokeys:
        return out

    # 1) WEI from component
    for i in range(0, len(cokeys), 300):
        subset = ",".join(map(str, cokeys[i:i+300]))
        sql = f"""
        SELECT cokey, wei
        FROM component
        WHERE cokey IN ({subset})
        """
        rows = _map_table(_sda(sql), ["cokey","wei"])
        for r in rows:
            ck = str(r.get("cokey"))
            try:
                wei_val = float(r.get("wei")) if r.get("wei") is not None else None
            except Exception:
                wei_val = None
            out.setdefault(ck, {})["wei"] = wei_val

    # Helper to choose best interpretation row by rule name tokens
    def _choose_interp(rows: list[dict], tokens: list[str]) -> dict|None:
        scored: list[tuple[int, dict]] = []
        for r in rows:
            name = (r.get("mrulename") or r.get("rulename") or "").upper()
            score = sum(1 for t in tokens if t in name)
            if score:
                scored.append((score, r))
        if not scored:
            return None
        scored.sort(key=lambda t: -t[0])
        return scored[0][1]

    # Generic fetch for an interpretation by tokens
    def _interp_by_tokens(cokey_subset: list[str], tokens: list[str]) -> dict[str, str|None]:
        subset = ",".join(map(str, cokey_subset))
        sql = f"""
        SELECT cokey, COALESCE(mrulename, rulename) AS rule_name,
               interphrc, interphr, interplrc, interplr
        FROM cointerp
        WHERE cokey IN ({subset})
        """
        rows = _map_table(_sda(sql), ["cokey","rule_name","interphrc","interphr","interplrc","interplr"])
        by_ck: dict[str, list[dict]] = {}
        for r in rows:
            by_ck.setdefault(str(r.get("cokey")), []).append(r)
        sel: dict[str, str|None] = {}
        for ck, rs in by_ck.items():
            best = _choose_interp(rs, tokens)
            if best:
                cls = (best.get("interphrc") or best.get("interplrc") or "").strip()
                sel[ck] = cls.title() if cls else None
            else:
                sel[ck] = None
        return sel

    # 2) Organic Matter Depletion (OMD)
    tokens_omd = ["ORGANIC", "MATTER", "DEPLETION"]
    for i in range(0, len(cokeys), 200):
        subset = cokeys[i:i+200]
        vals = _interp_by_tokens(subset, tokens_omd)
        for ck, v in vals.items():
            out.setdefault(ck, {})["omd_rating"] = v

    # 3) Aerobic Soil Organisms / Soil Organism Habitat
    tokens_aso = ["AEROBIC", "SOIL", "ORGANISM"]
    for i in range(0, len(cokeys), 200):
        subset = cokeys[i:i+200]
        vals = _interp_by_tokens(subset, tokens_aso)
        for ck, v in vals.items():
            out.setdefault(ck, {})["aso_rating"] = v

    # 4) Aggregate Stability
    tokens_agg = ["AGGREGATE", "STABILITY"]
    for i in range(0, len(cokeys), 200):
        subset = cokeys[i:i+200]
        vals = _interp_by_tokens(subset, tokens_agg)
        for ck, v in vals.items():
            out.setdefault(ck, {})["aggstab_rating"] = v

    return out


def fetch_cart_interps_by_cokey(cokeys: list[str]) -> dict:
    """
    Fetch three CART-aligned component interpretations by exact mrulename:
      - omd_rating:  'Agricultural Organic Matter Depletion'
      - aso_rating:  'Limitations for Aerobic Soil Organisms'
      - aggstab_rating: 'Agricultural Aggregate Stability'
    Returns dict[cokey] = {omd_rating, aso_rating, aggstab_rating}
    """
    out: dict[str, dict] = {}
    if not cokeys:
        return out

    def _norm_omd(r: str|None) -> str|None:
        s = (r or "").strip()
        if not s:
            return None
        sl = s.lower()
        if 'not rated' in sl:
            return 'Not rated'
        if 'moderately high' in sl:
            return 'Moderately High'
        if 'high' in sl and 'moderate' not in sl:
            return 'High'
        if 'moderate' in sl:
            return 'Moderate'
        if 'low' in sl:
            return 'Low'
        return s

    def _norm_aso(r: str|None) -> str|None:
        s = (r or "").strip()
        if not s:
            return None
        sl = s.lower()
        if 'not rated' in sl:
            return 'Not rated'
        if 'very limited' in sl:
            return 'Very limited'
        if 'somewhat limited' in sl:
            return 'Somewhat limited'
        if 'not limited' in sl:
            return 'Not limited'
        return s

    def _norm_agg(r: str|None) -> str|None:
        s = (r or "").strip()
        if not s:
            return None
        sl = s.lower()
        if 'not rated' in sl:
            return 'Not rated'
        if 'high' in sl:
            return 'High'
        if 'moderate' in sl:
            return 'Moderate'
        if 'low' in sl:
            return 'Low'
        return s

    CHUNK = 400
    # Case-insensitive LIKE patterns cover minor naming variations across datasets
    like_omd = "%ORGANIC%MATTER%DEPLETION%"
    like_aso = "%AEROBIC%SOIL%ORGANISM%"
    like_aso2 = "%SOIL%ORGANISM%HABITAT%"
    like_agg = "%AGGREGATE%STABILITY%"

    for i in range(0, len(cokeys), CHUNK):
        subset = cokeys[i:i+CHUNK]
        # cokey is numeric in SDA; pass unquoted
        inlist = ",".join(str(ck) for ck in subset)
        sql = f"""
        SELECT ci.cokey,
               COALESCE(ci.mrulename, ci.rulename) AS mrulename,
               ci.interphrc,
               ci.interplrc,
               ci.nullpropdatabool,
               ci.defpropdatabool,
               ci.incpropdatabool
        FROM cointerp ci
        WHERE ci.cokey IN ({inlist})
          AND (
            UPPER(COALESCE(ci.mrulename, ci.rulename)) LIKE '{like_omd}' OR
            UPPER(COALESCE(ci.mrulename, ci.rulename)) LIKE '{like_aso}' OR
            UPPER(COALESCE(ci.mrulename, ci.rulename)) LIKE '{like_aso2}' OR
            UPPER(COALESCE(ci.mrulename, ci.rulename)) LIKE '{like_agg}'
          );
        """
        rows = _map_table(_sda(sql), ["cokey","mrulename","interphrc","interplrc","nullpropdatabool","defpropdatabool","incpropdatabool"])
        for r in rows:
            ck = str(r.get('cokey'))
            name = (r.get('mrulename') or '').strip()
            rate = (r.get('interphrc') or r.get('interplrc') or '').strip()
            tgt = out.setdefault(ck, {})
            up = name.upper()
            if 'ORGANIC' in up and 'MATTER' in up and 'DEPLETION' in up:
                tgt['omd_rating'] = _norm_omd(rate)
            elif ('AEROBIC' in up and 'SOIL' in up and 'ORGANISM' in up) or ('SOIL' in up and 'ORGANISM' in up and 'HABITAT' in up):
                tgt['aso_rating'] = _norm_aso(rate)
            elif ('AGGREGATE' in up and 'STABILITY' in up):
                tgt['aggstab_rating'] = _norm_agg(rate)

    return out


def list_interpretation_names_by_cokeys(cokeys: list[str]) -> list[str]:
    """Return distinct mrulename/rulename values observed for the given cokeys (for debugging)."""
    names = set()
    if not cokeys:
        return []
    CHUNK = 400
    for i in range(0, len(cokeys), CHUNK):
        subset = cokeys[i:i+CHUNK]
        inlist = ",".join(str(ck) for ck in subset)
        sql = f"""
        SELECT DISTINCT COALESCE(ci.mrulename, ci.rulename) AS name
        FROM cointerp ci
        WHERE ci.cokey IN ({inlist});
        """
        rows = _map_table(_sda(sql), ["name"])
        for r in rows:
            nm = (r.get("name") or "").strip()
            if nm:
                names.add(nm)
    return sorted(names)
    return out


# -------------------- Freshness / dates (sacatalog, legend, text recdates) --------------------

_SACAT_FIELDS: dict | None = None

def _detect_sacatalog_fields() -> dict:
    """
    Detect actual sacatalog column names for version/date fields across SDA variants.
    Returns mapping with keys: saversion, saverest, tabularversion, tabularverest
    Values are the actual column names present (or None if not found).
    """
    global _SACAT_FIELDS
    if _SACAT_FIELDS is not None:
        return _SACAT_FIELDS

    # Probe the schema once by attempting minimal SELECTs per candidate column
    def col_exists(colname: str) -> bool:
        try:
            _ = _sda(f"SELECT TOP 1 {colname} FROM sacatalog")
            return True
        except Exception:
            return False

    # Build fully qualified references with alias `s.` when used in joins
    def choose(*candidates: str) -> str | None:
        for c in candidates:
            if col_exists(c):
                return c
        return None

    # Try common variants seen across SDA deployments
    saversion = choose("saversion", "sa_version")
    saverest = choose("saverest")
    # Some deployments expose short names
    tabularversion = choose("tabularversion", "tabversion")
    tabularverest = choose("tabularverest", "tabverest")

    _SACAT_FIELDS = {
        "saversion": saversion,
        "saverest": saverest,
        "tabularversion": tabularversion,
        "tabularverest": tabularverest,
    }
    return _SACAT_FIELDS

def freshness_by_mukeys(mukeys: list[int]) -> dict[int, dict]:
    """
    Return per-MUKEY date/version metadata used for freshness:
      - sacatalog.saverest, sacatalog.tabularverest, sacatalog.saversion, sacatalog.tabularversion
      - legend.cordate
      - mapunittext.max_recdate (max recdate per mukey)
      - componenttext.max_recdate (max recdate among components under the MU)
      - chtext.max_recdate (max horizon text recdate under the MU)

    All fields are optional; missing ones may not be populated for a given area.
    """
    out: dict[int, dict] = {}
    if not mukeys:
        return out

    # 1) sacatalog + legend by MUKEY (via legend.lkey)
    mukey_to_area: dict[int, str] = {}
    cols = _detect_sacatalog_fields()
    sel_cols = [
        "m.mukey",
        (f"s.{cols['saversion']} AS saversion" if cols.get("saversion") else "CAST(NULL AS int) AS saversion"),
        (f"s.{cols['saverest']} AS saverest" if cols.get("saverest") else "CAST(NULL AS datetime) AS saverest"),
        (f"s.{cols['tabularversion']} AS tabularversion" if cols.get("tabularversion") else "CAST(NULL AS int) AS tabularversion"),
        (f"s.{cols['tabularverest']} AS tabularverest" if cols.get("tabularverest") else "CAST(NULL AS datetime) AS tabularverest"),
        "l.cordate",
        "l.areasymbol",
    ]
    for i in range(0, len(mukeys), 300):
        subset = ",".join(map(str, mukeys[i:i+300]))
        sql = f"""
        SELECT {", ".join(sel_cols)}
        FROM mapunit m
        JOIN legend l ON l.lkey = m.lkey
        JOIN sacatalog s ON s.areasymbol = l.areasymbol
        WHERE m.mukey IN ({subset});
        """
        rows = _map_table(_sda(sql), [
            "mukey","saversion","saverest","tabularversion","tabularverest","cordate","areasymbol"
        ])
        if os.environ.get("SDA_DEBUG") == "1":
            print(f"[SDA] sacatalog/legend rows: {len(rows)} for chunk of {len(mukeys[i:i+300])}")
        for r in rows:
            mk = int(r.get("mukey")) if r.get("mukey") is not None else None
            if mk is None:
                continue
            tgt = out.setdefault(mk, {})
            tgt["saversion"] = r.get("saversion")
            tgt["saverest"] = r.get("saverest")
            tgt["tabularversion"] = r.get("tabularversion")
            tgt["tabularverest"] = r.get("tabularverest")
            tgt["legend_cordate"] = r.get("cordate")
            if r.get("areasymbol"):
                mukey_to_area[mk] = r.get("areasymbol")
        time.sleep(0.02)

    # 1b) Fallback: if some MUKEYs missing, fetch sacatalog by areasymbol
    missing_mukeys = [mk for mk in mukeys if mk not in out]
    if missing_mukeys:
        # fetch mukey→areasymbol mapping for missing
        for i in range(0, len(missing_mukeys), 400):
            subset = ",".join(map(str, missing_mukeys[i:i+400]))
            sql = f"""
            SELECT m.mukey, l.areasymbol
            FROM mapunit m
            JOIN legend l ON l.lkey = m.lkey
            WHERE m.mukey IN ({subset});
            """
            rows = _map_table(_sda(sql), ["mukey","areasymbol"])
            for r in rows:
                mk = int(r.get("mukey")) if r.get("mukey") is not None else None
                if mk is not None and r.get("areasymbol"):
                    mukey_to_area[mk] = r.get("areasymbol")
            time.sleep(0.02)
        areas = sorted({a for mk,a in mukey_to_area.items() if mk in missing_mukeys})
        for i in range(0, len(areas), 400):
            subset = ",".join(f"'{a}'" for a in areas[i:i+400])
            cols = _detect_sacatalog_fields()
            sel_area = [
                "areasymbol",
                (f"{cols['saversion']} AS saversion" if cols.get("saversion") else "CAST(NULL AS int) AS saversion"),
                (f"{cols['saverest']} AS saverest" if cols.get("saverest") else "CAST(NULL AS datetime) AS saverest"),
                (f"{cols['tabularversion']} AS tabularversion" if cols.get("tabularversion") else "CAST(NULL AS int) AS tabularversion"),
                (f"{cols['tabularverest']} AS tabularverest" if cols.get("tabularverest") else "CAST(NULL AS datetime) AS tabularverest"),
            ]
            sql = f"""
            SELECT {", ".join(sel_area)}
            FROM sacatalog
            WHERE areasymbol IN ({subset});
            """
            rows = _map_table(_sda(sql), ["areasymbol","saversion","saverest","tabularversion","tabularverest"])
            by_area = {r.get("areasymbol"): r for r in rows}
            for mk in missing_mukeys:
                area = mukey_to_area.get(mk)
                if not area:
                    continue
                sr = by_area.get(area)
                if not sr:
                    continue
                tgt = out.setdefault(mk, {})
                tgt.setdefault("saversion", sr.get("saversion"))
                tgt.setdefault("saverest", sr.get("saverest"))
                tgt.setdefault("tabularversion", sr.get("tabularversion"))
                tgt.setdefault("tabularverest", sr.get("tabularverest"))
            time.sleep(0.02)

    # 2) mapunittext.recdate (max per MU)
    for i in range(0, len(mukeys), 400):
        subset = ",".join(map(str, mukeys[i:i+400]))
        sql = f"""
        SELECT mut.mukey, MAX(mut.recdate) AS max_recdate
        FROM mapunittext mut
        WHERE mut.mukey IN ({subset})
        GROUP BY mut.mukey;
        """
        try:
            rows = _map_table(_sda(sql), ["mukey","max_recdate"])
            if os.environ.get("SDA_DEBUG") == "1":
                print(f"[SDA] mapunittext rows: {len(rows)} for chunk of {len(mukeys[i:i+400])}")
            for r in rows:
                mk = int(r.get("mukey")) if r.get("mukey") is not None else None
                if mk is None:
                    continue
                out.setdefault(mk, {})["mapunittext_latest_recdate"] = r.get("max_recdate")
        except Exception:
            # Some datasets may lack mapunittext or recdate; ignore errors
            pass
        time.sleep(0.02)

    # 3) componenttext.recdate → aggregate max per MU via component (optional table)
    for i in range(0, len(mukeys), 200):
        subset = ",".join(map(str, mukeys[i:i+200]))
        sql = f"""
        SELECT c.mukey, MAX(ct.recdate) AS max_recdate
        FROM component c
        JOIN componenttext ct ON ct.cokey = c.cokey
        WHERE c.mukey IN ({subset})
        GROUP BY c.mukey;
        """
        try:
            rows = _map_table(_sda(sql), ["mukey","max_recdate"])
            if os.environ.get("SDA_DEBUG") == "1":
                print(f"[SDA] componenttext rows: {len(rows)} for chunk of {len(mukeys[i:i+200])}")
            for r in rows:
                mk = int(r.get("mukey")) if r.get("mukey") is not None else None
                if mk is None:
                    continue
                out.setdefault(mk, {})["componenttext_latest_recdate"] = r.get("max_recdate")
        except Exception:
            # Some datasets may lack componenttext; ignore errors
            pass
        time.sleep(0.02)

    # 4) chtext.recdate → aggregate max per MU via chorizon → component
    for i in range(0, len(mukeys), 150):
        subset = ",".join(map(str, mukeys[i:i+150]))
        sql = f"""
        SELECT c.mukey, MAX(ht.recdate) AS max_recdate
        FROM component c
        JOIN chorizon h  ON h.cokey = c.cokey
        JOIN chtext   ht ON ht.chkey = h.chkey
        WHERE c.mukey IN ({subset})
        GROUP BY c.mukey;
        """
        try:
            rows = _map_table(_sda(sql), ["mukey","max_recdate"])
            if os.environ.get("SDA_DEBUG") == "1":
                print(f"[SDA] chtext rows: {len(rows)} for chunk of {len(mukeys[i:i+150])}")
            for r in rows:
                mk = int(r.get("mukey")) if r.get("mukey") is not None else None
                if mk is None:
                    continue
                out.setdefault(mk, {})["chtext_latest_recdate"] = r.get("max_recdate")
        except Exception:
            # Some datasets may lack chtext; ignore errors here
            pass
        time.sleep(0.02)

    return out
