# rewire/ — DUCT Federation of Models on the sg-urban-spine

Rewire, don't rewrite. DUCT's SaaS processors stay exactly as they are; this directory adds spine descriptors
(`transform.yaml`, validated against [tetratorus/sg-urban-spine](https://github.com/tetratorus/sg-urban-spine)
`spec/transform.schema.json`) and thin adapters that move data between the spine's Silver/Gold GeoParquet
(EPSG:3414) and the files each DUCT processor reads/writes. **No upstream file was modified.**

| DUCT processor (`descriptor.json`) | Spine id | Runtime | RL | Golden |
|---|---|---|---|---|
| `ucm-mva/proc_uwc` (ucm-mva-uwc) | `duct.mva_wind_corridor` | python, in-process | 6 | `tanjong_pagar` |
| `ucm-wrf/proc_prep` + `proc_sim` | `duct.wrf_mesoscale` | external (PBS/HPC) | 4 | n/a |
| `ucm-palm/proc_prep` + `proc_sim` | `duct.palm_microscale` | external (NSCC HPC) | 4 | n/a |
| `ucm-scout/proc` | `duct.scout_microscale` | external (NSCC ASPIRE2 + OpenFOAM) | 3 | n/a |
| `bem-cea/proc_gen`, `proc_bee` | pointer to `cea.building_demand` ([bem-cea/](bem-cea/README.md)) | — | — | owned by tetratorus/CityEnergyAnalyst |
| `ucm-mva/proc_uvp` (hotspots) | not wired yet | — | — | — |

## Run

Python 3.10 (DUCT requirement).

```bash
python3.10 -m venv venv && . venv/bin/activate
pip install 'git+https://github.com/tetratorus/sg-urban-spine@main' \
  'numpy==1.23.5' 'pandas<2.3' 'pyarrow<18' 'geopandas==0.14.4' h5py pytest \
  -r ucm-mva/requirements.txt   # includes saas @ cooling-singapore/saas-middleware@8654f38 (0.6.11)
spine --path rewire validate
spine --path rewire test            # golden: duct.mva_wind_corridor [tanjong_pagar]
pytest rewire/tests
```

Pins: upstream `saas` 0.6.x pins `numpy==1.23.5`; newer geopandas/pandas need numpy>=1.24, hence the pins.

## duct.mva_wind_corridor (runnable)

`adapter.py` writes `parameters`, `building-footprints` (GeoJSON, `height` = Silver `height_m`) and `land-mask`
(Silver `districts`) into a temp SaaS working directory, calls `proc_uwc.processor.function(wd)` unmodified, and
reads its four GeoTIFFs back into Gold `mva_wind_corridors` (one row per MVA cell, polygon in EPSG:3414,
`district_id` from the cell centroid).

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
   Gold `<model>_grid` + `<model>_values` (long format: cell, variable, unit, timestamp, level, value).

`tests/test_external_adapters.py` exercises prepare/collect on HDF5 files written in the same layout as the upstream
writers and checks them with spine's `check_gold`; the models themselves were not run. Gaps are listed per
descriptor under `readiness.gaps`.

## IDs (`id_bridge.py`)

No new keys: adapters pass the spine `building_id` as DUCT's feature `id`/`name`. `exact_rows()` emits Silver
`id_bridge` rows for `duct_fom` (identity) and `duct_bem_cea` (bem-cea-gen zone `Name` = name with `:`→`-`);
`spatial_rows()` maps legacy DUCT GeoJSON ids onto Silver buildings by best IoU (`match_method: spatial_overlap`,
`confidence` = IoU).

## Upstream changes

None.

## Not runnable / not done

- WRF, PALM, Scout: no HPC here (see above); no gold-import command in the spine yet for external results.
- Upstream `ucm-mva/tests/test_mva.py::test_proc_uvp` / `test_proc_uwc` need `TEST_DATA_PATH` fixtures that are not
  in the repo; only `test_proc_uwc_graph` runs (passes on Python 3.10 with `TEST_DATA_PATH` set to any dir).
- `ucm-mva/proc_uvp` (urban ventilation hotspots) and `bem-cea/proc_dcn` (district cooling) are not wired.
