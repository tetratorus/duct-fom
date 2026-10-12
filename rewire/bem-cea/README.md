# bem-cea -> `cea.building_demand` (pointer, not a wrapper)

LAYERS.md rule 4, "one copy of each science": DUCT's `bem-cea` processors (`bem-cea-gen`, `bem-cea-bee`,
`bem-cea-dcn`) wrap the City Energy Analyst. CEA has exactly one spine transform, **`cea.building_demand`**, owned by
the CityEnergyAnalyst fork (tetratorus/CityEnergyAnalyst `rewire/`). There is deliberately no `transform.yaml` here.

| DUCT processor (descriptor.json) | DUCT output | Spine equivalent |
|---|---|---|
| bem-cea-gen | cea_run_package, cea_databases | built inside `cea.building_demand` from `silver:buildings` |
| bem-cea-bee | annual_energy_demand, pv_potential, ah_emissions | `gold:cea.building_demand/<table>` (see its transform.yaml) |
| bem-cea-dcn | supply_systems (district cooling network) | not covered by `cea.building_demand`; stays a DUCT/SaaS processor |

IDs: bem-cea-gen names CEA zones from the building `name` with `:` replaced by `-` (`bem-cea/proc_gen/processor.py`
L90). `rewire/id_bridge.py::exact_rows` emits `id_bridge` rows `source=duct_bem_cea` -> `hub=building`, so any
legacy DUCT CEA output joins `cea.building_demand` results on `building_id`.
