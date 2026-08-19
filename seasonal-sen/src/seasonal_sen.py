from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
import pystac
import rioxarray as rxr
import xarray as xr
from pystac import Catalog
from shapely.geometry import mapping

_OUTPUT_ALIASES = {
    "r80p": "r80p",
    "deltair": "deltair",
    "percentchange": "percent_change",
    "pctchange": "percent_change",
    "slopeintercept": "slope_intercept",
    "slopeandintercept": "slope_intercept",
}


@dataclass
class SeasonalSenParameters:
    restoration_sites_file: str
    bap_composite_dir: str
    output_dir: str

    bap_manifest_file: Optional[str] = None
    expected_bap_profile: str = "seasonal_sen"
    index_name: str = "SAVI"

    reference_sites_file: Optional[str] = None
    reference_bap_composite_dir: Optional[str] = None
    reference_bap_manifest_file: Optional[str] = None

    output_metrics: list[str] = field(default_factory=lambda: ["R80P", "DeltaIR"])

    restoration_id_column: Optional[str] = None
    restoration_year_column: Optional[str] = None

    target_fraction: float = 0.8
    reference_baseline_year: Optional[int] = None
    reference_years_before_restoration: int = 3

    start_year_fallback: Optional[int] = None
    end_year: Optional[int] = None

    period: Optional[int] = None
    block_pixels: int = 20000
    all_touched: bool = False


def _sanitize_id(raw_value, default_value: str) -> str:
    candidate = str(raw_value).strip() if raw_value is not None else ""
    if not candidate:
        candidate = default_value
    return re.sub(r"[^A-Za-z0-9._-]+", "_", candidate)


def _parse_year(value) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, float) and np.isnan(value):
        return None

    try:
        ts = pd.to_datetime(value)
        if pd.isna(ts):
            return None
        return int(ts.year)
    except Exception:
        match = re.search(r"(19|20)\d{2}", str(value))
        return int(match.group(0)) if match else None


def _normalize_output_metric(name: str) -> str:
    key = re.sub(r"[^A-Za-z0-9]+", "", str(name).strip().lower())
    normalized = _OUTPUT_ALIASES.get(key)
    if not normalized:
        raise ValueError(
            f"Unsupported output metric '{name}'. Supported outputs: "
            "R80P, percent_change, DeltaIR, slope_intercept."
        )
    return normalized


def _normalize_output_metrics(metrics: list[str]) -> list[str]:
    if not metrics:
        raise ValueError("output_metrics must contain at least one value.")

    normalized = []
    seen = set()
    for metric in metrics:
        value = _normalize_output_metric(metric)
        if value not in seen:
            normalized.append(value)
            seen.add(value)
    return normalized


def _band_number_for_index(entry: dict, index_name: str) -> int:
    indices = entry.get("indices") or []
    if index_name not in indices:
        raise ValueError(
            f"Index '{index_name}' not present in manifest entry indices {indices}."
        )

    index_pos = indices.index(index_name) + 1
    payload = entry.get("export_payload")
    if payload == "both":
        reflectance_count = len(entry.get("reflectance_bands") or [])
        return reflectance_count + index_pos
    return index_pos


def _load_monthly_index_stack(
    manifest_path: Path,
    index_name: str,
    profile: str,
    bap_dir: Optional[Path] = None,
) -> xr.DataArray:
    with open(manifest_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    entries = payload.get("entries", [])
    selected = [
        entry
        for entry in entries
        if entry.get("export_profile") == profile
        and entry.get("compositing_mode") == "monthly"
        and entry.get("export_payload") in {"indices", "both"}
    ]
    if not selected:
        raise ValueError(
            f"No monthly entries with export_profile='{profile}' and index payload found in {manifest_path}."
        )

    frames = []
    for entry in selected:
        print(f"checking BAP entry: {json.dumps(entry)}")
        time_label = entry.get("time_label")
        if not time_label:
            continue
        timestamp = pd.to_datetime(time_label)

        path_value = entry.get("path") or entry.get("file")
        if not path_value:
            continue

        raster_path = Path(path_value)
        if not raster_path.is_absolute():
            base_dir = bap_dir if bap_dir is not None else manifest_path.parent
            raster_path = base_dir / raster_path

        if not raster_path.exists():
            continue

        band_number = _band_number_for_index(entry, index_name)
        da = rxr.open_rasterio(raster_path).sel(band=band_number).squeeze(drop=True)
        da = da.expand_dims(time=[timestamp])
        frames.append(da)

    if not frames:
        raise ValueError(f"No valid rasters were loaded from manifest: {manifest_path}")

    stack = xr.concat(frames, dim="time").sortby("time")
    stack.name = index_name
    return stack


def seasonal_sens_slope(x_old, period: int = 12) -> tuple[float, float]:
    def _preprocess(x):
        arr = np.asarray(x, dtype=float)
        if arr.ndim == 1:
            return arr
        if arr.ndim == 2 and arr.shape[1] == 1:
            return arr.flatten()
        raise ValueError("Input timeseries must be 1D (or 2D with one column).")

    def _sens_estimator(x):
        idx = 0
        n = len(x)
        d = np.ones(int(n * (n - 1) / 2), dtype=float)
        for i in range(n - 1):
            j = np.arange(i + 1, n)
            d[idx : idx + len(j)] = (x[j] - x[i]) / (j - i)
            idx += len(j)
        return d

    x = _preprocess(x_old)
    n = len(x)
    if np.mod(n, period) != 0:
        x = np.pad(
            x, (0, period - np.mod(n, period)), "constant", constant_values=np.nan
        )

    x = x.reshape(int(len(x) / period), period)
    d = []
    for i in range(period):
        d.extend(_sens_estimator(x[:, i]))

    slope = np.nanmedian(np.asarray(d))
    flat_old = np.asarray(x_old, dtype=float).flatten()
    valid_idx = np.arange(flat_old.size)[~np.isnan(flat_old)]
    if valid_idx.size == 0:
        return np.nan, np.nan

    intercept = np.nanmedian(flat_old) - np.median(valid_idx) / period * slope
    return float(slope), float(intercept)


def compute_slope_intercept_blocks(
    index_da: xr.DataArray,
    period: int,
    block_pixels: int = 20000,
) -> tuple[np.ndarray, np.ndarray]:
    arr = np.asarray(index_da.values, dtype=np.float32)
    if arr.ndim != 3:
        raise ValueError("Expected index DataArray with dimensions (time, y, x).")

    t, ny, nx = arr.shape
    npix = ny * nx
    flat = arr.reshape(t, npix)

    slope_flat = np.full(npix, np.nan, dtype=np.float32)
    intercept_flat = np.full(npix, np.nan, dtype=np.float32)

    def _calc(ts):
        if np.all(np.isnan(ts)):
            return np.array([np.nan, np.nan], dtype=np.float32)
        slope, intercept = seasonal_sens_slope(ts, period=period)
        return np.array([np.float32(slope), np.float32(intercept)], dtype=np.float32)

    for start in range(0, npix, block_pixels):
        end = min(start + block_pixels, npix)
        block = flat[:, start:end]
        res = np.apply_along_axis(_calc, 0, block)
        res = np.asarray(res)

        if res.shape[0] == 2 and res.shape[1] == (end - start):
            slope_flat[start:end] = res[0, :]
            intercept_flat[start:end] = res[1, :]
        elif res.shape[1] == 2 and res.shape[0] == (end - start):
            slope_flat[start:end] = res[:, 0]
            intercept_flat[start:end] = res[:, 1]
        else:
            for i in range(end - start):
                slope, intercept = _calc(block[:, i])
                slope_flat[start + i] = slope
                intercept_flat[start + i] = intercept

    return slope_flat.reshape(ny, nx), intercept_flat.reshape(ny, nx)


def _compute_reference_value(
    stack: xr.DataArray,
    rest_year: Optional[int],
    period: int,
    years_before: int,
    baseline_year: Optional[int],
) -> tuple[float, str]:
    if baseline_year is not None:
        subset = stack.sel(time=stack.time.dt.year == int(baseline_year))
        source = f"reference_baseline_year_{baseline_year}"
    elif rest_year is not None:
        ref_end = rest_year - 1
        ref_start = ref_end - years_before + 1
        subset = stack.sel(
            time=(stack.time.dt.year >= ref_start) & (stack.time.dt.year <= ref_end)
        )
        source = f"restoration_relative_{ref_start}_{ref_end}"
    else:
        n_steps = min(int(stack.time.size), max(1, years_before * period))
        subset = stack.isel(time=slice(0, n_steps))
        source = "fallback_early_window"

    if subset.time.size == 0:
        subset = stack
        source = f"{source}_fallback_full"

    value = float(np.nanmedian(subset.values))
    if not np.isfinite(value):
        raise ValueError("Computed reference value is non-finite.")

    return value, source


def _write_metric_tif(
    metric_da: xr.DataArray, template_da: xr.DataArray, out_path: Path
):
    out_path.parent.mkdir(parents=True, exist_ok=True)

    clean = xr.DataArray(
        np.asarray(metric_da.values, dtype=np.float32),
        coords={"y": template_da["y"], "x": template_da["x"]},
        dims=("y", "x"),
        name=metric_da.name,
    )

    try:
        clean = clean.rio.write_crs(template_da.rio.crs, inplace=False)
    except Exception:
        pass

    try:
        clean = clean.rio.write_transform(template_da.rio.transform(), inplace=False)
    except Exception:
        pass

    clean.rio.to_raster(
        str(out_path),
        driver="COG",
        compress="ZSTD",
        blocksize=512,
    )


def _add_metric_item_to_catalog(
    catalog: Catalog,
    output_path: Path,
    metric_name: str,
    index_name: str,
    aoi_id: str,
):
    da = rxr.open_rasterio(output_path).squeeze(drop=True)
    bounds = da.rio.bounds()
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

    DEFAULT_STYLE = "{\"color\":[\"color\",[\"interpolate\",[\"linear\"],[\"band\",1],-1,0,1,255],[\"interpolate\",[\"linear\"],[\"band\",1],-1,0,1,255],[\"interpolate\",[\"linear\"],[\"band\",1],-1,0,1,255],[\"case\",[\"==\",[\"band\",1],\"noDataValue\"],0,[\"!=\",[\"band\",1],[\"band\",1]],0,1]]}"
    RDYLGR_05_STYLE = "{\"color\":[\"color\",[\"interpolate\",[\"linear\"],[\"band\",1],-0.5,215,-0.25,253,0,255,0.25,166,0.5,26],[\"interpolate\",[\"linear\"],[\"band\",1],-0.5,25,-0.25,174,0,255,0.25,217,0.5,150],[\"interpolate\",[\"linear\"],[\"band\",1],-0.5,28,-0.25,97,0,191,0.25,106,0.5,65],[\"case\",[\"==\",[\"band\",1],\"noDataValue\"],0,[\"!=\",[\"band\",1],[\"band\",1]],0,1]]}"
    RDYLGR_15_STYLE = "{\"color\":[\"color\",[\"interpolate\",[\"linear\"],[\"band\",1],-1.5,215,-0.75,253,0,255,0.75,166,1.5,26],[\"interpolate\",[\"linear\"],[\"band\",1],-1.5,25,-0.75,174,0,255,0.75,217,1.5,150],[\"interpolate\",[\"linear\"],[\"band\",1],-1.5,28,-0.75,97,0,191,0.75,106,1.5,65],[\"case\",[\"==\",[\"band\",1],\"noDataValue\"],0,[\"!=\",[\"band\",1],[\"band\",1]],0,1]]}"
    RDYLGR_100_STYLE = "{\"color\":[\"color\",[\"interpolate\",[\"linear\"],[\"band\",1],-100,215,-50,253,0,255,50,166,100,26],[\"interpolate\",[\"linear\"],[\"band\",1],-100,25,-50,174,0,255,50,217,100,150],[\"interpolate\",[\"linear\"],[\"band\",1],-100,28,-50,97,0,191,50,106,100,65],[\"case\",[\"==\",[\"band\",1],\"noDataValue\"],0,[\"!=\",[\"band\",1],[\"band\",1]],0,1]]}"
    match metric_name:
        case "R80P":
            style=RDYLGR_15_STYLE
        case "DeltaIR":
            style=RDYLGR_05_STYLE
        case "percent_change":
            style=RDYLGR_100_STYLE
        case "slope":
            style=DEFAULT_STYLE
        case "intercept":
            style=DEFAULT_STYLE
        case _:
            style=DEFAULT_STYLE

    item = pystac.Item(
        id=output_path.stem,
        geometry=footprint,
        bbox=[bounds[0], bounds[1], bounds[2], bounds[3]],
        datetime=datetime.now(tz=timezone.utc),
        properties={
            "type": f"Seasonal Sen Slope - {metric_name}",
            "metric": metric_name,
            "index": index_name,
            "aoi_id": aoi_id,
            "style": style
        },
    )
    item.add_asset(
        key="image",
        asset=pystac.Asset(href=str(output_path), media_type=pystac.MediaType.GEOTIFF),
    )
    catalog.add_item(item)


def _resolve_manifest_path(manifest_file: Optional[str], composite_dir: str) -> Path:
    if manifest_file:
        return Path(manifest_file)
    return Path(composite_dir) / "bap_manifest.json"


def _infer_period(index_da: xr.DataArray, configured_period: Optional[int]) -> int:
    if configured_period is not None:
        period = int(configured_period)
        if period < 1:
            raise ValueError("period must be >= 1.")
        return period

    months = pd.DatetimeIndex(index_da.time.values).month.unique()
    inferred = int(len(months))
    return max(1, inferred)


def run(config: SeasonalSenParameters, catalog: Catalog):
    output_metrics = _normalize_output_metrics(config.output_metrics)

    restoration_manifest = _resolve_manifest_path(
        config.bap_manifest_file,
        config.bap_composite_dir,
    )
    if not restoration_manifest.exists():
        raise FileNotFoundError(f"BAP manifest not found: {restoration_manifest}")

    restoration_stack = _load_monthly_index_stack(
        manifest_path=restoration_manifest,
        index_name=config.index_name,
        profile=config.expected_bap_profile,
        bap_dir=Path(config.bap_composite_dir),
    )

    restoration_gdf = gpd.read_file(config.restoration_sites_file)
    if restoration_gdf.empty:
        raise ValueError("restoration_sites_file contains no features.")

    stack_crs = restoration_stack.rio.crs
    if stack_crs is None:
        raise ValueError("BAP stack has no CRS metadata.")

    if restoration_gdf.crs is None:
        raise ValueError("restoration_sites_file has no CRS.")

    restoration_gdf = restoration_gdf.to_crs(stack_crs)

    reference_stack = None
    reference_gdf = None
    if config.reference_sites_file:
        reference_manifest = _resolve_manifest_path(
            config.reference_bap_manifest_file,
            config.reference_bap_composite_dir or config.bap_composite_dir,
        )
        if not reference_manifest.exists():
            raise FileNotFoundError(
                f"Reference BAP manifest not found: {reference_manifest}"
            )

        reference_stack = _load_monthly_index_stack(
            manifest_path=reference_manifest,
            index_name=config.index_name,
            profile=config.expected_bap_profile,
            bap_dir=Path(
                config.reference_bap_composite_dir or config.bap_composite_dir
            ),
        )
        reference_gdf = gpd.read_file(config.reference_sites_file)
        if reference_gdf.empty:
            raise ValueError("reference_sites_file contains no features.")
        if reference_gdf.crs is None:
            raise ValueError("reference_sites_file has no CRS.")
        reference_gdf = reference_gdf.to_crs(reference_stack.rio.crs)

    period = _infer_period(restoration_stack, config.period)

    output_root = Path(config.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    summary_rows = []
    for row_idx, row in restoration_gdf.iterrows():
        aoi_id_value = (
            row.get(config.restoration_id_column)
            if config.restoration_id_column
            and config.restoration_id_column in restoration_gdf.columns
            else row_idx
        )
        aoi_id = _sanitize_id(aoi_id_value, default_value=f"aoi_{row_idx}")

        geom = row.geometry
        if geom is None or geom.is_empty:
            summary_rows.append(
                {
                    "aoi_id": aoi_id,
                    "status": "failed",
                    "reason": "missing or empty geometry",
                }
            )
            continue

        try:
            savi_ts = restoration_stack.rio.clip(
                [mapping(geom)],
                restoration_gdf.crs,
                drop=True,
                all_touched=config.all_touched,
            )
        except Exception as exc:
            summary_rows.append(
                {
                    "aoi_id": aoi_id,
                    "status": "failed",
                    "reason": f"clip_failed: {exc}",
                }
            )
            continue

        if savi_ts.time.size == 0:
            summary_rows.append(
                {
                    "aoi_id": aoi_id,
                    "status": "failed",
                    "reason": "no time steps after clip",
                }
            )
            continue

        slope_arr, intercept_arr = compute_slope_intercept_blocks(
            savi_ts,
            period=period,
            block_pixels=int(config.block_pixels),
        )

        template = savi_ts.isel(time=0, drop=True)
        slope_da = xr.DataArray(
            slope_arr,
            coords={"y": template["y"], "x": template["x"]},
            dims=("y", "x"),
            name=f"slope_{config.index_name}",
        )
        intercept_da = xr.DataArray(
            intercept_arr,
            coords={"y": template["y"], "x": template["x"]},
            dims=("y", "x"),
            name=f"intercept_{config.index_name}",
        )

        rest_year = None
        if (
            config.restoration_year_column
            and config.restoration_year_column in restoration_gdf.columns
        ):
            rest_year = _parse_year(row.get(config.restoration_year_column))

        start_year = (
            int(config.start_year_fallback)
            if config.start_year_fallback is not None
            else int(pd.DatetimeIndex(savi_ts.time.values).year.min())
        )
        end_year = (
            int(config.end_year)
            if config.end_year is not None
            else int(pd.DatetimeIndex(savi_ts.time.values).year.max())
        )

        if rest_year is not None:
            span_years = max(0, end_year - int(rest_year) + 1)
        else:
            span_years = max(0, end_year - int(start_year) + 1)

        pred_now = intercept_da + slope_da * float(span_years)
        delta_ir = (slope_da * float(span_years)).astype(np.float32)
        delta_ir.name = f"DeltaIR_{config.index_name}"

        if reference_stack is not None and reference_gdf is not None:
            ref_geom = reference_gdf.geometry.unary_union
            try:
                reference_ts = reference_stack.rio.clip(
                    [mapping(ref_geom)],
                    reference_gdf.crs,
                    drop=True,
                    all_touched=config.all_touched,
                )
            except Exception:
                reference_ts = savi_ts
        else:
            reference_ts = savi_ts

        try:
            ref_value, ref_source = _compute_reference_value(
                stack=reference_ts,
                rest_year=rest_year,
                period=period,
                years_before=int(config.reference_years_before_restoration),
                baseline_year=config.reference_baseline_year,
            )
        except Exception:
            ref_value = np.nan
            ref_source = "reference_unavailable"

        aoi_output_dir = output_root / aoi_id / "metrics"
        written_outputs = []

        if "r80p" in output_metrics:
            denom = float(config.target_fraction) * ref_value
            if np.isfinite(denom) and abs(denom) > 1e-12:
                r80p = (pred_now / denom).astype(np.float32)
            else:
                r80p = xr.full_like(pred_now, np.nan, dtype=np.float32)
            r80p.name = f"R80P_{config.index_name}"

            out_path = aoi_output_dir / f"R80P_{config.index_name}.tif"
            _write_metric_tif(r80p, template, out_path)
            _add_metric_item_to_catalog(
                catalog, out_path, "R80P", config.index_name, aoi_id
            )
            written_outputs.append(str(out_path))

        if "deltair" in output_metrics:
            out_path = aoi_output_dir / f"DeltaIR_{config.index_name}.tif"
            _write_metric_tif(delta_ir, template, out_path)
            _add_metric_item_to_catalog(
                catalog, out_path, "DeltaIR", config.index_name, aoi_id
            )
            written_outputs.append(str(out_path))

        if "percent_change" in output_metrics:
            if np.isfinite(ref_value) and abs(ref_value) > 1e-12:
                percent_change = (delta_ir / ref_value) * 100.0
            else:
                percent_change = xr.full_like(delta_ir, np.nan, dtype=np.float32)
            percent_change = percent_change.astype(np.float32)
            percent_change.name = f"PercentChange_{config.index_name}"

            out_path = aoi_output_dir / f"PercentChange_{config.index_name}.tif"
            _write_metric_tif(percent_change, template, out_path)
            _add_metric_item_to_catalog(
                catalog,
                out_path,
                "percent_change",
                config.index_name,
                aoi_id,
            )
            written_outputs.append(str(out_path))

        if "slope_intercept" in output_metrics:
            slope_path = aoi_output_dir / f"Slope_{config.index_name}.tif"
            intercept_path = aoi_output_dir / f"Intercept_{config.index_name}.tif"

            _write_metric_tif(slope_da.astype(np.float32), template, slope_path)
            _write_metric_tif(intercept_da.astype(np.float32), template, intercept_path)

            _add_metric_item_to_catalog(
                catalog,
                slope_path,
                "slope",
                config.index_name,
                aoi_id,
            )
            _add_metric_item_to_catalog(
                catalog,
                intercept_path,
                "intercept",
                config.index_name,
                aoi_id,
            )

            written_outputs.extend([str(slope_path), str(intercept_path)])

        summary_rows.append(
            {
                "aoi_id": aoi_id,
                "status": "ok",
                "reason": None,
                "time_steps": int(savi_ts.time.size),
                "period": int(period),
                "restoration_year": rest_year,
                "start_year": int(start_year),
                "end_year": int(end_year),
                "span_years": int(span_years),
                "reference_value": (
                    float(ref_value) if np.isfinite(ref_value) else np.nan
                ),
                "reference_value_source": ref_source,
                "outputs": ";".join(written_outputs),
            }
        )

    summary_df = pd.DataFrame(summary_rows)
    summary_csv = output_root / "pipeline_summary.csv"
    summary_json = output_root / "pipeline_summary.json"
    summary_df.to_csv(summary_csv, index=False)
    summary_df.to_json(summary_json, orient="records", indent=2)

    print(f"Saved summary CSV: {summary_csv}")
    print(f"Saved summary JSON: {summary_json}")
