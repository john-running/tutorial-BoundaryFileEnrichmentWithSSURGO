# ssurgo/field_io.py

import os
import shapefile
from shapely.geometry import shape, Polygon, MultiPolygon
from shapely.ops import unary_union, transform as shapely_transform
try:
    from pyproj import Transformer
except Exception:
    Transformer = None

# acres per square meter
ACRE_PER_M2 = 0.00024710538146717

def read_field_union(path: str):
    sf = shapefile.Reader(path)
    recs = sf.shapeRecords()
    if not recs:
        raise RuntimeError("No records in shapefile.")
    geoms = []
    for r in recs:
        g = shape(r.shape.__geo_interface__)
        if isinstance(g, (Polygon, MultiPolygon)):
            geoms.append(g)
    if not geoms:
        raise RuntimeError("No polygon geometry found.")
    fields = [f[0] for f in sf.fields[1:]]
    id_fields = dict(zip(fields, recs[0].record))
    return unary_union(geoms), id_fields

def field_area_eqm2(field_or_geom):
    # Accept either {"geom": ...} or a bare shapely geometry
    geom = field_or_geom["geom"] if isinstance(field_or_geom, dict) else field_or_geom
    if Transformer is not None:
        try:
            to_eq = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
            return float(shapely_transform(to_eq.transform, geom).area)
        except Exception:
            pass
    return float(geom.area)

def read_field_metadata(shp_path: str, geom, id_fields: dict) -> dict:
    """
    Build a compact metadata block from the boundary shapefile + geometry.
    Returns:
      {
        file_name, feature_count, crs_wkt,
        id_fields, centroid_lonlat, bbox_lonlat,
        area_m2, area_acres
      }
    """
    # centroid (lon, lat) in EPSG:4326 (geom is already lon/lat)
    c = geom.centroid
    centroid_lonlat = [float(c.x), float(c.y)]

    # bbox in EPSG:4326
    minx, miny, maxx, maxy = geom.bounds
    bbox_lonlat = [float(minx), float(miny), float(maxx), float(maxy)]

    # area in equal-area meters^2 (EPSG:5070) and acres
    area_m2 = field_area_eqm2({"geom": geom, "id_fields": id_fields})
    area_acres = area_m2 * ACRE_PER_M2 if area_m2 is not None else None

    # feature count (how many records in the shapefile)
    try:
        sf = shapefile.Reader(shp_path)
        feature_count = len(sf.shapeRecords())
    except Exception:
        feature_count = None

    # CRS WKT from .prj (if present)
    prj_path = os.path.splitext(shp_path)[0] + ".prj"
    crs_wkt = None
    if os.path.exists(prj_path):
        try:
            with open(prj_path, "r", encoding="utf-8") as f:
                crs_wkt = f.read().strip()
        except Exception:
            crs_wkt = None

    return {
        "file_name": os.path.basename(shp_path),
        "feature_count": feature_count,
        "crs_wkt": crs_wkt,
        "id_fields": id_fields or {},
        "centroid_lonlat": centroid_lonlat,
        "bbox_lonlat": bbox_lonlat,
        "area_m2": area_m2,
        "area_acres": area_acres,
    }
