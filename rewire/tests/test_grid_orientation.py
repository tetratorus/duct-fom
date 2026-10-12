"""Gold grid orientation vs DUCT's HDF5 writers (row 0 = south) on a north/south-asymmetric field.

Each test runs the upstream writer's own GeoTIFF function (extracted verbatim from the upstream processor source,
which cannot be imported here: wrf-python / netCDF4 / gmsh / pyvista are not installed) on the same array that goes
into the HDF5, then checks that every Gold cell polygon, sampled in that GeoTIFF, holds the Gold cell's value.
A north-up assumption mirrors the grid and fails every check.
"""
import ast
import importlib.util
import types
from pathlib import Path
from typing import Union

import h5py
import numpy as np
import pytest
import rasterio
from rasterio import CRS
from rasterio.transform import from_origin
from scipy.interpolate import griddata

REWIRE = Path(__file__).resolve().parents[1]
REPO = REWIRE.parent
# south, north, west, east (upstream attr order); ~67 m x 44 m so Scout's 1e-5 deg grid stays small
BBOX = [1.2790, 1.2794, 103.8450, 103.8456]


def _upstream_function(path, name, **globs):
    """Compile one top-level function from an upstream file without importing (or changing) the module."""
    src = (REPO / path).read_text()
    node = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == name)
    mod = ast.Module(body=[node], type_ignores=[])
    ns = {"np": np, "rasterio": rasterio, "CRS": CRS, "Union": Union, "BoundingBox": object, "BlockMeshDict": object,
          **globs}
    exec(compile(mod, str(REPO / path), "exec"), ns)
    return ns[name]


def _adapter(tid):
    spec = importlib.util.spec_from_file_location(f"{tid}_orient", REWIRE / tid / "adapter.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _bbox_ns():
    s, n, w, e = BBOX
    return types.SimpleNamespace(south=s, north=n, west=w, east=e)


def _south_up_field(ny, nx):
    """value = 100*row + col with row 0 at the southern edge: strictly increases northwards."""
    r, c = np.indices((ny, nx))
    return (100.0 * r + c).astype("float32")


def _write_h5(path, name, data, **extra):
    with h5py.File(path, "w") as f:
        ds = f.create_dataset(name, data=data, track_times=False)
        ds.attrs["unit"] = "x"
        ds.attrs["shape"] = data.shape
        ds.attrs["timestamps"] = list(range(data.shape[0]))
        ds.attrs["bounding_box"] = BBOX
        for k, v in extra.items():
            ds.attrs[k] = v


def _assert_matches_geotiff(grid, values, tiff, variable):
    v = values[(values.variable == variable) & (values.timestamp == 0) & (values.level == 0)].set_index("cell_id")
    g = grid.set_index("cell_id").loc[v.index].to_crs("EPSG:4326")
    pts = [(p.x, p.y) for p in g.geometry.representative_point()]
    with rasterio.open(tiff) as src:
        sampled = np.array([s[0] for s in src.sample(pts)], dtype="float64")
    ok = np.isfinite(sampled)
    assert ok.sum() > 0.9 * len(sampled)
    np.testing.assert_allclose(v["value"].to_numpy()[ok], sampled[ok], rtol=0, atol=1e-4)
    north = g.geometry.centroid.y.to_numpy()
    assert np.corrcoef(north[ok], v["value"].to_numpy()[ok])[0, 1] > 0.9  # field increases northwards
    first, last = g.loc[v.index[v.index.str.startswith("r0000")]], g.loc[v.index[~v.index.str.startswith("r0000")]]
    assert first.geometry.centroid.y.max() < last.geometry.centroid.y.max()  # row 0 is the southern edge


def test_wrf_hdf5_is_south_up(tmp_path):
    export_as_tiff = _upstream_function("ucm-wrf/proc_sim/processor.py", "export_as_tiff")
    c_data = np.stack([_south_up_field(6, 5)] * 2)  # (t, south_north, west_east), as create_combined_dataset
    _write_h5(tmp_path / "d04-near-surface-climate", "2m_air_temperature", c_data, timezone="UTC")
    export_as_tiff(c_data[0], str(tmp_path / "t2.tiff"), _bbox_ns(), True, np.float32)  # as proc_sim L850
    out = _adapter("duct.wrf_mesoscale").collect(tmp_path)
    _assert_matches_geotiff(out["wrf_d04_grid"], out["wrf_d04_values"], tmp_path / "t2.tiff", "2m_air_temperature")


def test_palm_hdf5_is_south_up(tmp_path):
    export_as_tiff_4326 = _upstream_function("ucm-palm/proc_prep/processor.py", "export_as_tiff_4326")
    field = _south_up_field(6, 5)  # PALM (y, x), y from the south boundary
    data = np.stack([np.stack([field, field + 0.5])] * 2)  # (t, z, y, x), as add_hdf5_output
    _write_h5(tmp_path / "climatic-variables", "air_temperature", data, heights=[0, 2])
    # ucm-palm-prep georeferences PALM (y, x) arrays with flip_up=True (extract_static_driver_variables)
    export_as_tiff_4326(field, _bbox_ns(), 6, 5, str(tmp_path / "ta.tiff"), np.float32, flip_up=True)
    out = _adapter("duct.palm_microscale").collect(tmp_path)
    _assert_matches_geotiff(out["palm_grid"], out["palm_values"], tmp_path / "ta.tiff", "air_temperature")


def test_scout_hdf5_is_south_up(tmp_path):
    xy = np.random.default_rng(1).uniform(0, 1, (4000, 2))
    xy = np.vstack([xy, [[0, 0], [0, 1], [1, 0], [1, 1]]])
    values = 300.0 + 10 * xy[:, 1] + xy[:, 0]  # Kelvin, increases with the unitless y (north)

    class Mesh:  # what pyvista.read returns, reduced to the two members convert_vtp_to_geotiff uses
        points = np.c_[xy, np.zeros(len(xy))]

        def __getitem__(self, key):
            return values

    pv = types.SimpleNamespace(read=lambda path: Mesh())
    bmd = types.SimpleNamespace(minx=lambda: 0.0, maxx=lambda: 1.0, miny=lambda: 0.0, maxy=lambda: 1.0)
    convert = _upstream_function("ucm-scout/proc/processor.py", "convert_vtp_to_geotiff",
                                 pv=pv, griddata=griddata, from_origin=from_origin)
    s, n, w, e = BBOX
    grid_var = convert("x.vtp", str(tmp_path / "T_0.geotiff"), "T", {"south": s, "north": n, "west": w, "east": e},
                       bmd, conversion=lambda k: k - 273.15)
    _write_h5(tmp_path / "climatic-variables", "air_temperature", np.stack([grid_var]))  # as ucm-scout L752
    out = _adapter("duct.scout_microscale").collect(tmp_path)
    _assert_matches_geotiff(out["scout_grid"], out["scout_values"], tmp_path / "T_0.geotiff", "air_temperature")


def test_row0_is_required():
    import sys
    sys.path.insert(0, str(REWIRE))
    from duct_common import grid_cells
    with pytest.raises(ValueError):
        grid_cells(BBOX, 2, 2, "up")
    south = grid_cells(BBOX, 2, 1, "south").to_crs("EPSG:4326").set_index("cell_id").geometry.centroid.y
    north = grid_cells(BBOX, 2, 1, "north").to_crs("EPSG:4326").set_index("cell_id").geometry.centroid.y
    assert south["r0000c0000"] < south["r0001c0000"] and north["r0000c0000"] > north["r0001c0000"]
