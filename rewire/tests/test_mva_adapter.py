"""The MVA adapter must be a pure re-plumbing of upstream: same numbers as running ucm-mva-uwc standalone."""
import json
import os
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from spine.golden import run_golden
from spine.lake import Lake
from spine.serve import collections, features
from spine.transform import load_descriptor

HERE = Path(__file__).resolve().parents[1] / "duct.mva_wind_corridor"
CASE = HERE / "golden" / "tanjong_pagar"
sys.path.insert(0, str(HERE))
import adapter


def _inputs():
    return {"buildings": gpd.read_parquet(CASE / "inputs" / "buildings.parquet"),
            "districts": gpd.read_parquet(CASE / "inputs" / "districts.parquet")}


def test_adapter_equals_upstream_cli(tmp_path):
    params = json.loads((CASE / "params.json").read_text())
    inputs = _inputs()
    adapter.write_upstream_inputs(tmp_path, inputs["buildings"], inputs["districts"], params)
    proc_dir = adapter.UPSTREAM / "proc_uwc"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(adapter.UPSTREAM), os.environ.get("PYTHONPATH", "")])}
    subprocess.run([sys.executable, "processor.py", str(tmp_path)], cwd=proc_dir, env=env, check=True,
                   stdout=subprocess.DEVNULL)
    got = adapter.run(inputs, params)["mva_wind_corridors"].sort_values(["row", "col"])
    for name, col in adapter.DIRECTIONS.items():
        with rasterio.open(tmp_path / name) as src:
            ref = src.read(1).astype("float64")
        np.testing.assert_allclose(got[col].to_numpy(), ref[got["row"], got["col"]], rtol=0, atol=0)


def test_output_contract():
    out = adapter.run(_inputs(), json.loads((CASE / "params.json").read_text()))["mva_wind_corridors"]
    assert out.crs.to_epsg() == 3414
    assert out["cell_id"].is_unique
    for col in adapter.DIRECTIONS.values():
        assert out[col].between(0, 1).all()
    assert out["district_id"].notna().all() and set(out["district_id"]) == {"fixture:tanjong_pagar"}
    land = gpd.read_parquet(CASE / "inputs" / "districts.parquet").geometry.iloc[0]
    assert out.geometry.centroid.within(land).all()


def test_spine_run_publishes_and_serves(tmp_path):
    """Reviewer repro: fresh lake, golden inputs in Silver, `spine run` must pass the WAP audit and be servable."""
    lake = Lake(tmp_path / "lake")
    for t in ("buildings", "districts"):
        lake.silver_write(t, gpd.read_parquet(CASE / "inputs" / f"{t}.parquet"))
    env = {**os.environ, "SPINE_LAKE": str(lake.root)}
    subprocess.run([sys.executable, "-m", "spine.cli", "--lake", str(lake.root), "--path", str(HERE.parent),
                    "run", "duct.mva_wind_corridor", "--params", (CASE / "params.json").read_text()],
                   check=True, env=env, stdout=subprocess.DEVNULL)
    cid = "gold.duct.mva_wind_corridor.mva_wind_corridors"
    assert cid in [c["id"] for c in collections(lake)]
    fc = features(lake, cid)
    expected = gpd.read_parquet(CASE / "expected" / "mva_wind_corridors.parquet")
    assert len(fc["features"]) == len(expected)
    assert all(f["properties"]["district_id"] == "fixture:tanjong_pagar" for f in fc["features"])


def test_golden():
    res = run_golden(load_descriptor(HERE / "transform.yaml"))
    assert all(r.passed for r in res), [m for r in res for m in r.messages]


def test_default_bbox_extends_past_land_mask():
    out = adapter.run(_inputs(), {"resolution": 30})["mva_wind_corridors"]
    assert out[list(adapter.DIRECTIONS.values())].notna().all().all()
