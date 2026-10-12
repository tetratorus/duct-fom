# rewire/ — DUCT Federation of Models on the sg-urban-spine

Rewire, don't rewrite. DUCT's SaaS processors stay exactly as they are; this directory adds spine descriptors
(`transform.yaml`, validated against the sg-urban-spine
([tetratorus/saas-middleware@sg-urban-spine](https://github.com/tetratorus/saas-middleware/tree/sg-urban-spine))
`spec/transform.schema.json`) and thin adapters that move data between the spine's Silver/Gold GeoParquet
(EPSG:3414) and the files each DUCT processor reads/writes. **No upstream file was modified.**

| DUCT processor (`descriptor.json`) | Spine id | Runtime | RL | Golden |
|---|---|---|---|---|
| `ucm-mva/proc_uwc` (ucm-mva-uwc) | `duct.mva_wind_corridor` | python, in-process | 6 | `tanjong_pagar` |
| `ucm-wrf/proc_prep` + `proc_sim` | `duct.wrf_mesoscale` | external (PBS/HPC) | 4 | n/a |
| `ucm-palm/proc_prep` + `proc_sim` | `duct.palm_microscale` | external (NSCC HPC) | 4 | n/a |
| `ucm-scout/proc` | `duct.scout_microscale` | external (NSCC ASPIRE2 + OpenFOAM) | 3 | n/a |
| `bem-cea/proc_gen`, `proc_bee` | pointer to `cea.building_demand` + `cea.solar_potential` ([bem-cea/](bem-cea/README.md)) | — | — | owned by tetratorus/CityEnergyAnalyst |
| `ucm-mva/proc_uvp` (hotspots) | not wired yet | — | — | — |

## Run

Python 3.10 (DUCT requirement).

```bash
python3.10 -m venv venv && . venv/bin/activate
pip install 'git+https://github.com/tetratorus/saas-middleware@sg-urban-spine' \
  'numpy==1.23.5' 'pandas<2.3' 'pyarrow<18' 'geopandas==0.14.4' h5py pytest \
  -r ucm-mva/requirements.txt   # includes saas @ cooling-singapore/saas-middleware@8654f38 (0.6.11)
spine --path rewire validate
spine --path rewire test            # golden: duct.mva_wind_corridor [tanjong_pagar]
pytest rewire/tests
# upstream's own tests, from inside ucm-mva (its tests import proc_uvp/proc_uwc and dependencies/python modules):
(cd ucm-mva && PYTHONPATH=.:dependencies/python TEST_DATA_PATH=/tmp pytest tests -k graph)   # 1 passed
```

End to end through Write-Audit-Publish and the OGC API Features view (fresh lake, golden inputs as Silver):

```bash
python -c "import geopandas as gpd; from spine.lake import Lake; L = Lake('lake'); G = 'rewire/duct.mva_wind_corridor/golden/tanjong_pagar/inputs/'
for t in ('buildings', 'districts'): L.silver_write(t, gpd.read_parquet(G + t + '.parquet'))"
spine --lake lake --path rewire run duct.mva_wind_corridor \
  --params "$(cat rewire/duct.mva_wind_corridor/golden/tanjong_pagar/params.json)"   # audit passes, 1,056 cells published
spine --lake lake serve   # /collections/gold.duct.mva_wind_corridor.mva_wind_corridors/items
```

Pins: upstream `saas` 0.6.x pins `numpy==1.23.5`; newer geopandas/pandas need numpy>=1.24, hence the pins.

## duct.mva_wind_corridor (runnable)

`adapter.py` writes `parameters`, `building-footprints` (GeoJSON, `height` = Silver `height_m`) and `land-mask`
(Silver `districts`) into a temp SaaS working directory, calls `proc_uwc.processor.function(wd)` unmodified, and
reads its four GeoTIFFs back into Gold `mva_wind_corridors`: one row per MVA cell whose centroid lies in a
district (upstream's land mask), polygon in EPSG:3414, `district_id` = that district. Upstream needs non-land
margin around the mask to start its least-cost paths; those margin cells (276 of 1,332 in `tanjong_pagar`) are
not part of the study area and are dropped after upstream's normalisation, so every published value is upstream's
own and no Gold column is null (sg-urban-spine `check_gold` requires every declared column).

Golden `tanjong_pagar`: 1,258 real Overture Maps buildings (release 2026-09-23.1, ODbL) in
103.840–103.850 E, 1.274–1.284 N, 30 m cells; `golden/make_fixture.py` documents the DuckDB query and the two
fixture choices (height = Overture `height`, else `num_floors × 3 m`; buildings with neither dropped; land mask =
bbox inset 50 m). Expected values were frozen with `spine freeze` from the unmodified processor;
`tests/test_mva_adapter.py` also checks the adapter output equals running upstream standalone
(`python processor.py <wd>`) bit-for-bit.

## WRF, PALM, Scout (external: HPC)

They cannot run here: each DUCT processor submits PBS jobs (`qsub`) to NSCC (or a GCE PBS image for WRF) with
compiled WRF/WPS, PALM-4U or OpenFOAM and large boundary data. The descriptors therefore use `runtime: external` and
document the contract:

1. `adapter.prepare(inputs, params, wd)` — Silver -> the processor's working-directory inputs.
2. run the DUCT processor through SaaS Middleware RTI (native adapter, config `nscc`); see
   tetratorus/saas-middleware `rewire/README.md`.
3. `adapter.collect(wd)` — the processor's HDF5 (`d04-near-surface-climate` / `climatic-variables`) ->
   Gold `<model>_grid` + `<model>_values` (long format: cell, variable, unit, timestamp, level, height_m, value;
   `height_m` never null). Every Gold column passes the unmodified spine `check_gold` (no `nullable`):
   - WRF d04 fields are all 2D; `height_m` is the nominal height upstream encodes in the variable name
     (`2m_*` = 2, `10m_*` = 10) and WBGT's 2 m reference height (`ucm-wrf/proc_sim/wbgt.py` `REF_HEIGHT = 2.0`).
   - Scout `air_temperature` is 2D on the OpenFOAM cutting plane `contour_xy_1`, z = 1 m
     (`ucm-scout/case_template/system/predefinedSampleDict`), so `height_m` = 1.
   - PALM 3D fields carry a `heights` attr -> `palm_values`; its 2D `*_xy` fields (`surface_temperature`, `pet`)
     have no height in the HDF5 or in duct-fom, so they go to a separate `palm_xy_values` table without
     `level`/`height_m` rather than getting an invented height.
   - A 2D dataset with no known height makes WRF/Scout `collect()` raise instead of publishing a null.

`tests/test_external_adapters.py` exercises prepare/collect on HDF5 files written in the same layout as the upstream
writers and checks them with spine's `check_gold`; the models themselves were not run. Gaps are listed per
descriptor under `readiness.gaps`.

Grid orientation: DUCT writes these HDF5 arrays **south-up** (row 0 = southern edge) and flips only its GeoTIFFs
(WRF `proc_sim/processor.py` L850 `flip_vertically=True`; Scout `proc/processor.py` builds on
`np.linspace(lat_min, lat_max)` and flips back at L582; PALM keeps PALM's south->north `y`). Each adapter passes
`ROW0 = "south"` to `duct_common.read_climate_hdf5`; `cell_id` `r<row>c<col>` keeps the HDF5 indices.
`tests/test_grid_orientation.py` writes a north/south-asymmetric field, renders it with each upstream's own GeoTIFF
function (extracted verbatim from the upstream source with `ast`, not imported or edited) and checks every Gold cell
polygon samples its own value in that GeoTIFF; with `ROW0 = "north"` all three tests fail.

## IDs (`id_bridge.py`)

No new keys: adapters pass the spine `building_id` as DUCT's feature `id`/`name`. `exact_rows()` emits Silver
`id_bridge` rows for `duct_fom` (identity) and `duct_bem_cea` (bem-cea-gen zone `Name` = name with `:`→`-`);
`spatial_rows()` maps legacy DUCT GeoJSON ids onto Silver buildings by best IoU (`match_method: spatial_overlap`,
`confidence` = IoU).

Landing them in the lake (run after Silver `buildings` is materialised, before/independent of `spine run`):

```bash
python rewire/id_bridge.py --lake lake --source-version <buildings version> \
  [--legacy old_duct_buildings.geojson --id-col id --legacy-source duct_bdp] [--scenario <name>]
```

`Lake.silver_write` replaces the whole table, so `land()` reads Silver `id_bridge`, drops only the rows of the
sources it writes (`duct_fom`, `duct_bem_cea`, and the legacy source if given; re-runs are idempotent and stale ids
go away), keeps every other source's rows, and writes the merged table. With `--scenario` the rows go to the
scenario overlay via `Lake.scenario_set` instead. `tests/test_id_bridge_land.py` covers preservation, idempotence,
the legacy spatial match and the CLI.

## Upstream changes

None.

## Not runnable / not done

- WRF, PALM, Scout: no HPC here (see above); no gold-import command in the spine yet for external results.
- MVA margin cells outside the land mask are not published (see above); upstream normalises over the whole
  bounding box, so a direction's published maximum can be below 1.
- Upstream `ucm-mva/tests/test_mva.py::test_proc_uvp` / `test_proc_uwc` need `TEST_DATA_PATH` fixtures that are not
  in the repo; only `test_proc_uwc_graph` runs: `cd ucm-mva && PYTHONPATH=.:dependencies/python TEST_DATA_PATH=/tmp
  pytest tests -k graph` (1 passed on Python 3.10; without that `PYTHONPATH`, collection fails with
  `ModuleNotFoundError: No module named 'proc_uvp'`).
- `ucm-mva/proc_uvp` (urban ventilation hotspots) and `bem-cea/proc_dcn` (district cooling) are not wired.
