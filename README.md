# PEOPLE-ECCO Terrestrial Solutions

Repository that covers PEOPLE-ECCO Terrestrial Solutions, developed by Hatfield.

It includes the following algorithms:

* *Best Available Pixel*: `bap` directory
* *Seasonal Sen's Slope*: `seasonsal-sen` directory
* *Spectral Recovery*: `breaks` directory
* *Breaks* (Habitat Disturbance Occurrence): `spectral-recovery` directory

## Development
### Run locally

Running as CWL Workflow requires `docker` and [`cwltool` (Link)](https://github.com/common-workflow-language/cwltool).

In order to run, the scripts requires several environment variables:

- OUTPUT_PATH = path to folder where results are written
- OPENEO_AUTH_CLIENT_ID = openeo client id
- OPENEO_AUTH_CLIENT_SECRET = openeo client secret

Additionally a JSON File with the parameters for the algorithm run must be provided. An example of the available parameters is provided in `tooling/spectral-recovery/spectral_recovery_run_parameters.json`.

```aiexclude
# Build Docker Image
docker build -f tooling/spectral-recovery/spectral_recovery.Dockerfile \
    -t ecco-hatfield-spectral-recovery:latest .

# Run using cwl-runner
cwl-runner tooling/spectral-recovery/spectral_recovery.cwl \
    --cdse_client_id=${OPENEO_AUTH_CLIENT_ID} \
    --cdse_client_secret=${OPENEO_AUTH_CLIENT_SECRET} \
    --parameters tooling/spectral-recovery/spectral_recovery_run_parameters.json \
    --run_name ${OUTPUT_PATH}
```

### Run Without CWL

If you prefer to run directly with Python, use the wrapper in `tooling/spectral-recovery`.

```bash
cd /home/jovyan/docs/Code/hatfield-ecco

# Credentials can be passed as args or environment variables.
export OPENEO_AUTH_CLIENT_ID="..."
export OPENEO_AUTH_CLIENT_SECRET="..."

python tooling/spectral-recovery/run_spectral_recovery.py \
    --parameters tooling/spectral-recovery/spectral_recovery_run_parameters.json \
    --output-dir /home/jovyan/docs/PEOPLE-ECCO/openEO_BAP_testing/vietnam_testing/runs \
    --run-name basin_run_01
```

The wrapper loads the same `Algorithm.run(...)` entrypoint as CWL and writes a STAC catalog under `--output-dir/--run-name`.

## BAP Workflow

BAP now supports the same CWL wrapper pattern as spectral-recovery.

### Run with CWL

```bash

# Build Docker Image
docker build -f tooling/bap/bap.Dockerfile \
    -t ghcr.io/people-ecco/hatfield-bap:latest .

# set variables
export OPENEO_AUTH_CLIENT_ID="..."
export OPENEO_AUTH_CLIENT_SECRET="..."
export OUTPUT_PATH="./target/cwl_output"

# Run using cwl-runner
cwl-runner cwl/bap.cwl \
    --cdse_client_id=${OPENEO_AUTH_CLIENT_ID} \
    --cdse_client_secret=${OPENEO_AUTH_CLIENT_SECRET} \
    --parameters tooling/bap/bap_run_parameters.json \
    --run_name ${OUTPUT_PATH}
```

### Run Without CWL

```bash
export OPENEO_AUTH_CLIENT_ID="..."
export OPENEO_AUTH_CLIENT_SECRET="..."

python tooling/bap/run_bap.py \
    --parameters tooling/bap/bap_run_parameters.json \
    --output-dir ./target/output \
    --run-name bap_run_01
```

The BAP wrapper writes:
- GeoTIFF composites and `bap_manifest.json` under `--output-dir/--run-name/output`
- a self-contained STAC catalog under `--output-dir/--run-name`

## Seasonal Sen's Slope Workflow

Seasonal Sen uses BAP scenes as input to derive different indices.

### Run with CWL

```bash
# Build Docker Image
docker build -f tooling/seasonal-sen/seasonal_sen.Dockerfile \
    -t ghcr.io/people-ecco/hatfield-seasonal_sen:latest .

# set variables
export OPENEO_AUTH_CLIENT_ID="..."
export OPENEO_AUTH_CLIENT_SECRET="..."
export OUTPUT_PATH="./target/cwl_output"

# Run using cwl-runner
cwl-runner cwl/seasonal_sen.cwl \
    --cdse_client_id=${OPENEO_AUTH_CLIENT_ID} \
    --cdse_client_secret=${OPENEO_AUTH_CLIENT_SECRET} \
    --parameters tooling/seasonal-sen/seasonal_sen_run_parameters.json \
    --run_name ${OUTPUT_PATH}
```

### Run Without CWL

```bash
export OPENEO_AUTH_CLIENT_ID="..."
export OPENEO_AUTH_CLIENT_SECRET="..."

python tooling/seasonal-sen/run_seasonal_sen.py \
    --parameters tooling/seasonal-sen/seasonal_sen_run_parameters.json \
    --output-dir ./target/output \
    --run-name sen_run_01
```

## BAP + Sen's Slope combined via CWL

Prepare a `.env` file with all required variables set (see below).

```bash
export $(cat .env | xargs) && cwl-runner bap_seasonal_sen.cwl --cdse_client_id=${OPENEO_AUTH_CLIENT_ID} --cdse_client_secret=${OPENEO_AUTH_CLIENT_SECRET} --bap_parameters tooling/bap/bap_run_parameters_bulgaria_map_example_seasonal_sen.json --seasonal_sen_parameters tooling/seasonal-sen/seasonal_sen_run_parameters_bulgaria_map_example_slope_test.json --bap_run_name bulgaria_bap --seasonal_sen_run_name bulgaria_sen

```