"""Build the Tanjong Pagar golden fixture as Silver-shaped tables (EPSG:3414).

Source: Overture Maps buildings, release 2026-09-23.1 (ODbL), downloaded with DuckDB:

    COPY (SELECT id, height, num_floors, class, ST_AsText(geometry) wkt
          FROM read_parquet('s3://overturemaps-us-west-2/release/2026-09-23.1/theme=buildings/type=building/*',
                            hive_partitioning=1)
          WHERE bbox.xmin>103.840 AND bbox.xmax<103.850 AND bbox.ymin>1.274 AND bbox.ymax<1.284)
    TO 'overture_tp.parquet' (FORMAT parquet)

Usage: python make_fixture.py overture_tp.parquet
Buildings with neither `height` nor `num_floors` are dropped (DUCT's MVA rasteriser needs a height);
height_m = height, else num_floors * 3.0 m. The single district is the fixture bbox inset by 50 m and stands in
for the land mask (DUCT used city admin zones); it is a fixture boundary, not an official URA planning area.
"""
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely import wkt
from shapely.geometry import box

SRC = "overture_2026-09-23.1"
HERE = Path(__file__).parent / "tanjong_pagar" / "inputs"


def main(path: str) -> None:
    raw = pd.read_parquet(path)
    raw["height_m"] = raw["height"].fillna(raw["num_floors"] * 3.0)
    raw = raw[raw["height_m"].notna() & (raw["height_m"] >= 1)].copy()
    g = gpd.GeoDataFrame(raw, geometry=raw["wkt"].map(wkt.loads), crs="EPSG:4326").to_crs("EPSG:3414")
    g = g[g.geom_type.isin(["Polygon", "MultiPolygon"])]
    buildings = gpd.GeoDataFrame({
        "building_id": "ovt:" + g["id"].astype(str),
        "geometry": g.geometry.values,
        "height_m": g["height_m"].astype(float).round(1).values,
        "storeys": g["num_floors"].astype("Int64").values,
        "source_version": SRC,
    }, crs="EPSG:3414").sort_values("building_id").reset_index(drop=True)
    extent = gpd.GeoSeries([box(103.840, 1.274, 103.850, 1.284)], crs="EPSG:4326").to_crs("EPSG:3414").iloc[0]
    districts = gpd.GeoDataFrame({
        "district_id": ["fixture:tanjong_pagar"], "name": ["Tanjong Pagar fixture extent (bbox inset 50 m)"],
        "geometry": [extent.buffer(-50, join_style=2)], "source_version": ["fixture"],
    }, crs="EPSG:3414")
    HERE.mkdir(parents=True, exist_ok=True)
    buildings.to_parquet(HERE / "buildings.parquet", index=False)
    districts.to_parquet(HERE / "districts.parquet", index=False)
    print(len(buildings), "buildings;", districts.geometry.iloc[0].bounds)


if __name__ == "__main__":
    main(sys.argv[1])
