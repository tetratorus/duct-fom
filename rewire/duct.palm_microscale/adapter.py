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
    return read_climate_hdf5(Path(wd) / "climatic-variables", "palm")


def run(inputs: dict, params: dict) -> dict:
    raise RuntimeError("duct.palm_microscale is runtime: external; see transform.yaml runtime.notes")
