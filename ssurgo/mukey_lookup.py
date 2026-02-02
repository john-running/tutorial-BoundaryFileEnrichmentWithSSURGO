# ssurgo/mukey_lookup.py
import requests
from xml.etree import ElementTree as ET

WFS_URL = "https://sdmdataaccess.nrcs.usda.gov/Spatial/SDMWGS84Geographic.wfs"

def mukeys_for_bbox(bbox):
    """
    Return a sorted list of MUKEY ints that intersect the given
    (minx, miny, maxx, maxy) bbox (EPSG:4326).
    """
    minx, miny, maxx, maxy = bbox
    pad = 1e-4
    minx -= pad; miny -= pad; maxx += pad; maxy += pad

    tries = [
        ("MapunitPoly", "xy", False),
        ("mapunitpoly", "xy", False),
        ("sdm:MapunitPoly", "xy", False),
        ("MapunitPoly", "yx", False),
        ("MapunitPoly", "xy", True),
        ("MapunitPoly", "yx", True),
    ]
    last_err = None

    for layer, order, epsg in tries:
        bbox_str = (
            f"{minx},{miny},{maxx},{maxy}" if order == "xy"
            else f"{miny},{minx},{maxy},{maxx}"
        )
        if epsg:
            bbox_str += ",EPSG:4326"
        params = {
            "service": "WFS",
            "version": "1.1.0",
            "request": "GetFeature",
            "typename": layer,
            "bbox": bbox_str,
            "srsName": "EPSG:4326",
            "resultType": "results",
            "maxFeatures": 200000,
        }
        try:
            r = requests.get(WFS_URL, params=params, timeout=60)
            r.raise_for_status()
            root = ET.fromstring(r.text)
            mukeys = set()
            for e in root.iter():
                if e.tag.split("}")[-1].lower() == "mukey":
                    t = (e.text or "").strip()
                    if t.isdigit():
                        mukeys.add(int(t))
            if mukeys:
                return sorted(mukeys)
            last_err = "OK but no <MUKEY>"
        except Exception as e:
            last_err = str(e)

    raise RuntimeError(f"WFS MUKEY fetch failed: {last_err}")
