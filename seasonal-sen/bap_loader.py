import json
from pathlib import Path
from typing import Optional

import pandas as pd
import rioxarray as rxr
import xarray as xr


def _band_number_for_index(entry: dict, index_name: str) -> int:
    indices = entry.get("indices") or []
    if index_name not in indices:
        raise ValueError(
            f"Index '{index_name}' not present in entry indices: {indices}"
        )

    index_pos = indices.index(index_name) + 1
    payload = entry.get("export_payload")
    if payload == "both":
        reflectance_count = len(entry.get("reflectance_bands") or [])
        return reflectance_count + index_pos
    return index_pos


def load_monthly_index_stack(
    manifest_path: str,
    index_name: str,
    bap_dir: Optional[str] = None,
    profile: str = "seasonal_sen",
) -> xr.DataArray:
    """Load BAP monthly index composites as a (time, y, x) DataArray.

    Args:
        manifest_path: Path to bap_manifest.json.
        index_name: Index to extract from each monthly file (e.g. NBR, NDVI, SAVI).
        bap_dir: Optional root directory for relative paths in the manifest.
        profile: Expected BAP export profile.
    """
    manifest_file = Path(manifest_path)
    with open(manifest_file, "r", encoding="utf-8") as f:
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
        raise ValueError("No monthly seasonal_sen index entries found in manifest.")

    frames = []
    for entry in selected:
        time_label = entry.get("time_label")
        if not time_label:
            continue
        ts = pd.to_datetime(time_label)

        path_value = entry.get("path") or entry.get("file")
        if not path_value:
            continue
        path = Path(path_value)
        if not path.is_absolute():
            base_dir = Path(bap_dir) if bap_dir else manifest_file.parent
            path = base_dir / path

        band_number = _band_number_for_index(entry, index_name)
        da = rxr.open_rasterio(path).sel(band=band_number).squeeze(drop=True)
        da = da.expand_dims(time=[ts])
        frames.append(da)

    if not frames:
        raise ValueError("No valid monthly rasters were loaded from the manifest entries.")

    monthly_stack = xr.concat(frames, dim="time").sortby("time")
    monthly_stack.name = index_name
    return monthly_stack
