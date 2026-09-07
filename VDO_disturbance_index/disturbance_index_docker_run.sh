#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INPUT_DIR="${SCRIPT_DIR}/_input"
OUTPUT_DIR="${SCRIPT_DIR}/_output"

mkdir -p "${OUTPUT_DIR}"

if [[ ! -f "${INPUT_DIR}/zones.geojson" ]]; then
  echo "Missing input file: ${INPUT_DIR}/zones.geojson" >&2
  exit 1
fi

if [[ ! -f "${INPUT_DIR}/breaks.tif" ]]; then
  echo "Missing input file: ${INPUT_DIR}/breaks.tif" >&2
  exit 1
fi

if [[ ! -f "${INPUT_DIR}/firms_fire_points.geojson" ]]; then
  echo "Missing input file: ${INPUT_DIR}/firms_fire_points.geojson" >&2
  exit 1
fi

if [[ ! -f "${INPUT_DIR}/built_areas.tif" ]]; then
  echo "Missing input file: ${INPUT_DIR}/built_areas.tif" >&2
  exit 1
fi

docker run --rm \
  -v "${INPUT_DIR}:/app/_input:ro" \
  -v "${OUTPUT_DIR}:/app/_output:rw" \
  disturbance-index:latest \
  --zones-polys /app/_input/zones.geojson \
  --breaks-raster /app/_input/breaks.tif \
  --fires-points /app/_input/firms_fire_points.geojson \
  --built-raster /app/_input/built_areas.tif \
  --output /app/_output/disturbance_index.geojson
