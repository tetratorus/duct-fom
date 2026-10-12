"""Spine adapter for DUCT ucm-palm (PALM-4U microscale). runtime: external (NSCC HPC); the spine never calls run().

prepare(): Silver buildings -> ucm-palm-prep `buildings` GeoJSON (id, height); `landcover`/`vegetation` GeoJSON and
           the PALM parameters are passed through from the caller (Silver has no PALM landcover_type yet).
collect(): ucm-palm-sim `climatic-variables` HDF5 (t, z, y, x) -> Gold palm_grid + palm_values.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from duct_common import bbox_wgs84, building_props, read_climate_hdf5, write_geojson

# HDF5 row 0 = south: ucm-palm-sim copies PALM's (y, x) netCDF arrays verbatim (check_and_extract -> add_hdf5_output);
# PALM y runs south->north (ucm-palm-prep flips the same static-driver arrays with flip_up=True for its GeoTIFFs),
# and DUCT Explorer flips the HDF5 for display (dots/duct_nsc_variables.py).
# 3D fields (wind_speed, wind_direction, relative_humidity, air_temperature) carry a heights attr -> palm_values.
# 2D fields (surface_temperature, pet) come from PALM's *_xy output with no height in the HDF5 or in duct-fom
# (pet's evaluation height is set inside PALM's biometeorology module, not in this repo) -> palm_xy_values.
ROW0 = "south"


def prepare(inputs: dict, params: dict, wd: Path) -> None:
    wd = Path(wd)
    b = inputs["buildings"]
    write_geojson(wd / "buildings", b[b["height_m"].notna()], building_props)
    for name in ("landcover", "vegetation"):
        src = params.get(f"{name}_geojson")
        if src:
            shutil.copy(src, wd / name)
    bbox = params.get("bounding_box") or bbox_wgs84(inputs["districts"])
    p = {"name": params.get("name", "spine"), "bbox": bbox, **params.get("palm_prep_parameters", {})}
    (wd / "parameters").write_text(json.dumps(p))


def collect(wd: Path) -> dict:
    return read_climate_hdf5(Path(wd) / "climatic-variables", "palm", ROW0, xy_table="xy_values")


def run(inputs: dict, params: dict) -> dict:
    raise RuntimeError("duct.palm_microscale is runtime: external; see transform.yaml runtime.notes")
