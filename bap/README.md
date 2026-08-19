## Best Available Pixels

Utility functions for generating composites. Later used as inputs to the VPT and Breaks code algorithms.

Based on: https://github.com/Open-EO/openeo-community-examples/blob/main/python/RankComposites/bap_composite.ipynb

## Interoperability Profiles

`BAPParameters` now supports profile-driven exports so downstream consumers can use outputs directly.

### `spectral_recovery`
- Forces `compositing_mode="yearly"`
- Forces `export_payload="reflectance"`
- Uses fixed reflectance band order: `B02,B03,B04,B08,B11,B12`
- Uses year-only filenames: `YYYY.tif`

### `seasonal_sen`
- Forces `compositing_mode="monthly"`
- Forces `export_payload="indices"`
- Respects user-selected `months` for each year

### `breaks`
- Forces `compositing_mode="yearly"`
- Forces `export_payload="indices"`
- Forces single-index output so each yearly file contains one band (required by Breaks pixel-wise time series)
- Uses the first entry in `indices_to_export` when multiple indices are provided
- Defaults to `SAVI` when `indices_to_export` is empty

### Manifest

Every `download_bap(...)` run writes `bap_manifest.json` in the output directory.
The manifest records file names, period/time labels, profile, payload type, bands/indices, and temporal extent.

## Example

```python
from hatfield.bap.BAP import BAPParameters, download_bap

params = BAPParameters(
	spatial_extent=my_feature_collection,
	export_profile="spectral_recovery",
	years=[2020, 2021, 2022],
	season_start="04-01",
	season_end="06-30",
	score_weight_dtc=1.0,
	score_weight_date=0.8,
	score_weight_coverage=0.5,
)
download_bap(params, conn, "bap-composites")
```

### Score weight parameters

Users can tune the BAP ranking weights through parameters:

- `score_weight_dtc`: weight for distance-to-cloud score
- `score_weight_date`: weight for date score
- `score_weight_coverage`: weight for cloud coverage score

These values must be non-negative, and at least one must be greater than zero.

## Smoke Validation

Use the smoke script to validate profile rules, naming, period generation, and manifest schema without running openEO jobs:

```bash
cd /home/jovyan/docs/Code/hatfield-ecco
python -m bap.smoke_test_bap_contract
```

## Run with CWL

Use the BAP-specific tooling wrapper in `tooling/bap`.

```bash
cd /home/jovyan/docs/Code/hatfield-ecco

docker build -f tooling/bap/bap.Dockerfile \
	-t ecco-hatfield-bap:latest .

cwl-runner tooling/bap/bap.cwl \
	--cdse_client_id=${OPENEO_AUTH_CLIENT_ID} \
	--cdse_client_secret=${OPENEO_AUTH_CLIENT_SECRET} \
	--parameters tooling/bap/bap_run_parameters.json \
	--run_name ${OUTPUT_PATH}
```

## Run Without CWL

```bash
cd /home/jovyan/docs/Code/hatfield-ecco

python tooling/bap/run_bap.py \
	--parameters tooling/bap/bap_run_parameters.json \
	--output-dir /home/jovyan/docs/PEOPLE-ECCO/openEO_BAP_testing/vietnam_testing/runs \
	--run-name bap_run_01
```

The wrapper calls `Algorithm.run(...)` from `bap/main.py`, runs `download_bap(...)`, and emits both native BAP outputs and STAC metadata.
