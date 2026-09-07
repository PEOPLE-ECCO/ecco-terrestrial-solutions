#!/usr/bin/env python3
"""Run the disturbance index workflow from CLI inputs so CWL can pass them in."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import geopandas as gpd
import numpy as np
import rioxarray as rxr

from disturbance_integration import (
    add_zonal_statistic,
    compute_disturbance_index,
    count_points_within_zones,
    majority_filter,
    minimum_mapping_unit_filter,
)


def parse_float_list(value: str) -> list[float]:
    """Parse a comma-separated numeric string into floats."""
    return [float(item.strip()) for item in value.split(",") if item.strip()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the disturbance index workflow with externally supplied inputs."
    )
    parser.add_argument("--zones-polys", required=True, help="Path to the zones polygons file")
    parser.add_argument("--breaks-raster", required=True, help="Path to the breaks raster")
    parser.add_argument("--fires-points", required=True, help="Path to the fires points file")
    parser.add_argument("--built-raster", required=True, help="Path to the built raster")
    parser.add_argument("--output", required=True, help="Output GeoJSON/GPKG/Shapefile path")
    parser.add_argument("--mmu-area", type=float, default=5000.0, help="MMU area in raster CRS units")
    parser.add_argument(
        "--majority-filter-size",
        type=int,
        default=7,
        help="Size of the majority filter kernel",
    )
    parser.add_argument(
        "--connectivity",
        type=int,
        choices=(4, 8),
        default=8,
        help="Connectivity used by the MMU filter",
    )
    parser.add_argument(
        "--weights",
        default="0.7,0.3,0.9",
        help="Comma-separated weights for disturbance_count, fire_count, built_sum",
    )
    parser.add_argument(
        "--cap-percentile",
        type=float,
        default=99.0,
        help="Percentile used for disturbance index normalization",
    )
    parser.add_argument(
        "--magnitude-threshold",
        type=float,
        default=-200.0,
        help="Threshold used to retain breaks by magnitude",
    )
    parser.add_argument(
        "--use-opencv",
        dest="use_opencv",
        action="store_true",
        default=True,
        help="Use OpenCV-backed majority filtering when available",
    )
    parser.add_argument(
        "--no-use-opencv",
        dest="use_opencv",
        action="store_false",
        help="Disable OpenCV-backed majority filtering",
    )
    return parser.parse_args()


def write_output(zones: gpd.GeoDataFrame, output_path: Path) -> None:
    """Write the output zones to disk using an appropriate driver."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    suffix = output_path.suffix.lower()
    if suffix == ".geojson":
        driver = "GeoJSON"
    elif suffix == ".gpkg":
        driver = "GPKG"
    elif suffix == ".shp":
        driver = "ESRI Shapefile"
    else:
        driver = "GeoJSON"

    zones.to_file(output_path, driver=driver)


def main() -> None:
    args = parse_args()
    weights = parse_float_list(args.weights)
    if len(weights) != 3:
        raise ValueError("--weights must contain exactly three comma-separated values")

    zones = gpd.read_file(args.zones_polys)

    breaks_raster = rxr.open_rasterio(args.breaks_raster)
    breaks_raster = breaks_raster.where(breaks_raster != breaks_raster.rio.nodata)
    years, magnitude = breaks_raster

    years = ((magnitude <= args.magnitude_threshold) | (magnitude == magnitude.rio.nodata)) * years
    years = years.fillna(1).astype(np.uint16)

    filtered_years = majority_filter(
        years,
        filter_size=args.majority_filter_size,
        use_opencv=args.use_opencv,
    )
    filtered_years = filtered_years.rio.write_crs(years.rio.crs)

    mmu_filtered = minimum_mapping_unit_filter(
        filtered_years.astype(np.int16),
        mmu_area=args.mmu_area,
        connectivity=args.connectivity,
    )
    mmu_filtered = mmu_filtered.rio.write_nodata(1)
    mmu_filtered = mmu_filtered.where(mmu_filtered > 0)

    fires = gpd.read_file(args.fires_points)
    built_s = rxr.open_rasterio(args.built_raster)[0]

    zones = add_zonal_statistic(
        zones=zones,
        raster=mmu_filtered,
        statistic="count",
        output_field_label="disturbance",
    )
    zones = count_points_within_zones(zones=zones, points=fires, count_column="fire_count")
    zones = add_zonal_statistic(
        zones=zones,
        raster=built_s,
        statistic="sum",
        output_field_label="built",
    )

    analysis_columns = ["disturbance_count", "fire_count", "built_sum"]
    missing_columns = [column for column in analysis_columns if column not in zones.columns]
    if missing_columns:
        raise ValueError(f"Expected columns missing from zones: {missing_columns}")

    zones["disturbance_index"] = compute_disturbance_index(
        zones[analysis_columns],
        factor_weights={
            "disturbance_count": weights[0],
            "fire_count": weights[1],
            "built_sum": weights[2],
        },
        cap_percentile=args.cap_percentile,
    )

    output_path = Path(args.output)
    write_output(zones, output_path)
    print(f"Wrote {len(zones)} zones to {output_path}")


if __name__ == "__main__":
    main()
