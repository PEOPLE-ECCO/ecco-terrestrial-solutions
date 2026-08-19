import json
import os
import pickle
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

import dask
import pystac
import spectral_recovery as sr
import xarray as xr
from pystac import Catalog


@dataclass
class SpectralRecoveryParameters:
    restoration_sites_file: str  # geojson
    reference_sites_file: Optional[str]  # geojson
    bap_composite_dir: str
    bap_manifest_file: Optional[str] = None
    expected_bap_profile: str = "spectral_recovery"
    reference_target_mode: Literal["from_sites", "from_cache"] = "from_sites"
    reference_target_cache_file: Optional[str] = None
    reference_cache_content: Literal["target", "timeseries_source"] = "target"
    metric_timestep: int = 5

    # Band mapping for your time series stacks
    band_names = {
        1: "blue",
        2: "green",
        3: "red",
        4: "nir",
        5: "swir16",
        6: "swir22",
    }

    # Indices to compute

    # NBR = is a remote sensing index that compares the reflectance of the NIR and SWIR bands. Higher productivity vegetation (i.e. trees) tends to display high NBR values, whereas crops or bare ground tend to have low NBR values.​
    # SAVI = “Soil Adjusted Vegetation Index” is a modification on the NDVI remote sensing index that aims to improve vegetation monitoring in areas where soil brightness can significantly affect reflectance measurements. Making it interesting to the Lebanon and Bulgaria contexts.
    indices = ["NBR", "NDVI", "SAVI"]

    # Restoration year bins (baseline year -> restoration start year)
    DIST_REST_YEARS = {
        0: [2019, 2020],
        # 1: [2016, 2017],
    }

    # Reference period for reference target
    REFERENCE_START = "2023"
    REFERENCE_END = "2024"

    # # Historical window for targets
    # HIST_REFERENCE_YEARS = {
    #     0: [2016, 2016],
    #     # 1: [2016, 2019],
    # }

    # Metrics to compute
    # R80P = Relative Recovery Status as compared to a target recovery value.
    # DeltaIR = Absolute Change in the spectral index.
    METRICS = ["deltaIR"]

    METRIC_STYLES = {
        "R80P": '{"color":["interpolate",["linear"],["band",1],-1.3,[255,255,255,1], -0.000001, [0,0,0,1], 0,[52,52,52,0]]}',
        "YrYr": '{"color":["interpolate",["linear"],["band",1],-0.6,[255,255,255,1], -0.000001, [0,0,0,1], 0,[52,52,52,0]]}',
        "Y2R": '{"color":["interpolate",["linear"],["band",1],-0.6,[255,255,255,1], -0.000001, [0,0,0,1], 0,[52,52,52,0]]}',
        "deltaIR": '{"color":["interpolate",["linear"],["band",1], MINVAL,[255,255,255,1], MAXVAL, [0,0,0,1]]}',
    }


def save_reference_target(reference_target, output_path: str):
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if isinstance(reference_target, (dict, str)):
        if path.suffix.lower() == ".json":
            with open(path, "w", encoding="utf-8") as f:
                json.dump(reference_target, f, indent=2)
            return
        with open(path, "wb") as f:
            pickle.dump(reference_target, f)
        return

    if path.suffix.lower() in {".pkl", ".pickle"}:
        with open(path, "wb") as f:
            pickle.dump(reference_target, f)
        return

    try:
        reference_target.to_netcdf(path)
    except Exception:
        with open(path, "wb") as f:
            pickle.dump(reference_target, f)


def load_reference_target(input_path: str):
    path = Path(input_path)
    if not path.exists():
        raise FileNotFoundError(f"Reference target cache file not found: {path}")

    if path.suffix.lower() == ".json":
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    if path.suffix.lower() in {".pkl", ".pickle"}:
        with open(path, "rb") as f:
            return pickle.load(f)

    try:
        return xr.load_dataarray(path)
    except Exception:
        pass

    try:
        loaded_ds = xr.load_dataset(path)
    except Exception:
        with open(path, "rb") as f:
            return pickle.load(f)

    data_vars = list(loaded_ds.data_vars)
    if len(data_vars) == 1:
        return loaded_ds[data_vars[0]]
    return loaded_ds


def normalize_timeseries_source(source):
    if not isinstance(source, dict):
        return source

    normalized = {}
    for key, value in source.items():
        try:
            normalized[int(key)] = value
        except (TypeError, ValueError):
            normalized[key] = value
    return normalized


def run(config: SpectralRecoveryParameters, catalog: Catalog):
    print(f"Running spectral-recovery with config: {config}")
    timeseries_source = resolve_bap_timeseries_source(config)

    print("read_timeseries")
    spectral_ts = sr.read_timeseries(
        path_to_tifs=timeseries_source,
        band_names=config.band_names,
    )

    print("compute_indices")
    index_ts = sr.compute_indices(
        timeseries_data=spectral_ts,
        indices=config.indices,
    )

    print("read_restoration_sites")
    rest_site = sr.read_restoration_sites(
        path=str(config.restoration_sites_file),
        dist_rest_years=config.DIST_REST_YEARS,
    )

    # median_hist = sr.targets.historic.window(
    #     timeseries_data=index_ts,
    #     restoration_sites=rest_site,
    #     reference_years=HIST_REFERENCE_YEARS,
    # )
    # median_hist

    if config.reference_target_mode == "from_cache":
        if not config.reference_target_cache_file:
            raise ValueError(
                "reference_target_cache_file is required when reference_target_mode='from_cache'."
            )
        print("load_reference_target")
        cached_reference = load_reference_target(config.reference_target_cache_file)

        if config.reference_cache_content == "timeseries_source" or isinstance(
            cached_reference, (dict, str)
        ):
            if not config.reference_sites_file:
                raise ValueError(
                    "reference_sites_file is required when loading reference from cached timeseries source."
                )

            reference_source = normalize_timeseries_source(cached_reference)

            print("read_timeseries (reference cache)")
            reference_spectral_ts = sr.read_timeseries(
                path_to_tifs=reference_source,
                band_names=config.band_names,
            )

            print("compute_indices (reference cache)")
            reference_index_ts = sr.compute_indices(
                timeseries_data=reference_spectral_ts,
                indices=config.indices,
            )

            print("sr.targets.reference.median (reference cache)")
            ref_target = sr.targets.reference.median(
                reference_sites=str(config.reference_sites_file),
                timeseries_data=reference_index_ts,
                reference_start=config.REFERENCE_START,
                reference_end=config.REFERENCE_END,
            )
        else:
            ref_target = cached_reference

        print(f"Loaded reference cache from: {config.reference_target_cache_file}")
    else:
        if not config.reference_sites_file:
            raise ValueError(
                "reference_sites_file is required when reference_target_mode='from_sites'."
            )
        print("sr.targets.reference.median")
        ref_target = sr.targets.reference.median(
            reference_sites=str(config.reference_sites_file),
            timeseries_data=index_ts,
            reference_start=config.REFERENCE_START,
            reference_end=config.REFERENCE_END,
        )
        if config.reference_target_cache_file:
            cache_payload = ref_target
            if config.reference_cache_content == "timeseries_source":
                cache_payload = timeseries_source
            save_reference_target(cache_payload, config.reference_target_cache_file)
            print(
                f"Saved reference target cache to: {config.reference_target_cache_file}"
            )

    print("compute_metrics")
    metrics = sr.compute_metrics(
        metrics=config.METRICS,
        restoration_sites=rest_site,
        timeseries_data=index_ts,
        recovery_targets=ref_target,
        timestep=config.metric_timestep,
    )

    # Store everything we computed in a persistent temp directory so callers can inspect outputs.
    output_dir = tempfile.mkdtemp(prefix="spectral_recovery_")
    for metric in config.METRICS:
        for index in config.indices:
            # Store as file
            dataset = metrics[0].sel(metric=metric, band=index)
            output_path = f"{output_dir}/{metric}_{index}.tif"
            dataset.rio.to_raster(
                output_path,
                driver="COG",
                creation_options={
                    "COMPRESS": "ZSTD",
                    "BLOCKSIZE": "512",
                    "NUM_THREADS": "ALL_CPUS",
                    "OVERVIEWS": "IGNORE_EXISTING",
                    "RESAMPLING": "NEAREST",
                },
            )

            # Calculate Metadata for STAC output
            bounds = dataset.rio.bounds()
            footprint = {
                "type": "Polygon",
                "coordinates": [
                    [
                        (bounds[0], bounds[1]),
                        (bounds[0], bounds[3]),
                        (bounds[2], bounds[3]),
                        (bounds[2], bounds[1]),
                        (bounds[0], bounds[1]),
                    ]
                ],
            }

            # Somewhat dynamically compute the style
            lazy_min = dataset.min(skipna=True)
            lazy_max = dataset.max(skipna=True)
            min_result, max_result = dask.compute(lazy_min, lazy_max)
            min_val = min_result.item()
            max_val = max_result.item()

            style_base = config.METRIC_STYLES[metric]
            style_base = style_base.replace("MINVAL", str(min_val))
            style_base = style_base.replace("MAXVAL", str(max_val))

            item = pystac.Item(
                id=output_path,
                geometry=footprint,
                bbox=dataset.rio.bounds(),
                datetime=datetime.now(tz=timezone.utc),
                properties={"type": "metric", "epsg": 32648, "style": style_base},
            )

            item.add_asset(
                key="image",
                asset=pystac.Asset(
                    href=output_path, media_type=pystac.MediaType.GEOTIFF
                ),
            )
            catalog.add_item(item)

            # DEBUG
            print(json.dumps(item.to_dict(), indent=4))
    print("Created catalog:")
    catalog.describe()


def resolve_bap_timeseries_source(config: SpectralRecoveryParameters):
    manifest_path = (
        Path(config.bap_manifest_file)
        if config.bap_manifest_file
        else Path(config.bap_composite_dir) / "bap_manifest.json"
    )

    if not manifest_path.exists():
        return config.bap_composite_dir

    with open(manifest_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    entries = payload.get("entries", [])
    selected = [
        entry
        for entry in entries
        if entry.get("export_profile") == config.expected_bap_profile
        and entry.get("export_payload") in {"reflectance", "both"}
    ]

    if not selected:
        return config.bap_composite_dir

    year_to_path = {}
    for entry in selected:
        period_label = str(entry.get("period_label", ""))
        year_match = re.match(r"^(\d{4})", period_label)
        if not year_match:
            continue
        year = int(year_match.group(1))
        path = str(entry.get("path", "")).strip()
        if not path:
            path = str(Path(config.bap_composite_dir) / entry["file"])
        if not os.path.isabs(path):
            path = str(Path(config.bap_composite_dir) / path)
        year_to_path[year] = path

    return year_to_path or config.bap_composite_dir
