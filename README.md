# PEOPLE-ECCO Terrestrial Solutions

Repository that covers PEOPLE-ECCO Terrestrial Solutions, developed by Hatfield.

It includes the following algorithms:

* *Best Available Pixel*: `bap` directory
* *Seasonal Sen's Slope*: `seasonsal-sen` directory
* *Spectral Recovery*: `breaks` directory
* *Breaks* (Habitat Disturbance Occurrence): `spectral-recovery` directory

## Development

### Environment

Running as CWL locally Workflow requires `docker` and [`cwlref-runner` (Link)](https://github.com/common-workflow-language/cwltool).

*Installing cwltool in Python virtual environment*:

```bash
$ python3 -m venv venv
$ source venv/bin/activate
$ pip install cwlref-runner
```

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
export $(cat .env | xargs) && cwl-runner cwl/bap_seasonal_sen.cwl --cdse_client_id=${OPENEO_AUTH_CLIENT_ID} --cdse_client_secret=${OPENEO_AUTH_CLIENT_SECRET} --bap_parameters tooling/seasonal-sen/bap_for_seasonal_sen_run_parameters.json --seasonal_sen_parameters tooling/seasonal-sen/seasonal_sen_run_parameters.json --bap_run_name bulgaria_bap --seasonal_sen_run_name bulgaria_sen

```

## Spectral Recovery Workflow

Spectral Recovery has two modes: `single_site`, which generates a BAP on-the-fly, and `basin_loop` which expects a pre-computed BAP composites for a reference area

### Run with CWL

```bash
# Build Docker Image
docker build -f tooling/spectral-recovery/spectral_recovery.Dockerfile \
    -t ghcr.io/people-ecco/hatfield-spectral-recovery:latest .

# set variables
export $(cat .env | xargs) && cwl-runner cwl/spectral_recovery_single_site.cwl --cdse_client_id=${OPENEO_AUTH_CLIENT_ID} --cdse_client_secret=${OPENEO_AUTH_CLIENT_SECRET} --parameters tooling/spectral-recovery/spectral_recovery_single_site_run_parameters.json --run_name sr_run
```

### Run Without CWL

```bash
export OPENEO_AUTH_CLIENT_ID="..."
export OPENEO_AUTH_CLIENT_SECRET="..."

python tooling/seasonal-sen/run_seasonal_sen.py \
    --parameters tooling/seasonal-sen/spectral_recovery_single_site_run_parameters.json \
    --output-dir ./target/output \
    --run-name sr_run_01