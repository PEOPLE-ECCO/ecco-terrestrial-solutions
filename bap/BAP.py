import calendar
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import geopandas as gpd
import numpy as np
from openeo import Connection
from openeo.processes import if_, is_nan
from shapely.geometry import box
from openeo.rest.job import BatchJob
import tempfile
from osgeo import gdal

from .utils_BAP import (
    calculate_cloud_mask,
    calculate_cloud_coverage_score,
    calculate_date_score,
    calculate_distance_to_cloud_score,
    aggregate_BAP_scores,
    create_rank_mask,
)


@dataclass
class BAPParameters:
    """
    All parameters that the BAP algorithm might possibly need.
    Defaults are set where possible
    """

    spatial_extent: dict

    # Compositing options: "yearly" or "monthly"
    compositing_mode: Literal["yearly", "monthly"] = "yearly"

    # Year range / list
    years: list[int] = field(default_factory=lambda: [2017, 2018, 2019])

    # For yearly mode: same seasonal window for each year
    season_start: str = "01-01"
    season_end: str = "03-30"

    # For monthly mode: months to process for each year
    months: list[int] = field(default_factory=lambda: [1, 2, 3])

    # Indices to export (supported: NBR, NDVI, NDMI, NBR2, SAVI, TCW)
    indices_to_export: list[str] = field(default_factory=lambda: ["NBR", "SAVI", "TCW"])

    # SAVI parameter (commonly 0.5 for intermediate vegetation density)
    savi_l: float = 0.5

    # TCW (Sentinel-2) coefficients for bands B02, B03, B04, B08, B11, B12
    tcw_coefficients: dict[str, float] = field(
        default_factory=lambda: {
            "B02": 0.1509,
            "B03": 0.1973,
            "B04": 0.3279,
            "B08": 0.3406,
            "B11": -0.7112,
            "B12": -0.4572,
        }
    )

    # Optional: include original reflectance bands in exports
    include_reflectance_bands: bool = False

    # Export profile for downstream interoperability
    # - custom: preserve user-specified behavior
    # - spectral_recovery: yearly reflectance stacks
    # - seasonal_sen: monthly index stacks
    # - breaks: yearly single-index stacks for Breaks change detection
    export_profile: Literal["custom", "spectral_recovery", "seasonal_sen", "breaks"] = (
        "custom"
    )

    # Export payload controls the content of each output cube
    # - reflectance: only source reflectance bands
    # - indices: only computed indices
    # - both: reflectance + indices
    export_payload: Literal["reflectance", "indices", "both"] = "indices"

    # Output naming convention
    # - legacy: keep backward-compatible naming
    # - profiled: include profile and payload in output name
    naming_convention: Literal["legacy", "profiled"] = "profiled"

    # Manifest output file written in output_dir
    manifest_filename: str = "bap_manifest.json"

    # BAP controls
    max_cloud_cover: int = 70
    spatial_resolution: int = 10
    dtc_max_distance: int = 30
    cloud_buffer_px: int = 2
    exclude_scl_classes: list[int] = field(
        default_factory=lambda: [1, 2, 3, 7, 8, 9, 10]
    )
    # Relative score weights for BAP ranking components
    # score = weighted mean of [distance_to_cloud, date, coverage]
    score_weight_dtc: float = 1.0
    score_weight_date: float = 0.8
    score_weight_coverage: float = 0.5

    # Clip result to detailed AOI polygon before export
    clip_to_aoi: int = True

    # Resume mode: skip period exports whose output file already exists.
    # Useful when rerunning after a backend interruption.
    resume_existing_outputs: bool = True


SPECTRAL_RECOVERY_REFLECTANCE_BANDS = ["B02", "B03", "B04", "B08", "B11", "B12"]


def validate_user_inputs(config: BAPParameters):
    valid_modes = {"yearly", "monthly"}
    if config.compositing_mode not in valid_modes:
        raise ValueError(f"compositing_mode must be one of {valid_modes}")

    supported_indices = {"NBR", "NDVI", "NDMI", "NBR2", "SAVI", "TCW"}
    invalid_indices = [
        idx for idx in config.indices_to_export if idx not in supported_indices
    ]
    if invalid_indices:
        raise ValueError(
            f"Unsupported indices: {invalid_indices}. Supported: {sorted(supported_indices)}"
        )

    if config.compositing_mode == "monthly":
        if not config.months:
            raise ValueError(
                "For monthly compositing, provide at least one month in `months`."
            )
        if any((m < 1 or m > 12) for m in config.months):
            raise ValueError("All values in `months` must be between 1 and 12.")

    if config.savi_l < 0:
        raise ValueError("savi_l must be >= 0.")

    required_tcw_bands = {"B02", "B03", "B04", "B08", "B11", "B12"}
    if (
        "TCW" in config.indices_to_export
        and set(config.tcw_coefficients.keys()) != required_tcw_bands
    ):
        raise ValueError(
            f"tcw_coefficients must contain exactly these keys: {sorted(required_tcw_bands)}"
        )

    valid_payloads = {"reflectance", "indices", "both"}
    if config.export_payload not in valid_payloads:
        raise ValueError(f"export_payload must be one of {valid_payloads}")

    valid_profiles = {"custom", "spectral_recovery", "seasonal_sen", "breaks"}
    if config.export_profile not in valid_profiles:
        raise ValueError(f"export_profile must be one of {valid_profiles}")

    score_weights = [
        config.score_weight_dtc,
        config.score_weight_date,
        config.score_weight_coverage,
    ]
    if any((w < 0) for w in score_weights):
        raise ValueError("score weights must be >= 0.")
    if sum(score_weights) == 0:
        raise ValueError("At least one score weight must be > 0.")


def apply_profile_defaults(config: BAPParameters) -> BAPParameters:
    if config.export_profile == "spectral_recovery":
        config.compositing_mode = "yearly"
        config.export_payload = "reflectance"
    elif config.export_profile == "seasonal_sen":
        config.compositing_mode = "monthly"
        config.export_payload = "indices"
    elif config.export_profile == "breaks":
        config.compositing_mode = "yearly"
        config.export_payload = "indices"
        # Breaks expects one annual value per pixel; keep a single index band.
        if not config.indices_to_export:
            config.indices_to_export = ["SAVI"]
        elif len(config.indices_to_export) > 1:
            config.indices_to_export = [config.indices_to_export[0]]
    return config


def validate_profile_compatibility(config: BAPParameters):
    if config.export_profile == "spectral_recovery":
        if config.compositing_mode != "yearly":
            raise ValueError("spectral_recovery profile requires yearly compositing.")
        if config.export_payload != "reflectance":
            raise ValueError("spectral_recovery profile requires reflectance payload.")

    if config.export_profile == "seasonal_sen":
        if config.compositing_mode != "monthly":
            raise ValueError("seasonal_sen profile requires monthly compositing.")
        if config.export_payload not in {"indices", "both"}:
            raise ValueError("seasonal_sen profile requires indices or both payload.")
        if not config.months:
            raise ValueError("seasonal_sen profile requires at least one month.")

    if config.export_profile == "breaks":
        if config.compositing_mode != "yearly":
            raise ValueError("breaks profile requires yearly compositing.")
        if config.export_payload != "indices":
            raise ValueError("breaks profile requires indices payload.")
        if len(config.indices_to_export) != 1:
            raise ValueError(
                "breaks profile requires exactly one index in indices_to_export."
            )


def required_bands_for_indices(indices):
    index_to_bands = {
        "NBR": {"B08", "B12"},
        "NDVI": {"B08", "B04"},
        "NDMI": {"B08", "B11"},
        "NBR2": {"B11", "B12"},
        "SAVI": {"B08", "B04"},
        "TCW": {"B02", "B03", "B04", "B08", "B11", "B12"},
    }
    bands = set()
    for idx in indices:
        bands |= index_to_bands[idx]
    return sorted(bands)


def compute_index(config, cube, index_name):
    if index_name == "NBR":
        a = cube.band("B08")
        b = cube.band("B12")
        index_cube = (a - b) / (a + b)
    elif index_name == "NDVI":
        a = cube.band("B08")
        b = cube.band("B04")
        index_cube = (a - b) / (a + b)
    elif index_name == "NDMI":
        a = cube.band("B08")
        b = cube.band("B11")
        index_cube = (a - b) / (a + b)
    elif index_name == "NBR2":
        a = cube.band("B11")
        b = cube.band("B12")
        index_cube = (a - b) / (a + b)
    elif index_name == "SAVI":
        nir = cube.band("B08")
        red = cube.band("B04")
        index_cube = ((nir - red) / (nir + red + config.savi_l)) * (1 + config.savi_l)
    elif index_name == "TCW":
        index_cube = (
            cube.band("B02") * config.tcw_coefficients["B02"]
            + cube.band("B03") * config.tcw_coefficients["B03"]
            + cube.band("B04") * config.tcw_coefficients["B04"]
            + cube.band("B08") * config.tcw_coefficients["B08"]
            + cube.band("B11") * config.tcw_coefficients["B11"]
            + cube.band("B12") * config.tcw_coefficients["B12"]
        )
    else:
        raise ValueError(f"Unsupported index: {index_name}")

    return index_cube.add_dimension(name="bands", label=index_name, type="bands")


def add_selected_indices(config: BAPParameters, cube, indices, keep_reflectance=False):
    output_cube = cube if keep_reflectance else None
    for idx in indices:
        idx_cube = compute_index(config, cube, idx)
        output_cube = (
            idx_cube if output_cube is None else output_cube.merge_cubes(idx_cube)
        )
    return output_cube


def build_export_cube(config: BAPParameters, composite):
    if config.export_payload == "reflectance":
        return composite
    if config.export_payload == "indices":
        return add_selected_indices(
            config=config,
            cube=composite,
            indices=config.indices_to_export,
            keep_reflectance=False,
        )
    return add_selected_indices(
        config=config,
        cube=composite,
        indices=config.indices_to_export,
        keep_reflectance=True,
    )


def build_output_name(
    config: BAPParameters, period_label: str, reflectance_bands: list[str]
) -> str:
    if config.export_profile == "spectral_recovery":
        year_token = period_label.split("_", 1)[0]
        return f"{year_token}.tif"

    idx_label = (
        "-".join(config.indices_to_export).lower()
        if config.indices_to_export
        else "none"
    )
    band_label = (
        "-".join([b.lower() for b in reflectance_bands])
        if reflectance_bands
        else "none"
    )
    if config.naming_convention == "legacy":
        return f"bap_{config.compositing_mode}_{period_label}_{idx_label}.tif"

    content_label = (
        idx_label if config.export_payload in {"indices", "both"} else band_label
    )
    return (
        f"bap_{config.export_profile}_{config.compositing_mode}_{period_label}_"
        f"{config.export_payload}_{content_label}.tif"
    )


def _period_time_label(period: dict, compositing_mode: str) -> str:
    if compositing_mode == "monthly":
        label = period["label"]
        year, month = label.split("_")
        return f"{year}-{month}-01"
    return period["temporal_extent"][0]


def write_manifest(output_dir: str, config: BAPParameters, entries: list[dict]):
    manifest_path = Path(output_dir) / config.manifest_filename
    payload = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "export_profile": config.export_profile,
        "compositing_mode": config.compositing_mode,
        "export_payload": config.export_payload,
        "entries": entries,
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def resolve_reflectance_bands(config: BAPParameters) -> list[str]:
    if config.export_profile == "spectral_recovery":
        return SPECTRAL_RECOVERY_REFLECTANCE_BANDS

    bands = required_bands_for_indices(config.indices_to_export)
    if config.export_payload in {"reflectance", "both"} and not bands:
        return SPECTRAL_RECOVERY_REFLECTANCE_BANDS
    return bands


def make_periods(config: BAPParameters):
    periods = []
    if config.compositing_mode == "yearly":
        for year in config.years:
            temporal_extent = [
                f"{year}-{config.season_start}",
                f"{year}-{config.season_end}",
            ]
            label = f"{year}_{config.season_start.replace('-', '')}_{config.season_end.replace('-', '')}"
            periods.append(
                {
                    "label": label,
                    "temporal_extent": temporal_extent,
                }
            )
    else:
        for year in config.years:
            for month in config.months:
                last_day = calendar.monthrange(year, month)[1]
                start = f"{year}-{month:02d}-01"
                end = f"{year}-{month:02d}-{last_day:02d}"
                label = f"{year}_{month:02d}"
                periods.append(
                    {
                        "label": label,
                        "temporal_extent": [start, end],
                    }
                )
    return periods


def build_scl_exclusion_mask(scl, excluded_classes):
    scl_band = scl.band("SCL")
    exclusion_mask = None
    for class_id in excluded_classes:
        class_mask = scl_band == class_id
        exclusion_mask = (
            class_mask if exclusion_mask is None else (exclusion_mask | class_mask)
        )
    return exclusion_mask.add_dimension(name="bands", label="score", type="bands")


def dilate_binary_mask(mask_cube, radius_px):
    if radius_px <= 0:
        return mask_cube
    kernel_size = 2 * radius_px + 1
    kernel = np.ones((kernel_size, kernel_size), dtype="float32")
    expanded = mask_cube.apply_kernel(kernel)
    return expanded > 0


def build_bap_score(
    config: BAPParameters, conn: Connection, temporal_extent, process_area
):
    scl = (
        conn.load_collection(
            "SENTINEL2_L2A",
            temporal_extent=temporal_extent,
            spatial_extent=process_area,
            bands=["SCL"],
            max_cloud_cover=config.max_cloud_cover,
        )
        .resample_spatial(config.spatial_resolution)
        .filter_spatial(process_area)
    )

    scl = scl.apply(lambda x: if_(is_nan(x), 0, x))
    cloud_mask = calculate_cloud_mask(scl)
    buffered_cloud_mask = dilate_binary_mask(cloud_mask, config.cloud_buffer_px)
    scl_exclusion_mask = build_scl_exclusion_mask(scl, config.exclude_scl_classes)
    combined_invalid_mask = buffered_cloud_mask | scl_exclusion_mask

    coverage_score = calculate_cloud_coverage_score(
        buffered_cloud_mask, process_area, scl
    )
    date_score = calculate_date_score(scl)
    dtc_score = calculate_distance_to_cloud_score(
        buffered_cloud_mask,
        config.spatial_resolution,
        max_distance=config.dtc_max_distance,
    )

    weights = [
        config.score_weight_dtc,
        config.score_weight_date,
        config.score_weight_coverage,
    ]
    score = aggregate_BAP_scores(dtc_score, date_score, coverage_score, weights)
    return score.mask(combined_invalid_mask)


def build_bap_composite(
    config: BAPParameters,
    conn,
    temporal_extent,
    process_area,
    reflectance_bands,
    clip_geometry=None,
):
    score = build_bap_score(config, conn, temporal_extent, process_area)
    rank_mask = create_rank_mask(score)

    s2 = conn.load_collection(
        "SENTINEL2_L2A",
        temporal_extent=temporal_extent,
        spatial_extent=process_area,
        bands=reflectance_bands,
        max_cloud_cover=config.max_cloud_cover,
    ).filter_spatial(process_area)

    composite = s2.mask(rank_mask).aggregate_temporal_period("year", "median")
    if clip_geometry is not None:
        composite = composite.mask_polygon(clip_geometry)
    return composite


def download_bap(config: BAPParameters, conn: Connection, output_dir: str):
    config = apply_profile_defaults(config)
    validate_user_inputs(config)
    validate_profile_compatibility(config)

    crs_urn = config.spatial_extent.get("crs", {}).get("properties", {}).get("name")
    if not crs_urn:
        crs_urn = "EPSG:4326"
    gdf = (
        gpd.GeoDataFrame.from_features(config.spatial_extent, crs=crs_urn)
        .dissolve()
        .to_crs("EPSG:4326")
    )

    # Detailed geometry for clipping
    clip_area = gdf.geometry.iloc[0].__geo_interface__

    process_area = clip_area
    # Alternatively we could use BBOX, but if the spatial_extent features are far apart from another the bbox gets huge
    # Bounding box for faster processing
    # bbox = gdf.total_bounds
    # bbox_gdf = gpd.GeoDataFrame(geometry=[box(*bbox)], crs=gdf.crs)
    # process_area = eval(bbox_gdf.to_json())

    if process_area is None:
        raise ValueError("AOI processing geometry is None.")

    reflectance_bands = resolve_reflectance_bands(config)
    periods = make_periods(config)
    manifest_entries: dict[str, dict] = {}
    jobs: list[BatchJob] = []  # List to hold all submitted jobs
    for period in periods:
        temporal_extent = period["temporal_extent"]
        period_label = period["label"]

        out_name = build_output_name(
            config=config,
            period_label=period_label,
            reflectance_bands=reflectance_bands,
        )
        out_path = os.path.join(output_dir, out_name)

        manifest_entry = {
            "file": out_name,
            "path": out_name,
            "period_label": period_label,
            "time_label": _period_time_label(period, config.compositing_mode),
            "temporal_extent": temporal_extent,
            "export_profile": config.export_profile,
            "compositing_mode": config.compositing_mode,
            "export_payload": config.export_payload,
            "reflectance_bands": reflectance_bands,
            "indices": config.indices_to_export,
            "spatial_resolution": config.spatial_resolution,
            "score_weights": {
                "distance_to_cloud": config.score_weight_dtc,
                "date": config.score_weight_date,
                "coverage": config.score_weight_coverage,
            },
        }

        if (
            config.resume_existing_outputs
            and os.path.exists(out_path)
            and os.path.getsize(out_path) > 0
        ):
            print(f"Skipping existing composite for {period_label}: {out_path}")
            manifest_entries[out_name] = manifest_entry
            continue

        print(f"""
            Building composite:
            ---
            Compositing mode: {config.compositing_mode}
            Period: {period_label}
            Temporal Extent: {temporal_extent}
            Indices: {config.indices_to_export}
            Bands requested: {reflectance_bands}
            Area: {process_area}
            Clipping: {config.clip_to_aoi}
        """)

        composite = build_bap_composite(
            config=config,
            conn=conn,
            temporal_extent=temporal_extent,
            process_area=process_area,
            reflectance_bands=reflectance_bands,
            clip_geometry=clip_area if config.clip_to_aoi else None,
        )

        export_cube = build_export_cube(config=config, composite=composite)
        print(f"Exporting to path: {out_path}")
        job = export_cube.create_job(
            title=(
                f"BAP {config.export_profile} {config.compositing_mode} "
                f"composite {period_label} ({config.export_payload})"
            ),
            out_format="GTiff",
            outputfile=out_path,
        )
        job.start()
        jobs.append(job)  # Add job to list

        manifest_entries[job.job_id] = manifest_entry

    # Wait for all submitted jobs to finish
    terminal_statuses = ["finished", "error", "canceled"]
    while True:
        print("Waiting for all jobs to complete...")
        all_done = True

        for job in jobs:
            # Fetch the current status from the backend
            status = job.status()
            print(
                f"{job.job_id} {manifest_entries[job.job_id]["time_label"]}: {status}"
            )
            if status not in terminal_statuses:
                all_done = False

        if all_done:
            print("\nAll jobs have reached a terminal state!")
            break

        # Wait for a bit before checking the server again to avoid spamming the API
        time.sleep(15)

    for job in jobs:
        status = job.status()
        if status != "finished":
            print(f"job failed, skipping download: {job.job_id}")
        else:
            print(
                f"Downloading result of job: {job.job_id} --> {manifest_entries[job.job_id]["path"]}"
            )
            job.download_result(target=os.path.join(output_dir, manifest_entries[job.job_id]["path"]))
            convert_to_cog(os.path.join(output_dir, manifest_entries[job.job_id]["path"]))

    write_manifest(
        output_dir=output_dir,
        config=config,
        entries=[manifest_entries[k] for k in manifest_entries],
    )

def convert_to_cog(file_path: str):
    creation_options = [
        "COMPRESS=LZW",
        "BLOCKSIZE=512",
        "OVERVIEWS=IGNORE_EXISTING",
        "RESAMPLING=NEAREST",
        "NUM_THREADS=ALL_CPUS",
    ]

    # temporary file path in the same directory
    # (Keeping it in the same directory prevents cross-drive move errors)
    directory = os.path.dirname(file_path) or "."
    fd, temp_path = tempfile.mkstemp(suffix=".tif", dir=directory)
    os.close(fd)  # Close the Python file descriptor so GDAL can use it

    print(f"Translating {file_path} to temporary COG...")

    try:
        # translation to the temporary file
        out_ds = gdal.Translate(
            destName=temp_path,
            srcDS=file_path,
            format="COG",
            creationOptions=creation_options,
        )

        # Explicitly clear the GDAL dataset variable!
        # This forces GDAL to flush its cache and release the file lock.
        out_ds = None

        # replace the original file with the new COG
        os.replace(temp_path, file_path)
        print(f"Successfully replaced {file_path} with COG version.")

    except Exception as e:
        # Clean up the temporary file if GDAL crashes
        if os.path.exists(temp_path):
            os.remove(temp_path)
        print(f"Conversion failed. Original file remains untouched.")
        raise e
