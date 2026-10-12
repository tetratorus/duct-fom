"""Spine adapter for DUCT ucm-wrf (mWRF-SG mesoscale). runtime: external (PBS/HPC); the spine never calls run().

prepare(): Silver land_cover.lcz -> the `lcz-map` GeoTIFF input of ucm-wrf-prep (the other ucm-wrf-prep inputs,
           bem-parameters, vegfra-map and AH profiles, are passed through unchanged from the caller).
collect(): ucm-wrf-sim `d04-near-surface-climate` HDF5 -> Gold wrf_d04_grid + wrf_d04_values.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_bounds

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from duct_common import bbox_wgs84, read_climate_hdf5

# HDF5 row 0 = south: ucm-wrf/proc_sim/processor.py stores c_data in WRF south_north order and only flips it for the
# GeoTIFF (export_as_tiff(..., flip_vertically=True)); DUCT Explorer flips the HDF5 back (dots/duct_nsc_variables.py).
ROW0 = "south"
UHI_OUTSIDE_URBAN = 999  # ucm-wrf/proc_sim/processor.py writes 999 outside the urban mask for 2m_air_temperature_uhi


def write_lcz_map(path: Path, land_cover: gpd.GeoDataFrame, bbox: dict, width: int, height: int) -> None:
    lc = land_cover[land_cover["lcz"].notna()].to_crs("EPSG:4326")
    tf = from_bounds(bbox["west"], bbox["south"], bbox["east"], bbox["north"], width, height)
    data = rasterize(((g, int(v)) for g, v in zip(lc.geometry, lc["lcz"])), out_shape=(height, width),
                     transform=tf, fill=0, dtype="uint8")
    with rasterio.open(path, "w", driver="GTiff", width=width, height=height, count=1, dtype="uint8",
                       crs="EPSG:4326", transform=tf, nodata=0) as dst:
        dst.write(data, 1)


def prepare(inputs: dict, params: dict, wd: Path) -> None:
    """Write ucm-wrf-prep's `parameters` and `lcz-map` into a SaaS working directory."""
    wd = Path(wd)
    lc = inputs["land_cover"]
    bbox = params.get("bounding_box") or bbox_wgs84(lc)
    write_lcz_map(wd / "lcz-map", lc, bbox, params.get("lcz_width", 1000), params.get("lcz_height", 1000))
    (wd / "parameters").write_text(json.dumps(params.get("wrf_prep_parameters", {})))


def collect(wd: Path) -> dict:
    return read_climate_hdf5(Path(wd) / "d04-near-surface-climate", "wrf_d04", ROW0,
                             sentinels={"2m_air_temperature_uhi": UHI_OUTSIDE_URBAN})


def run(inputs: dict, params: dict) -> dict:
    raise RuntimeError("duct.wrf_mesoscale is runtime: external; see transform.yaml runtime.notes")
