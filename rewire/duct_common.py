"""Shared plumbing for the DUCT rewire adapters: Silver frames <-> the files DUCT's SaaS processors read/write.

No science here: only format conversion (GeoParquet EPSG:3414 <-> GeoJSON/GeoTIFF EPSG:4326, DUCT HDF5 -> Gold).
"""
from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import h5py
import numpy as np
import pandas as pd
from shapely.geometry import box


def bbox_wgs84(gdf: gpd.GeoDataFrame) -> dict:
    w, s, e, n = gdf.to_crs("EPSG:4326").total_bounds
    return {"west": float(w), "south": float(s), "east": float(e), "north": float(n)}


def write_geojson(path: Path, gdf: gpd.GeoDataFrame, props) -> int:
    """Write a GeoJSON FeatureCollection in EPSG:4326 with properties=props(row). Returns feature count."""
    g = gdf.to_crs("EPSG:4326")
    feats = [{"type": "Feature", "geometry": geom.__geo_interface__, "properties": props(row)}
             for geom, (_, row) in zip(g.geometry, g.iterrows())]
    Path(path).write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    return len(feats)


def building_props(row) -> dict:
    """DUCT GeoVectorData building feature: spine building_id is used verbatim as DUCT's id and name."""
    return {"id": row["building_id"], "name": row["building_id"], "height": float(row["height_m"])}


ROW0 = ("south", "north")


def grid_cells(bounding_box: list, ny: int, nx: int, row0: str) -> gpd.GeoDataFrame:
    """Cells of a regular lat/lon grid. DUCT HDF5 bounding_box attr is [south, north, west, east].

    row0 says which edge array row 0 lies on. DUCT's HDF5 writers keep the model's own order, which is
    south-up (row 0 = southernmost) for WRF, PALM and Scout; GeoTIFFs are north-up. cell_id/row/col keep the
    upstream array indices, so Gold rows map 1:1 to the HDF5.
    """
    if row0 not in ROW0:
        raise ValueError(f"row0 must be one of {ROW0}, got {row0!r}")
    s, n, w, e = (float(v) for v in bounding_box)
    dx, dy = (e - w) / nx, (n - s) / ny
    rows, cols = np.indices((ny, nx))
    rows, cols = rows.ravel(), cols.ravel()
    south_edge = s + rows * dy if row0 == "south" else n - (rows + 1) * dy
    geoms = [box(w + c * dx, y0, w + (c + 1) * dx, y0 + dy) for c, y0 in zip(cols, south_edge)]
    return gpd.GeoDataFrame({"cell_id": [f"r{r:04d}c{c:04d}" for r, c in zip(rows, cols)],
                             "row": rows.astype("int64"), "col": cols.astype("int64")},
                            geometry=geoms, crs="EPSG:4326").to_crs("EPSG:3414")


def read_climate_hdf5(path: Path, prefix: str, row0: str, sentinels: dict | None = None) -> dict:
    """DUCT.ClimateVariables / DUCT.NearSurfaceClimateVariables HDF5 -> {<prefix>_grid, <prefix>_values}.

    Each dataset is (t, y, x) or (t, z, y, x) with attrs unit, timestamps, bounding_box[, heights].
    row0: edge of the bounding box that y index 0 lies on ("south" for every DUCT writer, see grid_cells).
    height_m is the dataset's heights attr, or null for 2D fields (upstream gives none).
    Values equal to a dataset's sentinel (e.g. WRF's 999 outside the urban mask) and non-finite values are dropped.
    """
    sentinels = sentinels or {}
    grid, frames = None, []
    with h5py.File(path, "r") as f:
        for name in sorted(f.keys()):
            ds = f[name]
            data = np.asarray(ds[()], dtype="float64")
            if data.ndim == 3:
                data, heights = data[:, None], [np.nan]
            elif data.ndim == 4:
                heights = list(ds.attrs.get("heights", range(data.shape[1])))
            else:
                raise ValueError(f"{name}: unexpected shape {data.shape}")
            nt, nz, ny, nx = data.shape
            if grid is None:
                grid = grid_cells(list(ds.attrs["bounding_box"]), ny, nx, row0)
                grid_shape = (ny, nx)
            elif (ny, nx) != grid_shape:
                raise ValueError(f"{name}: grid {ny}x{nx} differs from {grid_shape}")
            ts = [int(t) for t in ds.attrs["timestamps"]]
            t_idx, z_idx, cell = np.meshgrid(np.arange(nt), np.arange(nz), np.arange(ny * nx), indexing="ij")
            vals = data.reshape(nt, nz, ny * nx).ravel()
            df = pd.DataFrame({
                "cell_id": grid["cell_id"].to_numpy()[cell.ravel()],
                "variable": name,
                "unit": str(ds.attrs.get("unit", "")),
                "timestamp": np.asarray(ts, dtype="int64")[t_idx.ravel()],
                "level": z_idx.ravel().astype("int64"),
                "height_m": np.asarray(heights, dtype="float64")[z_idx.ravel()],
                "value": vals,
            })
            keep = np.isfinite(vals)
            if name in sentinels:
                keep &= vals != sentinels[name]
            frames.append(df[keep])
    return {f"{prefix}_grid": grid, f"{prefix}_values": pd.concat(frames, ignore_index=True)}
