# Seasonal Sen

This folder contains the Seasonal Sen implementation for precomputed BAP monthly composites.

## What It Does

- Loads monthly index stacks from an existing BAP manifest.
- Computes Seasonal Sen slope and intercept per pixel.
- Computes user-selected outputs:
	- `R80P`
	- `percent_change`
	- `DeltaIR`
	- `slope_intercept` (exports both slope and intercept rasters)
- Supports one or multiple reference sites through a single `reference_sites_file` (FeatureCollection).

## Input Assumptions

- BAP inputs are already generated and available on disk.
- Manifest entries should be monthly and include index payloads:
	- `export_profile="seasonal_sen"`
	- `compositing_mode="monthly"`
	- `export_payload in {"indices", "both"}`

## Files

- `bap_loader.py`: helper loader for monthly BAP index stacks.
- `src/main.py`: `Algorithm.run(...)` entrypoint.
- `src/seasonal_sen.py`: core metric logic.

## Tooling

See `tooling/seasonal-sen` for:

- `run_seasonal_sen.py`: run without CWL.
- `seasonal_sen.cwl`: CWL workflow wrapper.
- `seasonal_sen.Dockerfile`: container build file.
- `seasonal_sen_run_parameters.json`: example parameters.

## Parameter Highlights

- Required:
	- `restoration_sites_file`
	- `bap_composite_dir`
- Optional but common:
	- `bap_manifest_file`
	- `reference_sites_file`
	- `reference_bap_composite_dir`
	- `reference_bap_manifest_file`
	- `output_metrics`
	- `restoration_id_column`
	- `restoration_year_column`
	- `target_fraction`
	- `reference_baseline_year`
	- `reference_years_before_restoration`
	- `period`

## Loader Example

```python
from pathlib import Path
import importlib.util

loader_file = Path("/home/jovyan/docs/Code/hatfield-ecco/seasonal-sen/bap_loader.py")
spec = importlib.util.spec_from_file_location("bap_loader", loader_file)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

da = mod.load_monthly_index_stack(
		manifest_path="/path/to/bap-composites/bap_manifest.json",
		index_name="SAVI",
)

print(da.dims)  # ('time', 'y', 'x')
```