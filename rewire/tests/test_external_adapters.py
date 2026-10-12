"""External transforms: adapters are tested on files laid out exactly as the upstream processors write them."""
import importlib.util
import json
from pathlib import Path

import geopandas as gpd
import h5py
import numpy as np
import pandas as pd
import pytest
import rasterio
from shapely.geometry import box
from spine.transform import NotRunnable, invoke, load_descriptor
from spine.validate import check_gold

REWIRE = Path(__file__).resolve().parents[1]
CASE = REWIRE / "duct.mva_wind_corridor" / "golden" / "tanjong_pagar" / "inputs"
BBOX = [1.274, 1.284, 103.840, 103.850]  # upstream attr order: south, north, west, east


def _adapter(tid):
    spec = importlib.util.spec_from_file_location(f"{tid}_adapter", REWIRE / tid / "adapter.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, load_descriptor(REWIRE / tid / "transform.yaml")


def _silver():
    return {"buildings": gpd.read_parquet(CASE / "buildings.parquet"),
            "districts": gpd.read_parquet(CASE / "districts.parquet")}


def _write_h5(path, datasets):
    with h5py.File(path, "w") as f:
        for name, (data, unit, extra) in datasets.items():
            ds = f.create_dataset(name, data=data, track_times=False)
            ds.attrs["unit"] = unit
            ds.attrs["shape"] = data.shape
            ds.attrs["timestamps"] = list(range(0, 3600 * data.shape[0], 3600))
            ds.attrs["bounding_box"] = BBOX
            for k, v in extra.items():
                ds.attrs[k] = v


def _check(desc, out):
    for spec in desc.data["outputs"]:
        check_gold(spec["name"], out[spec["name"]], spec)


@pytest.mark.parametrize("tid", ["duct.wrf_mesoscale", "duct.palm_microscale", "duct.scout_microscale"])
def test_external_not_runnable_by_spine(tid):
    _, desc = _adapter(tid)
    with pytest.raises(NotRunnable):
        invoke(desc, {}, {})


def test_wrf_prepare_and_collect(tmp_path):
    mod, desc = _adapter("duct.wrf_mesoscale")
    cells = gpd.GeoDataFrame({"cell_id": ["a", "b"], "lcz": [1, 6]},
                             geometry=[box(28800, 28600, 29300, 29500), box(29300, 28600, 29800, 29500)],
                             crs="EPSG:3414")
    mod.prepare({"land_cover": cells}, {"lcz_width": 50, "lcz_height": 50}, tmp_path)
    with rasterio.open(tmp_path / "lcz-map") as src:
        assert set(np.unique(src.read(1))) <= {0, 1, 6} and {1, 6} <= set(np.unique(src.read(1)))
    t2 = np.full((2, 4, 5), 30.0)
    uhi = np.full((2, 4, 5), 1.5)
    uhi[:, 0, 0] = 999
    _write_h5(tmp_path / "d04-near-surface-climate",
              {"2m_air_temperature": (t2, "˚C", {"timezone": "UTC"}),
               "2m_air_temperature_uhi": (uhi, "Δ˚C", {"timezone": "UTC"})})
    out = mod.collect(tmp_path)
    _check(desc, out)
    v = out["wrf_d04_values"]
    assert len(out["wrf_d04_grid"]) == 20
    assert len(v[v.variable == "2m_air_temperature"]) == 40 and len(v[v.variable == "2m_air_temperature_uhi"]) == 38
    assert out["wrf_d04_grid"].crs.to_epsg() == 3414


def test_palm_prepare_and_collect(tmp_path):
    mod, desc = _adapter("duct.palm_microscale")
    mod.prepare(_silver(), {"palm_prep_parameters": {"resolution": [64, 64]}}, tmp_path)
    feats = json.loads((tmp_path / "buildings").read_text())["features"]
    assert len(feats) == 1258 and all(int(f["properties"]["height"]) >= 1 for f in feats)
    assert json.loads((tmp_path / "parameters").read_text())["bbox"]["west"] > 103.8
    _write_h5(tmp_path / "climatic-variables",
              {"air_temperature": (np.random.default_rng(0).normal(30, 1, (2, 3, 4, 5)), "C", {"heights": [0, 2, 4]})})
    out = mod.collect(tmp_path)
    _check(desc, out)
    assert len(out["palm_values"]) == 2 * 3 * 20
    assert sorted(out["palm_values"].height_m.unique()) == [0, 2, 4]


def test_scout_prepare_and_collect(tmp_path):
    mod, desc = _adapter("duct.scout_microscale")
    mod.prepare(_silver(), {"scout_parameters": {"foo": 1}}, tmp_path)
    p = json.loads((tmp_path / "parameters").read_text())
    assert p["foo"] == 1 and set(p["area"]) == {"west", "south", "east", "north"}
    feats = json.loads((tmp_path / "building-footprints").read_text())["features"]
    assert all(float(f["properties"]["height"]) > 0 and f["properties"]["id"] for f in feats)
    _write_h5(tmp_path / "climatic-variables", {"air_temperature": (np.full((3, 4, 5), 31.0), "˚C", {})})
    out = mod.collect(tmp_path)
    _check(desc, out)
    assert (out["scout_values"].value == 31.0).all()


def test_id_bridge():
    import sys
    sys.path.insert(0, str(REWIRE))
    from id_bridge import COLUMNS, exact_rows, spatial_rows
    b = _silver()["buildings"]
    rows = exact_rows(b, "fixture")
    assert list(rows.columns) == COLUMNS and len(rows) == 2 * len(b)
    cea = rows[rows.source == "duct_bem_cea"]
    assert not cea.source_id.str.contains(":").any() and (cea.hub_id.isin(b.building_id)).all()
    hits = gpd.sjoin(b[["geometry"]], b[["geometry"]], predicate="intersects").groupby(level=0).size()
    isolated = b.loc[hits[hits == 1].index[:20]]  # Overture has overlapping parts; use footprints with no neighbours
    legacy = gpd.GeoDataFrame({"duct_id": [f"bdp-{i}" for i in range(len(isolated))]},
                              geometry=isolated.geometry.buffer(0.5).values, crs="EPSG:3414")
    sp = spatial_rows(legacy, "duct_id", b, "duct_bdp", "legacy").sort_values("source_id", key=lambda s: s.str[4:].astype(int))
    assert len(sp) == 20 and (sp.hub_id.values == isolated.building_id.values).all()
    assert (sp.confidence > 0.5).all() and isinstance(sp, pd.DataFrame)
