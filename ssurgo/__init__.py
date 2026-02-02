# ssurgo/__init__.py
from .field_io import read_field_union, field_area_eqm2
from .mukey_lookup import mukeys_for_bbox
from .mukey_weights import area_shares_wfs_gml
from .sda_client import (
    components_muaggatt, horizons_by_cokeys, chfrags_sum_by_chkeys,
    textures_by_chkeys, corestrictions_by_cokeys, compaction_interpretation_by_cokeys,
    fetch_interpretations
)
from .featurize import horizon_aggregates
from .aggregate import compute_summary
