import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pystac
import rioxarray
import xarray as xr
from openeo.rest.connection import Connection
from pystac import Catalog

from .yearly_composites import BAND_NAMES, NODATA, detect_breaks

BREAKS_STYLE = '{"color":["interpolate",["linear"],["band",2],MINVAL,[255,255,255,1],MAXVAL,[0,0,0,1]]}'


def _resolve_composite_paths(parameters: Dict[str, Any]) -> Dict[int, Path]:
    """Map year -> BAP composite GeoTIFF, from the manifest when it is available."""
    composite_dir = Path(parameters["bap_composite_dir"])
    manifest_path = (
        Path(parameters["bap_manifest_file"])
        if parameters.get("bap_manifest_file")
        else composite_dir / "bap_manifest.json"
    )

    year_to_path: Dict[int, Path] = {}

    if manifest_path.exists():
        with open(manifest_path, "r", encoding="utf-8") as f:
            entries = json.load(f).get("entries", [])

        for entry in entries:
            year_match = re.match(r"^(\d{4})", str(entry.get("period_label", "")))
            if not year_match:
                continue
            # manifest paths are container-side, so prefer the local file name
            path = composite_dir / Path(entry["file"]).name
            if path.exists():
                year_to_path[int(year_match.group(1))] = path
    else:
        for path in sorted(composite_dir.glob("*.tif")):
            if path.stem.isdigit() and len(path.stem) == 4:
                year_to_path[int(path.stem)] = path

    if not year_to_path:
        raise ValueError(f"No yearly composites found in {composite_dir}")

    return dict(sorted(year_to_path.items()))


def _resolve_band_index(parameters: Dict[str, Any], composite_dir: Path) -> int:
    """1-based band index of the index the breaks run works on."""
    if parameters.get("index_band") is not None:
        return int(parameters["index_band"])

    index_name = parameters.get("index_name", "SAVI")
    manifest_path = (
        Path(parameters["bap_manifest_file"])
        if parameters.get("bap_manifest_file")
        else composite_dir / "bap_manifest.json"
    )
    if manifest_path.exists():
        with open(manifest_path, "r", encoding="utf-8") as f:
            for entry in json.load(f).get("entries", []):
                # entries list both key sets regardless of what was written, so
                # follow export_payload the way BAP.build_export_cube does
                payload = entry.get("export_payload", "indices")
                reflectance = list(entry.get("reflectance_bands") or [])
                indices = list(entry.get("indices") or [])
                if payload == "reflectance":
                    band_order = reflectance
                elif payload == "indices":
                    band_order = indices
                else:
                    band_order = reflectance + indices

                if index_name in band_order:
                    return band_order.index(index_name) + 1

    raise ValueError(
        f"Could not locate band {index_name!r} in the manifest; pass index_band explicitly."
    )


def _load_index_stack(year_to_path: Dict[int, Path], band_index: int) -> xr.DataArray:
    """Stack the per-year composites into the (year, y, x) cube detect_breaks expects."""
    layers = []
    for year, path in year_to_path.items():
        layer = rioxarray.open_rasterio(path, chunks={"x": 256, "y": 256}).sel(
            band=band_index, drop=True
        )
        layers.append(layer.assign_coords(year=year))

    stack = xr.concat(layers, dim="year").astype(np.float32)
    if stack.rio.nodata is not None:
        stack = stack.where(stack != stack.rio.nodata)
    return stack.chunk({"year": -1, "y": 256, "x": 256})


def _add_item_to_catalog(catalog: Catalog, output_path: Path, final: xr.DataArray):
    bounds = final.rio.bounds()
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

    magnitude = final.sel(metric=BAND_NAMES[1]).where(lambda x: x != NODATA)
    style = BREAKS_STYLE.replace("MINVAL", str(float(magnitude.min()))).replace(
        "MAXVAL", str(float(magnitude.max()))
    )

    item = pystac.Item(
        id=output_path.stem,
        geometry=footprint,
        bbox=list(bounds),
        datetime=datetime.now(tz=timezone.utc),
        properties={"type": "breaks", "style": style},
    )
    item.add_asset(
        key="image",
        asset=pystac.Asset(href=str(output_path), media_type=pystac.MediaType.GEOTIFF),
    )
    catalog.add_item(item)


class Algorithm:

    @staticmethod
    def run(conn: Connection, catalog: Catalog, parameters: Dict) -> None:
        """
        Entrypoint for all runnable Algorithms.

        :param conn: openEO-Connection, already pre-authenticated. Unused: breaks
            runs on BAP composites that already exist on disk.
        :param catalog: STAC-Catalog storing all outputs this algorithm produces
        :param parameters: User-Supplied parameters.
        :return: None
        """
        os.chdir(Path(__file__).resolve().parent)

        # BAP_COMPOSITE_DIR wins over the parameter: under CWL the composites are
        # staged into the container, so only the runtime knows their path.
        composite_dir_raw = os.getenv("BAP_COMPOSITE_DIR") or parameters.get(
            "bap_composite_dir"
        )
        if not composite_dir_raw:
            raise ValueError(
                "bap_composite_dir must be provided, either as a parameter or "
                "via the BAP_COMPOSITE_DIR environment variable."
            )
        parameters["bap_composite_dir"] = composite_dir_raw

        output_dir = Path(
            parameters.get("output_dir") or os.getenv("OUTPUT_DIR") or "./output"
        )
        output_dir.mkdir(parents=True, exist_ok=True)

        composite_dir = Path(composite_dir_raw)
        year_to_path = _resolve_composite_paths(parameters)
        band_index = _resolve_band_index(parameters, composite_dir)
        print(
            f"Running breaks on {len(year_to_path)} composites "
            f"({min(year_to_path)}-{max(year_to_path)}), band {band_index}"
        )

        stack = _load_index_stack(year_to_path, band_index)
        final = detect_breaks(
            stack,
            minyear=min(year_to_path),
            maxyear=max(year_to_path),
            crs=stack.rio.crs,
            threshold=float(parameters.get("break_threshold", 0.025)),
            scale=float(parameters.get("index_scale", 1000)),
        )

        output_path = output_dir / f"{parameters.get('output_name', 'breaks')}.tif"
        print(f"Writing {output_path}")
        final.rio.to_raster(output_path, compress="deflate", predictor="3")

        _add_item_to_catalog(catalog, output_path, final)
        print("Finished breaks pipeline")
