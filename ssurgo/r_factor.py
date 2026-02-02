from typing import Optional, Sequence, Tuple, List
import math
from contextlib import contextmanager
import numpy as np
import rasterio
from rasterio.warp import transform as transform_coords
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT


def _to_float_or_none(val, nodata) -> Optional[float]:
    if val is None:
        return None
    # Handle masked arrays
    try:
        if np.ma.isMaskedArray(val):
            if getattr(val, "mask", False) is True:
                return None
            val = val.data
    except Exception:
        pass
    # Unpack 0-d numpy arrays
    try:
        if hasattr(val, "shape") and val.shape == ():
            val = float(val)
    except Exception:
        pass
    try:
        f = float(val)
    except Exception:
        return None
    if nodata is not None:
        try:
            if float(nodata) == f:
                return None
        except Exception:
            pass
    if np.isnan(f) or not np.isfinite(f):
        return None
    return f


def r_value_at(lon: float, lat: float, tif_path: str, bilinear: bool = True) -> Optional[float]:
    """
    Return RUSLE R-factor at (lon, lat) from a GeoTIFF.
    Inputs lon/lat are EPSG:4326. Returns None for NoData/NaN.
    """
    with rasterio.open(tif_path) as src:
        x_list, y_list = transform_coords("EPSG:4326", src.crs, [lon], [lat])
        x, y = x_list[0], y_list[0]
        if bilinear:
            with WarpedVRT(src, resampling=Resampling.bilinear) as vrt:
                val = list(vrt.sample([(x, y)]))[0][0]
                return _to_float_or_none(val, vrt.nodata)
        else:
            val = list(src.sample([(x, y)]))[0][0]
            return _to_float_or_none(val, src.nodata)


def r_values_bulk(points_lonlat: Sequence[Tuple[float, float]], tif_path: str, bilinear: bool = True) -> List[Optional[float]]:
    """
    Vectorized sampling for many lon/lat points. Returns a list aligned to inputs.
    Returns None entries for NoData/NaN.
    """
    if not points_lonlat:
        return []
    with rasterio.open(tif_path) as src:
        xs, ys = transform_coords(
            "EPSG:4326",
            src.crs,
            [p[0] for p in points_lonlat],
            [p[1] for p in points_lonlat],
        )
        if bilinear:
            with WarpedVRT(src, resampling=Resampling.bilinear) as vrt:
                vals = list(vrt.sample(list(zip(xs, ys))))
                nodata = vrt.nodata
        else:
            vals = list(src.sample(list(zip(xs, ys))))
            nodata = src.nodata
    out: List[Optional[float]] = []
    for v in vals:
        out.append(_to_float_or_none(v[0], nodata))
    return out


