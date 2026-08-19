from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, Optional

from pystac import Catalog

from .seasonal_sen import SeasonalSenParameters, run


def _load_geojson_parameter(
    parameters: Dict,
    payload_key: str,
    file_key: str,
) -> Optional[Dict]:
    payload = parameters.get(payload_key)
    if payload is not None:
        return payload

    file_path = parameters.get(file_key)
    if not file_path:
        return None

    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _as_feature_collection(spatial_extent: Dict) -> Dict:
    if not isinstance(spatial_extent, dict):
        raise ValueError("spatial_extent must be a GeoJSON object.")

    geojson_type = spatial_extent.get("type")
    if geojson_type == "FeatureCollection":
        return spatial_extent
    if geojson_type == "Feature":
        return {"type": "FeatureCollection", "features": [spatial_extent]}
    if geojson_type == "Polygon":
        return {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": spatial_extent, "properties": {}}]}

    raise ValueError(
        "spatial_extent must be either a GeoJSON FeatureCollection or Feature."
    )


def _write_temp_geojson(payload: Dict) -> str:
    fc = _as_feature_collection(payload)
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".geojson", delete=False)
    with tmp:
        json.dump(fc, tmp)
    return tmp.name


class Algorithm:

    @staticmethod
    def run(conn, catalog: Catalog, parameters: Dict) -> None:
        """
        Entrypoint for all runnable algorithms.

        :param conn: openEO connection (unused for precomputed BAP workflow)
        :param catalog: STAC catalog that receives metric outputs
        :param parameters: user-supplied algorithm parameters
        """
        del conn
        os.chdir(Path(__file__).resolve().parent)

        temp_files: list[str] = []
        try:
            output_dir_raw = parameters.get("output_dir") or os.getenv("OUTPUT_DIR")
            if not output_dir_raw:
                raise ValueError(
                    "output_dir parameter is required (or set OUTPUT_DIR env var)."
                )

            restoration_sites_file = parameters.get("restoration_sites_file")
            if not restoration_sites_file:
                restoration_payload = _load_geojson_parameter(
                    parameters,
                    "spatial_extent_restoration_site",
                    "spatial_extent_restoration_site_file",
                )
                if restoration_payload is None:
                    raise ValueError(
                        "Provide restoration_sites_file or spatial_extent_restoration_site(_file)."
                    )
                restoration_sites_file = _write_temp_geojson(restoration_payload)
                temp_files.append(restoration_sites_file)

            reference_sites_file = parameters.get("reference_sites_file")
            if not reference_sites_file:
                reference_payload = _load_geojson_parameter(
                    parameters,
                    "spatial_extent_reference_site",
                    "spatial_extent_reference_site_file",
                )
                if reference_payload is not None:
                    reference_sites_file = _write_temp_geojson(reference_payload)
                    temp_files.append(reference_sites_file)

            bap_composite_dir = parameters.get("bap_composite_dir")
            if not bap_composite_dir:
                raise ValueError("bap_composite_dir is required.")

            config = SeasonalSenParameters(
                restoration_sites_file=str(restoration_sites_file),
                reference_sites_file=(
                    str(reference_sites_file) if reference_sites_file else None
                ),
                bap_composite_dir=str(bap_composite_dir),
                bap_manifest_file=parameters.get("bap_manifest_file"),
                reference_bap_composite_dir=parameters.get(
                    "reference_bap_composite_dir"
                ),
                reference_bap_manifest_file=parameters.get(
                    "reference_bap_manifest_file"
                ),
                expected_bap_profile=parameters.get(
                    "expected_bap_profile", "seasonal_sen"
                ),
                index_name=parameters.get("index_name", "SAVI"),
                output_metrics=list(
                    parameters.get("output_metrics", ["R80P", "DeltaIR"])
                ),
                restoration_id_column=parameters.get("restoration_id_column"),
                restoration_year_column=parameters.get("restoration_year_column"),
                target_fraction=float(parameters.get("target_fraction", 0.8)),
                reference_baseline_year=(
                    int(parameters["reference_baseline_year"])
                    if parameters.get("reference_baseline_year") is not None
                    else None
                ),
                reference_years_before_restoration=int(
                    parameters.get("reference_years_before_restoration", 3)
                ),
                start_year_fallback=(
                    int(parameters["start_year_fallback"])
                    if parameters.get("start_year_fallback") is not None
                    else None
                ),
                end_year=(
                    int(parameters["end_year"])
                    if parameters.get("end_year") is not None
                    else None
                ),
                period=(
                    int(parameters["period"])
                    if parameters.get("period") is not None
                    else None
                ),
                block_pixels=int(parameters.get("block_pixels", 20000)),
                all_touched=bool(parameters.get("all_touched", False)),
                output_dir=str(output_dir_raw),
            )

            print(f"Running seasonal-sen from precomputed BAPs with config: {config}")
            run(config, catalog)
            print("Finished seasonal-sen pipeline")

        except Exception as e:
            raise RuntimeError(f"seasonal-sen pipeline failed: {e}") from e

        finally:
            for temp_file in temp_files:
                try:
                    os.remove(temp_file)
                except OSError:
                    pass
