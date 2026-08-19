import glob
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

import geopandas as gpd
import pystac
import spectral_recovery as sr
from openeo.rest.connection import Connection
from pystac import Catalog
from hatfield.bap.BAP import BAPParameters, download_bap

from .spectral_rec import (
    SpectralRecoveryParameters,
    load_reference_target,
    resolve_bap_timeseries_source,
    run,
    save_reference_target,
)


def _ensure_feature_collection(spatial_extent: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(spatial_extent, dict):
        raise ValueError("spatial_extent must be a GeoJSON object.")

    geojson_type = spatial_extent.get("type")
    if geojson_type == "FeatureCollection":
        return spatial_extent
    if geojson_type == "Feature":
        return {"type": "FeatureCollection", "features": [spatial_extent]}

    raise ValueError(
        "spatial_extent must be either a GeoJSON FeatureCollection or Feature."
    )


def _load_geojson_parameter(
    parameters: Dict[str, Any],
    payload_key: str,
    file_key: str,
) -> Dict[str, Any] | None:
    payload = parameters.get(payload_key)
    if payload is not None:
        return payload

    file_path = parameters.get(file_key)
    if not file_path:
        return None

    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _normalize_int_key_dict(input_dict: Dict[Any, Any]) -> Dict[int, Any]:
    normalized = {}
    for key, value in input_dict.items():
        normalized[int(key)] = value
    return normalized


def _sanitize_basin_id(raw_id: Any) -> str:
    basin_id = str(raw_id).strip()
    if not basin_id:
        basin_id = "unknown"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", basin_id)


def _manifest_is_complete(manifest_path: Path) -> Tuple[bool, str]:
    if not manifest_path.exists():
        return False, f"Manifest missing: {manifest_path}"

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as exc:
        return False, f"Manifest unreadable: {manifest_path} ({exc})"

    entries = payload.get("entries", [])
    if not entries:
        return False, f"Manifest has no entries: {manifest_path}"

    for entry in entries:
        candidate = entry.get("path")
        if candidate:
            file_path = Path(candidate)
            if not file_path.is_absolute():
                file_path = manifest_path.parent / file_path
        else:
            file_name = entry.get("file")
            if not file_name:
                return False, f"Manifest entry missing path and file: {manifest_path}"
            file_path = manifest_path.parent / file_name

        if not file_path.exists():
            return False, f"Missing timeseries file: {file_path}"

    return True, "ok"


def _build_bap_parameters(
    spatial_extent: Dict[str, Any],
    parameters: Dict[str, Any],
) -> BAPParameters:
    kwargs: Dict[str, Any] = {
        "spatial_extent": _ensure_feature_collection(spatial_extent),
    }

    configurable_keys = [
        "compositing_mode",
        "years",
        "season_start",
        "season_end",
        "months",
        "indices_to_export",
        "savi_l",
        "tcw_coefficients",
        "include_reflectance_bands",
        "exclude_scl_classes",
        "export_profile",
        "export_payload",
        "naming_convention",
        "manifest_filename",
        "max_cloud_cover",
        "spatial_resolution",
        "dtc_max_distance",
        "cloud_buffer_px",
        "clip_to_aoi",
        "resume_existing_outputs",
    ]
    for key in configurable_keys:
        value = parameters.get(key)
        if value is not None:
            kwargs[key] = value

    kwargs.setdefault("export_profile", "spectral_recovery")
    kwargs.setdefault("years", [2020, 2021, 2022, 2023, 2024, 2025])
    kwargs.setdefault("season_start", "04-01")
    kwargs.setdefault("season_end", "06-30")
    kwargs.setdefault("indices_to_export", ["NBR", "NDVI", "SAVI", "TCW"])

    return BAPParameters(**kwargs)


def _apply_sr_overrides(config: SpectralRecoveryParameters, parameters: Dict[str, Any]):
    sr_indices = parameters.get("sr_indices")
    if sr_indices is not None:
        config.indices = list(sr_indices)

    sr_metrics = parameters.get("sr_metrics")
    if sr_metrics is not None:
        config.METRICS = list(sr_metrics)

    sr_dist_rest_years = parameters.get("sr_dist_rest_years")
    if sr_dist_rest_years is not None:
        config.DIST_REST_YEARS = _normalize_int_key_dict(sr_dist_rest_years)

    sr_reference_start = parameters.get("sr_reference_start")
    if sr_reference_start is not None:
        config.REFERENCE_START = str(sr_reference_start)

    sr_reference_end = parameters.get("sr_reference_end")
    if sr_reference_end is not None:
        config.REFERENCE_END = str(sr_reference_end)

    sr_band_names = parameters.get("sr_band_names")
    if sr_band_names is not None:
        config.band_names = {
            int(key): value for key, value in dict(sr_band_names).items()
        }


def _add_bap_items_to_catalog(catalog: Catalog, bap_dir: Path):
    for filepath in glob.iglob(os.path.join(str(bap_dir), "*")):
        if filepath.endswith(".json"):
            continue
        item = pystac.Item(
            id=filepath,
            datetime=datetime.now(tz=timezone.utc),
            geometry=None,
            bbox=None,
            properties={"type": "bap", "epsg": 32648},
        )

        item.add_asset(
            key="image",
            asset=pystac.Asset(href=filepath, media_type=pystac.MediaType.GEOTIFF),
        )
        catalog.add_item(item)


def _compute_and_cache_reference_target(
    conn: Connection,
    parameters: Dict[str, Any],
    reference_site_payload: Dict[str, Any],
    reference_site_path: Path,
    reference_target_cache_file: Path,
    metric_timestep: int,
):
    reference_mode = parameters.get("reference_target_mode", "from_sites")
    if reference_mode == "from_cache" and reference_target_cache_file.exists():
        loaded = load_reference_target(str(reference_target_cache_file))
        if isinstance(loaded, dict):
            raise ValueError(
                "reference_target_cache_file contains timeseries source, but basin loop requires a cached reference target."
            )
        print(f"Using existing reference target cache: {reference_target_cache_file}")
        return

    reference_bap_dir_param = parameters.get("reference_bap_dir")
    if reference_bap_dir_param:
        reference_bap_dir = Path(reference_bap_dir_param)
    else:
        reference_bap_dir = reference_target_cache_file.parent / "bap"
    reference_bap_dir.mkdir(parents=True, exist_ok=True)

    reference_bap_params = _build_bap_parameters(reference_site_payload, parameters)
    reference_manifest_file = (
        Path(parameters["reference_bap_manifest_file"])
        if parameters.get("reference_bap_manifest_file")
        else reference_bap_dir / reference_bap_params.manifest_filename
    )

    is_complete, reason = _manifest_is_complete(reference_manifest_file)
    if not is_complete:
        print(
            f"Generating reference BAP timeseries because cache is incomplete: {reason}"
        )
        print(f"Running with parameters: {reference_bap_params}")
        download_bap(reference_bap_params, conn, str(reference_bap_dir))

    is_complete, reason = _manifest_is_complete(reference_manifest_file)
    if not is_complete:
        raise RuntimeError(f"Reference BAP generation failed integrity check: {reason}")

    sr_config = SpectralRecoveryParameters(
        restoration_sites_file=str(reference_site_path),
        reference_sites_file=str(reference_site_path),
        bap_composite_dir=str(reference_bap_dir),
        bap_manifest_file=str(reference_manifest_file),
        expected_bap_profile=parameters.get(
            "expected_bap_profile", "spectral_recovery"
        ),
        reference_target_mode="from_sites",
        reference_target_cache_file=str(reference_target_cache_file),
        reference_cache_content="target",
        metric_timestep=metric_timestep,
    )
    _apply_sr_overrides(sr_config, parameters)

    timeseries_source = resolve_bap_timeseries_source(sr_config)
    spectral_ts = sr.read_timeseries(
        path_to_tifs=timeseries_source,
        band_names=sr_config.band_names,
    )
    index_ts = sr.compute_indices(
        timeseries_data=spectral_ts,
        indices=sr_config.indices,
    )

    reference_target = sr.targets.reference.median(
        reference_sites=str(reference_site_path),
        timeseries_data=index_ts,
        reference_start=sr_config.REFERENCE_START,
        reference_end=sr_config.REFERENCE_END,
    )

    save_reference_target(reference_target, str(reference_target_cache_file))
    print(f"Saved shared reference target cache: {reference_target_cache_file}")


class Algorithm:

    @staticmethod
    def run(conn: Connection, catalog: Catalog, parameters: Dict) -> None:
        """
        Entrypoint for all runnable Algorithms.

        :param conn: openEO-Connection, already pre-authenticated
        :param catalog: STAC-Catalog storing all outputs this algorithm produces
        :param parameters: User-Supplied parameters.
        :return: None
        """
        os.chdir(Path(__file__).resolve().parent)

        try:
            execution_mode = parameters.get("execution_mode", "single_site")
            if execution_mode not in {"single_site", "basin_loop"}:
                raise ValueError(
                    "execution_mode must be either 'single_site' or 'basin_loop'."
                )

            reference_target_mode = parameters.get(
                "reference_target_mode", "from_sites"
            )
            reference_target_cache_file = parameters.get("reference_target_cache_file")
            reference_cache_content = parameters.get(
                "reference_cache_content", "target"
            )
            metric_timestep = parameters.get("metric_timestep", 5)

            if reference_cache_content not in {"target", "timeseries_source"}:
                raise ValueError(
                    "reference_cache_content must be either 'target' or 'timeseries_source'."
                )

            try:
                metric_timestep = int(metric_timestep)
            except (TypeError, ValueError) as exc:
                raise ValueError("metric_timestep must be an integer.") from exc

            if metric_timestep < 1:
                raise ValueError("metric_timestep must be >= 1.")

            if execution_mode == "single_site":
                spatial_extent = (
                    parameters.get("spatial_extent") or default_spatial_extent
                )
                bap_params = _build_bap_parameters(spatial_extent, parameters)
                print(f"Running single_site with BAP parameters: {bap_params}")

                output_dir_raw = parameters.get("output_dir") or os.getenv(
                    "OUTPUT_DIR"
                )
                if not output_dir_raw:
                    print("output_dir parameter not defined, using temp directory")
                    output_dir_raw = tempfile.mkdtemp(
                        prefix="spectral_recovery_single_site_"
                    )
                output_dir = Path(output_dir_raw)
                output_dir.mkdir(parents=True, exist_ok=True)

                bap_composite_dir = str(output_dir / "bap")
                os.makedirs(bap_composite_dir, exist_ok=True)
                print("Starting BAP_processing")
                download_bap(bap_params, conn, bap_composite_dir)
                print("Finished BAP_processing")

                _add_bap_items_to_catalog(catalog, Path(bap_composite_dir))

                print("Starting Spectral Recovery")
                restoration_site_payload = _load_geojson_parameter(
                    parameters,
                    "spatial_extent_restoration_site",
                    "spatial_extent_restoration_site_file",
                )
                if restoration_site_payload is None:
                    raise ValueError(
                        "spatial_extent_restoration_site is required in single_site mode."
                    )

                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".json", delete=False
                ) as tmp:
                    json.dump(restoration_site_payload, tmp)
                    restoration_site_path = tmp.name

                reference_site_payload = _load_geojson_parameter(
                    parameters,
                    "spatial_extent_reference_site",
                    "spatial_extent_reference_site_file",
                )
                reference_site_path = None
                if reference_site_payload is not None:
                    with tempfile.NamedTemporaryFile(
                        mode="w", suffix=".json", delete=False
                    ) as tmp:
                        json.dump(reference_site_payload, tmp)
                        reference_site_path = tmp.name

                if (
                    reference_target_mode == "from_sites"
                    and reference_site_path is None
                ):
                    raise ValueError(
                        "spatial_extent_reference_site is required when reference_target_mode='from_sites'."
                    )

                if (
                    reference_target_mode == "from_cache"
                    and not reference_target_cache_file
                ):
                    raise ValueError(
                        "reference_target_cache_file is required when reference_target_mode='from_cache'."
                    )

                sr_params = SpectralRecoveryParameters(
                    restoration_sites_file=restoration_site_path,
                    reference_sites_file=reference_site_path,
                    bap_composite_dir=bap_composite_dir,
                    bap_manifest_file=os.path.join(
                        bap_composite_dir, bap_params.manifest_filename
                    ),
                    expected_bap_profile=parameters.get(
                        "expected_bap_profile", "spectral_recovery"
                    ),
                    reference_target_mode=reference_target_mode,
                    reference_target_cache_file=reference_target_cache_file,
                    reference_cache_content=reference_cache_content,
                    metric_timestep=metric_timestep,
                )
                _apply_sr_overrides(sr_params, parameters)
                run(sr_params, catalog)
                print("Finished Spectral Recovery")
                return

            # basin_loop execution mode
            basin_aoi_file = parameters.get("aoi_basins_file")
            if not basin_aoi_file:
                raise ValueError("aoi_basins_file is required for basin_loop mode.")

            basin_id_column = parameters.get("basin_id_column")
            if not basin_id_column:
                raise ValueError("basin_id_column is required for basin_loop mode.")

            basin_output_root = Path(
                parameters.get("basin_output_root")
                or tempfile.mkdtemp(prefix="spectral_recovery_basins_")
            )
            basin_output_root.mkdir(parents=True, exist_ok=True)
            reference_dir = basin_output_root / "reference"
            reference_dir.mkdir(parents=True, exist_ok=True)

            if reference_target_cache_file:
                shared_reference_cache = Path(reference_target_cache_file)
                if not shared_reference_cache.is_absolute():
                    shared_reference_cache = basin_output_root / shared_reference_cache
            else:
                shared_reference_cache = reference_dir / "reference_target_cache.pkl"
            shared_reference_cache.parent.mkdir(parents=True, exist_ok=True)

            reference_site_payload = _load_geojson_parameter(
                parameters,
                "spatial_extent_reference_site",
                "spatial_extent_reference_site_file",
            )
            if reference_site_payload is None:
                raise ValueError(
                    "spatial_extent_reference_site is required in basin_loop mode."
                )
            reference_site_payload = _ensure_feature_collection(reference_site_payload)

            reference_site_path = reference_dir / "reference_sites.geojson"
            with open(reference_site_path, "w", encoding="utf-8") as f:
                json.dump(reference_site_payload, f)

            _compute_and_cache_reference_target(
                conn=conn,
                parameters=parameters,
                reference_site_payload=reference_site_payload,
                reference_site_path=reference_site_path,
                reference_target_cache_file=shared_reference_cache,
                metric_timestep=metric_timestep,
            )

            print("reading basin_aoi_file")
            basins_gdf = gpd.read_file(basin_aoi_file)
            if basin_id_column not in basins_gdf.columns:
                raise ValueError(
                    f"basin_id_column '{basin_id_column}' not found in {basin_aoi_file}."
                )
            if basins_gdf[basin_id_column].isna().any():
                raise ValueError(
                    f"basin_id_column '{basin_id_column}' contains null values."
                )
            if basins_gdf[basin_id_column].duplicated().any():
                raise ValueError(
                    f"basin_id_column '{basin_id_column}' must be unique for basin_loop mode."
                )

            total_basins = len(basins_gdf)
            if total_basins < 1:
                raise ValueError("No basins found in aoi_basins_file.")

            run_summary = []
            completed_basins = 0
            successful_basins = 0
            failed_basins = 0
            print(f"Starting basin_loop for {total_basins} basin(s).")

            for basin_index, (_, basin_row) in enumerate(
                basins_gdf.iterrows(), start=1
            ):
                raw_basin_id = basin_row[basin_id_column]
                basin_id = _sanitize_basin_id(raw_basin_id)
                start_pct = ((basin_index - 1) / total_basins) * 100
                print(
                    f"[progress] starting basin {basin_index}/{total_basins} "
                    f"({start_pct:.1f}% complete): {basin_id}"
                )
                basin_dir = basin_output_root / basin_id
                basin_bap_dir = basin_dir / "bap"
                basin_metrics_dir = basin_dir / "metrics"
                basin_dir.mkdir(parents=True, exist_ok=True)
                basin_bap_dir.mkdir(parents=True, exist_ok=True)
                basin_metrics_dir.mkdir(parents=True, exist_ok=True)

                try:
                    basin_gdf = gpd.GeoDataFrame(
                        geometry=[basin_row.geometry],
                        crs=basins_gdf.crs,
                    )
                    basin_feature_collection = json.loads(
                        basin_gdf.to_crs(4326).to_json()
                    )

                    basin_bap_params = _build_bap_parameters(
                        basin_feature_collection,
                        parameters,
                    )
                    basin_manifest_path = (
                        basin_bap_dir / basin_bap_params.manifest_filename
                    )

                    bap_complete, bap_reason = _manifest_is_complete(
                        basin_manifest_path
                    )
                    bap_status = "cached"
                    if not bap_complete:
                        bap_status = "generated"
                        print(
                            f"[{basin_id}] running BAP because cache is incomplete: {bap_reason}"
                        )
                        download_bap(basin_bap_params, conn, str(basin_bap_dir))

                    bap_complete, bap_reason = _manifest_is_complete(
                        basin_manifest_path
                    )
                    if not bap_complete:
                        raise RuntimeError(
                            f"BAP output failed integrity check: {bap_reason}"
                        )

                    basin_restoration_path = basin_dir / "restoration_site.geojson"
                    basin_gdf.to_file(basin_restoration_path, driver="GeoJSON")

                    basin_catalog = pystac.Catalog(
                        id=f"spectral-recovery-{basin_id}",
                        description=f"Spectral recovery outputs for basin {basin_id}",
                    )

                    sr_params = SpectralRecoveryParameters(
                        restoration_sites_file=str(basin_restoration_path),
                        reference_sites_file=str(reference_site_path),
                        bap_composite_dir=str(basin_bap_dir),
                        bap_manifest_file=str(basin_manifest_path),
                        expected_bap_profile=parameters.get(
                            "expected_bap_profile", "spectral_recovery"
                        ),
                        reference_target_mode="from_cache",
                        reference_target_cache_file=str(shared_reference_cache),
                        reference_cache_content="target",
                        metric_timestep=metric_timestep,
                    )
                    _apply_sr_overrides(sr_params, parameters)

                    run(sr_params, basin_catalog)

                    metric_file_count = 0
                    for item in list(basin_catalog.get_all_items()):
                        image_asset = item.assets.get("image")
                        if not image_asset or not image_asset.href:
                            continue

                        source_path = Path(image_asset.href)
                        if not source_path.exists():
                            continue

                        target_path = basin_metrics_dir / source_path.name
                        shutil.copy2(source_path, target_path)
                        image_asset.href = str(target_path)
                        item.id = f"{basin_id}_{target_path.stem}"
                        catalog.add_item(item)
                        metric_file_count += 1

                    run_summary.append(
                        {
                            "basin_id": str(raw_basin_id),
                            "basin_folder": basin_id,
                            "bap_status": bap_status,
                            "metric_file_count": metric_file_count,
                            "status": "ok",
                            "reason": None,
                        }
                    )
                    successful_basins += 1
                    print(
                        f"[{basin_id}] completed with {metric_file_count} metric files."
                    )

                except Exception as basin_exc:
                    run_summary.append(
                        {
                            "basin_id": str(raw_basin_id),
                            "basin_folder": basin_id,
                            "bap_status": "failed",
                            "metric_file_count": 0,
                            "status": "failed",
                            "reason": str(basin_exc),
                        }
                    )
                    failed_basins += 1
                    print(f"[{basin_id}] failed: {basin_exc}")

                finally:
                    completed_basins += 1
                    progress_pct = (completed_basins / total_basins) * 100
                    print(
                        f"[progress] completed {completed_basins}/{total_basins} basins "
                        f"({progress_pct:.1f}%) | ok={successful_basins} failed={failed_basins}"
                    )

            summary_json = basin_output_root / "pipeline_summary.json"
            summary_csv = basin_output_root / "pipeline_summary.csv"
            with open(summary_json, "w", encoding="utf-8") as f:
                json.dump(run_summary, f, indent=2)

            with open(summary_csv, "w", encoding="utf-8") as f:
                f.write(
                    "basin_id,basin_folder,bap_status,metric_file_count,status,reason\n"
                )
                for row in run_summary:
                    reason = (row["reason"] or "").replace("\n", " ").replace(",", ";")
                    f.write(
                        f"{row['basin_id']},{row['basin_folder']},{row['bap_status']},"
                        f"{row['metric_file_count']},{row['status']},{reason}\n"
                    )

            print(f"Basin pipeline summary JSON: {summary_json}")
            print(f"Basin pipeline summary CSV: {summary_csv}")

            failed_runs = [row for row in run_summary if row["status"] != "ok"]
            if failed_runs:
                raise RuntimeError(
                    f"Basin pipeline completed with {len(failed_runs)} failed basin(s). "
                    f"See {summary_json} and {summary_csv} for details."
                )

        except Exception as e:
            raise RuntimeError(f"Spectral recovery pipeline failed: {e}") from e


default_spatial_extent = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [105.52389281475337, 19.954856070257676],
                        [105.56767141131917, 19.954856070257676],
                        [105.56767141131917, 19.924442133192102],
                        [105.52389281475337, 19.924442133192102],
                        [105.52389281475337, 19.954856070257676],
                    ]
                ],
            },
        }
    ],
}
