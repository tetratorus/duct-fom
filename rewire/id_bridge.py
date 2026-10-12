"""Silver id_bridge rows (GERS pattern) for identifiers DUCT invents, so DUCT outputs join on spine hub IDs.

- duct_fom:      the `id` the rewire adapters put on DUCT building features (= spine building_id, exact).
- duct_bem_cea:  bem-cea-gen zone `Name` = building `name` with ':' -> '-' (bem-cea/proc_gen/processor.py L90).
- legacy DUCT GeoJSON with its own ids (e.g. old DUCT Explorer exports): spatial overlap onto silver buildings.
"""
from __future__ import annotations

import geopandas as gpd
import pandas as pd

COLUMNS = ["source", "source_id", "hub", "hub_id", "match_method", "confidence", "source_version"]


def exact_rows(buildings: pd.DataFrame, source_version: str) -> pd.DataFrame:
    ids = buildings["building_id"].astype(str)
    duct = pd.DataFrame({"source": "duct_fom", "source_id": ids})
    cea = pd.DataFrame({"source": "duct_bem_cea", "source_id": ids.str.replace(":", "-", regex=False)})
    out = pd.concat([duct.assign(hub_id=ids.values), cea.assign(hub_id=ids.values)], ignore_index=True)
    out = out.assign(hub="building", match_method="exact", confidence=1.0, source_version=source_version)
    return out[COLUMNS]


def spatial_rows(native: gpd.GeoDataFrame, id_col: str, buildings: gpd.GeoDataFrame, source: str,
                 source_version: str, min_iou: float = 0.5) -> pd.DataFrame:
    """Best-IoU match of each native footprint onto a silver building (EPSG:3414); below min_iou -> unmatched."""
    a = native[[id_col, "geometry"]].to_crs("EPSG:3414").rename(columns={id_col: "source_id"})
    b = buildings[["building_id", "geometry"]].to_crs("EPSG:3414")
    pairs = gpd.sjoin(a, b, how="inner", predicate="intersects")
    bg = b.set_index("building_id").geometry
    ag = a.geometry
    inter = [ag.loc[i].intersection(bg.loc[h]).area for i, h in zip(pairs.index, pairs["building_id"])]
    union = [ag.loc[i].union(bg.loc[h]).area for i, h in zip(pairs.index, pairs["building_id"])]
    pairs = pairs.assign(iou=[i / u if u else 0.0 for i, u in zip(inter, union)])
    best = pairs.sort_values("iou", ascending=False).drop_duplicates("source_id")
    best = best[best["iou"] >= min_iou]
    return pd.DataFrame({"source": source, "source_id": best["source_id"].astype(str).values, "hub": "building",
                         "hub_id": best["building_id"].values, "match_method": "spatial_overlap",
                         "confidence": best["iou"].round(4).values, "source_version": source_version})[COLUMNS]
