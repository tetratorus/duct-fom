"""Spine adapter for DUCT ucm-scout (OpenFOAM microscale). runtime: external (NSCC ASPIRE2); never called by spine.

prepare(): Silver buildings -> ucm-scout `building-footprints` GeoJSON (id, name, height) + `parameters`
           (name, area from the districts bbox, everything else passed through).
collect(): ucm-scout `climatic-variables` HDF5 (air_temperature, t, y, x) -> Gold scout_grid + scout_values.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from duct_common import bbox_wgs84, building_props, read_climate_hdf5, write_geojson

# HDF5 row 0 = south: ucm-scout/proc/processor.py convert_vtp_to_geotiff grids on np.linspace(lat_min, lat_max),
# flips for the GeoTIFF and flips back (L582) before the array is stacked into the HDF5.
# air_temperature is 2D: sampled on the OpenFOAM cutting plane contour_xy_1 (processor.py L739), which
# case_template/system/predefinedSampleDict defines at point (0 0 1), normal (0 0 1), i.e. z = 1 m.
NOMINAL_HEIGHT_M = {"air_temperature": 1.0}
ROW0 = "south"


def prepare(inputs: dict, params: dict, wd: Path) -> None:
    wd = Path(wd)
    b = inputs["buildings"]
    write_geojson(wd / "building-footprints", b[b["height_m"].fillna(0) > 0], building_props)
    area = params.get("bounding_box") or bbox_wgs84(inputs["districts"])
    p = {**params.get("scout_parameters", {}), "name": params.get("name", "spine"), "area": area}
    (wd / "parameters").write_text(json.dumps(p))


def collect(wd: Path) -> dict:
    return read_climate_hdf5(Path(wd) / "climatic-variables", "scout", ROW0, nominal_heights=NOMINAL_HEIGHT_M)


def run(inputs: dict, params: dict) -> dict:
    raise RuntimeError("duct.scout_microscale is runtime: external; see transform.yaml runtime.notes")
