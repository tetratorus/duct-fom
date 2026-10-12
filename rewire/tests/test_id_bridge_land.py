"""id_bridge landing: read-merge-write into Silver keeps every other source's rows (Lake.silver_write replaces)."""
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import box
from spine.lake import Lake

REWIRE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REWIRE))
from id_bridge import COLUMNS, land


def _lake(tmp_path):
    lake = Lake(tmp_path / "lake")
    b = gpd.GeoDataFrame({"building_id": ["B1", "B2"], "source_version": "fixture"},
                         geometry=[box(29000, 29000, 29010, 29010), box(29100, 29000, 29110, 29010)], crs="EPSG:3414")
    lake.silver_write("buildings", b)
    other = pd.DataFrame([{"source": "ura_mp2019", "source_id": "P9", "hub": "plot", "hub_id": "P9",
                           "match_method": "exact", "confidence": None, "source_version": "mp19"},
                          {"source": "duct_fom", "source_id": "GONE", "hub": "building", "hub_id": "GONE",
                           "match_method": "exact", "confidence": None, "source_version": "old"}], columns=COLUMNS)
    lake.silver_write("id_bridge", other)
    return lake


def _ids(df, source):
    return sorted(df.loc[df.source == source, "source_id"])


def test_land_preserves_other_sources_and_replaces_own(tmp_path):
    lake = _lake(tmp_path)
    land(lake, "v1")
    got = lake.silver_read("id_bridge")
    assert _ids(got, "ura_mp2019") == ["P9"]
    assert _ids(got, "duct_fom") == ["B1", "B2"]  # stale GONE replaced, not appended to
    assert _ids(got, "duct_bem_cea") == ["B1", "B2"]
    land(lake, "v1")  # idempotent
    assert len(lake.silver_read("id_bridge")) == len(got)


def test_land_legacy_spatial(tmp_path):
    lake = _lake(tmp_path)
    legacy = gpd.GeoDataFrame({"id": ["old-7"]}, geometry=[box(29001, 29001, 29010, 29010)], crs="EPSG:3414")
    land(lake, "v1", legacy=legacy)
    got = lake.silver_read("id_bridge").set_index(["source", "source_id"])
    assert got.loc[("duct_bdp", "old-7"), "hub_id"] == "B1"
    assert got.loc[("duct_bdp", "old-7"), "match_method"] == "spatial_overlap"


def test_land_cli(tmp_path):
    lake = _lake(tmp_path)
    r = subprocess.run([sys.executable, str(REWIRE / "id_bridge.py"), "--lake", str(lake.root), "--source-version", "v1"],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    got = lake.silver_read("id_bridge")
    assert _ids(got, "duct_bem_cea") == ["B1", "B2"] and _ids(got, "ura_mp2019") == ["P9"]
