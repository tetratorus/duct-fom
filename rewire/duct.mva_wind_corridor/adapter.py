"""Thin spine adapter for DUCT ucm-mva-uwc (urban wind corridors). Calls the UNMODIFIED upstream processor.

Silver buildings/districts (EPSG:3414) -> the SaaS working-directory files upstream expects
(`parameters`, `building-footprints`, `land-mask` GeoJSON in EPSG:4326) -> upstream `proc_uwc.processor.function`
-> its four GeoTIFFs -> one Gold row per MVA grid cell, cell polygons reprojected to EPSG:3414.
"""
from __future__ import annotations

import importlib
import json
import sys
import tempfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from shapely.geometry import box

UPSTREAM = Path(__file__).resolve().parents[2] / "ucm-mva"
# output name in upstream -> gold column; order = upstream DirectionWind 0..3
DIRECTIONS = {
    "wind-corridors-ns": "corridor_ns",
    "wind-corridors-ew": "corridor_ew",
    "wind-corridors-nwse": "corridor_nwse",
    "wind-corridors-nesw": "corridor_nesw",
}


def _upstream_processor():
    for p in (UPSTREAM / "dependencies" / "python", UPSTREAM):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    return importlib.import_module("proc_uwc.processor")


def _features(gdf: gpd.GeoDataFrame, props) -> dict:
    gdf = gdf.to_crs("EPSG:4326")
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": geom.__geo_interface__, "properties": props(row)}
        for geom, (_, row) in zip(gdf.geometry, gdf.iterrows())
    ]}


def bounding_box(districts: gpd.GeoDataFrame, margin_m: float = 100.0) -> dict:
    """Districts bounds plus a margin: upstream finds corridors from the land-mask edge, so the raster must extend past it."""
    w, s, e, n = gpd.GeoSeries(districts.to_crs("EPSG:3414").geometry).total_bounds
    w, s, e, n = gpd.GeoSeries([box(w - margin_m, s - margin_m, e + margin_m, n + margin_m)],
                               crs="EPSG:3414").to_crs("EPSG:4326").total_bounds
    return {"west": float(w), "south": float(s), "east": float(e), "north": float(n)}


def write_upstream_inputs(wd: Path, buildings: gpd.GeoDataFrame, districts: gpd.GeoDataFrame, params: dict) -> dict:
    buildings = buildings[buildings["height_m"].notna()]
    bbox = params.get("bounding_box") or bounding_box(districts, params.get("margin_m", 100.0))
    (wd / "parameters").write_text(json.dumps({"bounding_box": bbox, "resolution": params["resolution"]}))
    (wd / "building-footprints").write_text(json.dumps(_features(
        buildings, lambda r: {"id": r["building_id"], "height": float(r["height_m"])})))
    (wd / "land-mask").write_text(json.dumps(_features(districts, lambda r: {"id": r["district_id"]})))
    return bbox


def read_upstream_outputs(wd: Path, districts: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    cols, transform, shape = {}, None, None
    for name, col in DIRECTIONS.items():
        with rasterio.open(wd / name) as src:
            cols[col] = src.read(1).astype("float64")
            transform, shape = src.transform, src.shape
    rows, colidx = np.indices(shape)
    rows, colidx = rows.ravel(), colidx.ravel()
    x0, y0 = transform * (colidx, rows)
    x1, y1 = transform * (colidx + 1, rows + 1)
    cells = gpd.GeoDataFrame({
        "cell_id": [f"mva:r{r:04d}c{c:04d}" for r, c in zip(rows, colidx)],
        "row": rows.astype("int64"), "col": colidx.astype("int64"),
        **{k: v.ravel() for k, v in cols.items()},
    }, geometry=[box(min(a, b), min(c, d), max(a, b), max(c, d)) for a, b, c, d in zip(x0, x1, y0, y1)],
        crs="EPSG:4326").to_crs("EPSG:3414")
    centroids = gpd.GeoDataFrame(geometry=cells.geometry.centroid, crs="EPSG:3414")
    hit = gpd.sjoin(centroids, districts[["district_id", "geometry"]].to_crs("EPSG:3414"),
                    how="left", predicate="within")
    cells["district_id"] = hit.groupby(level=0)["district_id"].first().reindex(cells.index).values
    return cells[["cell_id", "row", "col", *DIRECTIONS.values(), "district_id", "geometry"]]


def run(inputs: dict, params: dict) -> dict:
    buildings, districts = inputs["buildings"], inputs["districts"]
    processor = _upstream_processor()
    with tempfile.TemporaryDirectory(prefix="duct-mva-") as tmp:
        wd = Path(tmp)
        write_upstream_inputs(wd, buildings, districts, params)
        if processor.function(str(wd)) != 0:
            raise RuntimeError("upstream ucm-mva-uwc processor returned -1 (see its log output above)")
        cells = read_upstream_outputs(wd, districts)
    if cells[list(DIRECTIONS.values())].isna().all().all():
        raise RuntimeError("upstream returned no corridors (all NaN): the bounding box must extend beyond the land mask")
    # upstream normalises by the max; a direction with no least-cost paths is 0/0 = NaN everywhere -> strength 0
    for col in DIRECTIONS.values():
        if cells[col].isna().all():
            cells[col] = 0.0
    return {"mva_wind_corridors": cells}
