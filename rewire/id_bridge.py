"""Silver id_bridge rows (GERS pattern) for identifiers DUCT invents, so DUCT outputs join on spine hub IDs.

- duct_fom:      the `id` the rewire adapters put on DUCT building features (= spine building_id, exact).
- duct_bem_cea:  bem-cea-gen zone `Name` = building `name` with ':' -> '-' (bem-cea/proc_gen/processor.py L90).
- legacy DUCT GeoJSON with its own ids (e.g. old DUCT Explorer exports): spatial overlap onto silver buildings.

Landing the rows in a lake (read-merge-write; Lake.silver_write replaces the whole id_bridge table):

    python rewire/id_bridge.py --lake <lake> --source-version <v> [--legacy f.geojson --id-col id --legacy-source s]
                               [--scenario <name>]
"""
from __future__ import annotations

import argparse
import sys

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


def land(lake, source_version: str, legacy: gpd.GeoDataFrame | None = None, id_col: str = "id",
         legacy_source: str = "duct_bdp", scenario: str | None = None) -> pd.DataFrame:
    """Build this repo's id_bridge rows from the lake's Silver buildings and append them to Silver id_bridge.

    Baseline: rows of the sources written here are replaced (idempotent re-runs, no stale ids); every other
    source's rows are kept. Scenario: rows go to the scenario overlay via Lake.scenario_set, never to baseline.
    """
    from spine.lake import BASELINE

    scenario = scenario or BASELINE
    buildings = lake.silver_read("buildings", scenario)
    new = exact_rows(buildings, source_version)
    if legacy is not None:
        new = pd.concat([new, spatial_rows(legacy, id_col, buildings, legacy_source, source_version)],
                        ignore_index=True)
    if scenario != BASELINE:
        lake.scenario_set(scenario, "id_bridge", new)
        return new
    try:
        existing = lake.silver_read("id_bridge")
    except FileNotFoundError:
        existing = pd.DataFrame(columns=COLUMNS)
    kept = existing[~existing["source"].isin(new["source"].unique())]
    merged = pd.concat([kept, new], ignore_index=True) if len(kept) else new
    lake.silver_write("id_bridge", merged)
    return merged


def main(argv=None) -> int:
    from spine.lake import Lake

    ap = argparse.ArgumentParser(description="append duct_fom / duct_bem_cea (+ legacy) rows to Silver id_bridge")
    ap.add_argument("--lake", default=None, help="lake root (default $SPINE_LAKE or ./lake)")
    ap.add_argument("--source-version", required=True)
    ap.add_argument("--legacy", help="legacy DUCT building GeoJSON with its own ids (spatial match)")
    ap.add_argument("--id-col", default="id")
    ap.add_argument("--legacy-source", default="duct_bdp")
    ap.add_argument("--scenario", default=None)
    a = ap.parse_args(argv)
    legacy = gpd.read_file(a.legacy) if a.legacy else None
    out = land(Lake(a.lake), a.source_version, legacy, a.id_col, a.legacy_source, a.scenario)
    print(out.groupby("source").size().to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
