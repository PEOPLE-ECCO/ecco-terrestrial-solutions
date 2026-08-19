import glob
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import pystac
from openeo.rest.connection import Connection
from pystac import Catalog

from .BAP import BAPParameters, download_bap

BAP_STYLE = "{\"color\":[\"color\",[\"interpolate\",[\"linear\"],[\"band\",1],170.002,0,710.998,255],[\"interpolate\",[\"linear\"],[\"band\",2],372.003,0,978.000,255],[\"interpolate\",[\"linear\"],[\"band\",3],179.997,0,1100.000,255],[\"case\",[\"==\",[\"band\",1],\"noDataValue\"],0,1]]}	"

def _ensure_feature_collection(spatial_extent: Dict[str, Any]) -> Dict[str, Any]:
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
        "score_weight_dtc",
        "score_weight_date",
        "score_weight_coverage",
        "clip_to_aoi",
        "resume_existing_outputs",
    ]
    for key in configurable_keys:
        value = parameters.get(key)
        if value is not None:
            kwargs[key] = value

    kwargs.setdefault("export_profile", "spectral_recovery")
    kwargs.setdefault("years", [2020, 2021])
    kwargs.setdefault("season_start", "04-01")
    kwargs.setdefault("season_end", "06-30")
    kwargs.setdefault("indices_to_export", ["NBR", "NDVI", "SAVI", "TCW"])

    return BAPParameters(**kwargs)


def _iter_bap_output_paths(output_dir: Path, manifest_filename: str) -> list[tuple[Path, str]]:
    manifest_path = output_dir / manifest_filename
    raster_paths: list[tuple[Path, str]] = []

    if manifest_path.exists():
        with open(manifest_path, "r", encoding="utf-8") as f:
            payload = json.load(f)

        for entry in payload.get("entries", []):
            candidate = entry.get("path")
            if candidate:
                file_path = Path(candidate)
                if not file_path.is_absolute():
                    file_path = output_dir / file_path
            else:
                file_name = entry.get("file")
                if not file_name:
                    continue
                file_path = output_dir / file_name

            if file_path.exists() and file_path.suffix.lower() in {".tif", ".tiff"}:
                raster_paths.append((file_path, entry.get("time_label", "")))

    if raster_paths:
        return raster_paths

    for file_path in glob.iglob(os.path.join(str(output_dir), "*.tif")):
        raster_paths.append((Path(file_path), ""))
    for file_path in glob.iglob(os.path.join(str(output_dir), "*.tiff")):
        raster_paths.append((Path(file_path), ""))

    return raster_paths


def _datetime_from_time_label(time_label: str, file_path: Path) -> datetime:
    if time_label:
        # "2022" → mid-year representative date
        if time_label.isdigit() and len(time_label) == 4:
            return datetime(int(time_label), 7, 1, tzinfo=timezone.utc)
        try:
            dt = datetime.fromisoformat(time_label)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    return _datetime_from_file_path(file_path)


def _datetime_from_file_path(file_path: Path) -> datetime:
    if file_path.stem.isdigit() and len(file_path.stem) == 4:
        return datetime(int(file_path.stem), 7, 1, tzinfo=timezone.utc)
    elif file_path.stem.isdigit() and len(file_path.stem) == 7:
        y = int(file_path.stem[:4])
        m = int(file_path.stem[5:7])
        return datetime(y, m, 1, tzinfo=timezone.utc)

    return datetime.now(tz=timezone.utc)


def _iter_geojson_positions(coordinates: Any):
    if (
        isinstance(coordinates, list)
        and len(coordinates) >= 2
        and all(isinstance(value, (int, float)) for value in coordinates[:2])
    ):
        yield coordinates[:2]
        return

    if isinstance(coordinates, list):
        for child in coordinates:
            yield from _iter_geojson_positions(child)


def _bbox_from_geometry(geometry: Dict[str, Any]) -> list[float]:
    """
    iterates through all coordinates tuples, finds the mins and maxs for lat and lon
    """
    positions = list(_iter_geojson_positions(geometry.get("coordinates")))
    if not positions:
        print("GeoTIFF footprint has no coordinates.")
        return None

    xs = [position[0] for position in positions]
    ys = [position[1] for position in positions]
    return [min(xs), min(ys), max(xs), max(ys)]


def _read_geotiff_footprint(file_path: Path) -> tuple[Dict[str, Any], list[float]]:
    result = subprocess.run(
        ["gdalinfo", "-json", str(file_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    json_start = result.stdout.find("{")
    if json_start == -1:
        print(f"gdalinfo did not return JSON for {file_path}.")
        return None, None

    payload = json.loads(result.stdout[json_start:])
    geometry = payload.get("wgs84Extent")
    if not isinstance(geometry, dict) or "coordinates" not in geometry:
        print(f"gdalinfo did not return a WGS84 footprint for {file_path}.")
        return None, None

    return geometry, _bbox_from_geometry(geometry)


def _add_bap_items_to_catalog(
    catalog: Catalog, output_dir: Path, manifest_filename: str
):
    for file_path, time_label in _iter_bap_output_paths(output_dir, manifest_filename):
        geometry, bbox = _read_geotiff_footprint(file_path)
        item = pystac.Item(
            id=file_path.stem,
            datetime=_datetime_from_time_label(time_label, file_path),
            geometry=geometry,
            bbox=bbox,
            properties={"type": "bap", "style": BAP_STYLE},
        )
        item.add_asset(
            key="image",
            asset=pystac.Asset(
                href=str(file_path), media_type=pystac.MediaType.GEOTIFF
            ),
        )
        catalog.add_item(item)


class Algorithm:

    @staticmethod
    def run(conn: Connection, catalog: Catalog, parameters: Dict) -> None:
        """
        Entrypoint for all runnable Algorithms.

        :param conn: openEO-Connection, already pre-authenticated
        :param catalog: STAC-Catalog storing all outputs this algorithm produces
        :param parameters: User-supplied parameters
        :return: None
        """
        os.chdir(Path(__file__).resolve().parent)

        try:
            output_dir_raw = parameters.get("output_dir") or os.getenv("OUTPUT_DIR")
            if not output_dir_raw:
                print("output_dir parameter not defined, using temp directory")
                import tempfile
                tmp_dir = tempfile.TemporaryDirectory()
                output_dir_raw = tmp_dir.name

            output_dir = Path(output_dir_raw)
            output_dir.mkdir(parents=True, exist_ok=True)

            spatial_extent = _load_geojson_parameter(
                parameters,
                "spatial_extent",
                "spatial_extent_file",
            )
            if spatial_extent is None:
                raise ValueError(
                    "spatial_extent or spatial_extent_file must be provided."
                )

            bap_params = _build_bap_parameters(spatial_extent, parameters)

            print(f"Running BAP with output_dir={output_dir}")
            print(f"BAP parameters: {bap_params}")
            download_bap(bap_params, conn, str(output_dir))

            _add_bap_items_to_catalog(
                catalog,
                output_dir,
                bap_params.manifest_filename,
            )
            print("Finished BAP pipeline")

        except Exception as e:
            raise RuntimeError(f"BAP pipeline failed: {e}") from e
