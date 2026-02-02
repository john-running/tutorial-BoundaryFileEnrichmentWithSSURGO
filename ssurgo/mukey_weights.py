def area_shares_wfs_gml(field_geom, debug=False):
    import requests
    from xml.etree import ElementTree as ET
    from shapely.ops import unary_union, transform as shapely_transform
    from shapely.geometry import Polygon

    WFS_URL = "https://sdmdataaccess.nrcs.usda.gov/Spatial/SDMWGS84Geographic.wfs"
    _GML = "http://www.opengis.net/gml"
    def _ln(tag): return tag.split("}")[-1]

    def _parse_pos_tokens(text):
        raw = (text or "").strip().replace(",", " ")
        return [float(t) for t in raw.split() if t]

    def _ring_from(elem, swap_xy=False):
        for tag in ("posList","pos","coordinates"):
            node = elem.find(f".//{{{_GML}}}{tag}")
            if node is not None and (node.text or "").strip():
                nums = _parse_pos_tokens(node.text)
                if len(nums) < 8: return None
                pts=[]
                for i in range(0,len(nums),2):
                    x,y = nums[i], nums[i+1]
                    pts.append((y,x) if swap_xy else (x,y))
                return pts
        return None

    def _poly_from(elem, swap_xy=False):
        lr = elem.find(f".//{{{_GML}}}LinearRing")
        if lr is None: return None
        # exterior
        ext = elem.find(f".//{{{_GML}}}exterior/{{{_GML}}}LinearRing") or lr
        exterior = _ring_from(ext, swap_xy=swap_xy)
        if not exterior or len(exterior) < 4: return None
        # interiors
        holes=[]
        for il in elem.findall(f".//{{{_GML}}}interior/{{{_GML}}}LinearRing"):
            ring = _ring_from(il, swap_xy=swap_xy)
            if ring and len(ring) >= 4: holes.append(ring)
        try:
            return Polygon(exterior, holes)
        except Exception:
            return None

    def _geom_from_feature(feat, swap_xy=False):
        # Try a bunch of geometry containers
        for tag in ("Polygon","Surface","MultiSurface","MultiPolygon"):
            nodes = feat.findall(f".//{{{_GML}}}{tag}")
            if not nodes: continue
            polys=[]
            for node in nodes:
                if tag == "Polygon":
                    g = _poly_from(node, swap_xy=swap_xy)
                elif tag == "Surface":
                    # gml:Surface has PolygonPatch children with LinearRings
                    patches = node.findall(f".//{{{_GML}}}PolygonPatch")
                    if patches:
                        for p in patches:
                            gp = _poly_from(p, swap_xy=swap_xy)
                            if gp and not gp.is_empty: polys.append(gp)
                        g = None
                    else:
                        g = _poly_from(node, swap_xy=swap_xy)
                else:  # Multi*
                    # Collect all descendant Polygons under the multi container
                    for p in node.findall(f".//{{{_GML}}}Polygon"):
                        gp = _poly_from(p, swap_xy=swap_xy)
                        if gp and not gp.is_empty: polys.append(gp)
                    g = None
                if g is not None and not g.is_empty:
                    polys.append(g)
            if polys:
                try:
                    return unary_union(polys)
                except Exception:
                    pass
        return None

    # Project field to equal-area
    try:
        from pyproj import Transformer
        to_eq = Transformer.from_crs("EPSG:4326","EPSG:5070",always_xy=True)
        field_eq = shapely_transform(to_eq.transform, field_geom)
    except Exception:
        to_eq = None
        field_eq = field_geom

    minx,miny,maxx,maxy = field_geom.bounds
    pad = 1e-4
    minx -= pad; miny -= pad; maxx += pad; maxy += pad
    tries = [("MapunitPoly","xy"),("mapunitpoly","xy"),("MapunitPoly","yx")]

    area_by_mukey={}
    for layer,order in tries:
        bbox = f"{minx},{miny},{maxx},{maxy}" if order=="xy" else f"{miny},{minx},{maxy},{maxx}"
        params = {
            "service":"WFS","version":"1.1.0","request":"GetFeature",
            "typename":layer,"bbox":bbox,"srsName":"EPSG:4326",
            "resultType":"results","maxFeatures":200000
        }
        try:
            r = requests.get(WFS_URL, params=params, timeout=90)
            r.raise_for_status()
            root = ET.fromstring(r.text)

            # Collect feature elems that contain MUKEY
            features=[]
            for fm in root.iter():
                if _ln(fm.tag) in ("featureMember","featureMembers"):
                    for feat in fm.iter():
                        for child in feat:
                            if _ln(child.tag).lower()=="mukey":
                                features.append(feat); break
            if debug: print(f"[weights:gml] layer={layer} feats={len(features)}")

            for pass_swap in (False, True):
                parsed = 0
                intersecting = 0
                tmp={}
                for feat in features:
                    # MUKEY
                    mk=None
                    for e in feat.iter():
                        if _ln(e.tag).lower()=="mukey":
                            t=(e.text or "").strip()
                            if t.isdigit(): mk=int(t); break
                    if mk is None: continue

                    g = _geom_from_feature(feat, swap_xy=pass_swap)
                    if g is None or g.is_empty: continue
                    parsed += 1

                    g_eq = shapely_transform(to_eq.transform, g) if to_eq else g
                    inter = field_eq.intersection(g_eq)
                    if inter.is_empty: continue
                    intersecting += 1
                    a = float(inter.area)
                    if a > 0:
                        tmp[mk] = tmp.get(mk, 0.0) + a

                if debug:
                    print(f"[weights:gml] axis={'lat,lon' if pass_swap else 'lon,lat'} parsed={parsed} intersecting={intersecting}")

                if tmp:
                    area_by_mukey = tmp
                    break  # axis chosen

            if area_by_mukey:
                break  # layer chosen

        except Exception as e:
            if debug: print(f"[weights:gml] ERROR {e}")
            continue

    total = float(sum(area_by_mukey.values()))
    if total <= 0:
        return {}
    return {mk: a/total for mk,a in area_by_mukey.items()}
