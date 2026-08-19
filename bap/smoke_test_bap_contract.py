"""Smoke checks for BAP interoperability contract.

This script validates profile behavior and manifest structure without running
openEO jobs. It is intended as a fast local sanity check after refactors.
"""

import argparse
import json
import sys
import tempfile
import types
from pathlib import Path

# Ensure repository root is importable regardless of invocation directory.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _install_import_stubs():
    """Install minimal stubs for optional heavy dependencies.

    The smoke checks exercise export contract helpers only and do not call
    runtime openEO/geospatial code paths.
    """
    if "geopandas" not in sys.modules:
        sys.modules["geopandas"] = types.ModuleType("geopandas")

    if "numpy" not in sys.modules:
        numpy_mod = types.ModuleType("numpy")
        numpy_mod.ceil = lambda x: x
        numpy_mod.ones = lambda shape, dtype=None: [1] * (
            shape[0] * shape[1] if isinstance(shape, tuple) else shape
        )
        numpy_mod.outer = lambda a, b: [[1 for _ in b] for _ in a]
        numpy_mod.array = lambda x, dtype=None: x
        numpy_mod.float32 = float
        sys.modules["numpy"] = numpy_mod

    if "shapely" not in sys.modules:
        sys.modules["shapely"] = types.ModuleType("shapely")
    if "shapely.geometry" not in sys.modules:
        shapely_geometry = types.ModuleType("shapely.geometry")
        shapely_geometry.box = lambda *args, **kwargs: None
        sys.modules["shapely.geometry"] = shapely_geometry

    if "openeo" not in sys.modules:
        openeo_mod = types.ModuleType("openeo")
        openeo_mod.Connection = object
        openeo_mod.DataCube = object
        sys.modules["openeo"] = openeo_mod

    if "openeo.processes" not in sys.modules:
        openeo_processes = types.ModuleType("openeo.processes")
        openeo_processes.if_ = lambda cond, a, b: a if cond else b
        openeo_processes.is_nan = lambda x: False
        sys.modules["openeo.processes"] = openeo_processes

    if "openeo.api" not in sys.modules:
        sys.modules["openeo.api"] = types.ModuleType("openeo.api")
    if "openeo.api.process" not in sys.modules:
        openeo_api_process = types.ModuleType("openeo.api.process")

        class _Parameter:
            def __init__(self, *_args, **_kwargs):
                pass

        openeo_api_process.Parameter = _Parameter
        sys.modules["openeo.api.process"] = openeo_api_process

    if "scipy" not in sys.modules:
        sys.modules["scipy"] = types.ModuleType("scipy")
    if "scipy.signal" not in sys.modules:
        sys.modules["scipy.signal"] = types.ModuleType("scipy.signal")
    if "scipy.signal.windows" not in sys.modules:
        scipy_windows = types.ModuleType("scipy.signal.windows")
        scipy_windows.gaussian = lambda M, std: [1.0] * int(M)
        sys.modules["scipy.signal.windows"] = scipy_windows


_install_import_stubs()

from bap.BAP import (
    BAPParameters,
    apply_profile_defaults,
    build_output_name,
    make_periods,
    resolve_reflectance_bands,
    validate_profile_compatibility,
    validate_user_inputs,
    write_manifest,
)


def _minimal_spatial_extent() -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [0.0, 0.0],
                            [0.1, 0.0],
                            [0.1, 0.1],
                            [0.0, 0.1],
                            [0.0, 0.0],
                        ]
                    ],
                },
            }
        ],
    }


def _assert(condition: bool, message: str):
    if not condition:
        raise AssertionError(message)


def check_spectral_recovery_profile():
    cfg = BAPParameters(
        spatial_extent=_minimal_spatial_extent(),
        export_profile="spectral_recovery",
        years=[2020, 2021],
        season_start="04-01",
        season_end="06-30",
        indices_to_export=["NBR", "NDVI", "SAVI"],
    )
    cfg = apply_profile_defaults(cfg)
    validate_user_inputs(cfg)
    validate_profile_compatibility(cfg)

    _assert(
        cfg.compositing_mode == "yearly", "spectral_recovery must force yearly mode"
    )
    _assert(
        cfg.export_payload == "reflectance",
        "spectral_recovery must force reflectance payload",
    )

    bands = resolve_reflectance_bands(cfg)
    _assert(
        bands == ["B02", "B03", "B04", "B08", "B11", "B12"],
        "spectral_recovery reflectance order must be fixed",
    )

    periods = make_periods(cfg)
    _assert(len(periods) == 2, "Expected one yearly period per requested year")

    name = build_output_name(cfg, periods[0]["label"], bands)
    _assert(
        name == "2020.tif", "spectral_recovery filename must be year-only (YYYY.tif)"
    )


def check_seasonal_sen_profile():
    cfg = BAPParameters(
        spatial_extent=_minimal_spatial_extent(),
        export_profile="seasonal_sen",
        years=[2020],
        months=[1, 2, 3, 10, 11],
        indices_to_export=["NBR", "SAVI", "TCW"],
    )
    cfg = apply_profile_defaults(cfg)
    validate_user_inputs(cfg)
    validate_profile_compatibility(cfg)

    _assert(cfg.compositing_mode == "monthly", "seasonal_sen must force monthly mode")
    _assert(cfg.export_payload == "indices", "seasonal_sen must force indices payload")

    periods = make_periods(cfg)
    labels = {p["label"] for p in periods}
    _assert(
        labels == {"2020_01", "2020_02", "2020_03", "2020_10", "2020_11"},
        "Monthly period labels do not match configured months",
    )

    name = build_output_name(cfg, "2020_01", resolve_reflectance_bands(cfg))
    _assert(
        "seasonal_sen" in name and "indices" in name,
        "Profiled filename is missing expected tokens",
    )


def check_breaks_profile():
    cfg = BAPParameters(
        spatial_extent=_minimal_spatial_extent(),
        export_profile="breaks",
        years=[2020, 2021],
        season_start="04-01",
        season_end="06-30",
        indices_to_export=["SAVI", "NBR"],
    )
    cfg = apply_profile_defaults(cfg)
    validate_user_inputs(cfg)
    validate_profile_compatibility(cfg)

    _assert(cfg.compositing_mode == "yearly", "breaks must force yearly mode")
    _assert(cfg.export_payload == "indices", "breaks must force indices payload")
    _assert(cfg.indices_to_export == ["SAVI"], "breaks must keep a single index band")

    periods = make_periods(cfg)
    _assert(len(periods) == 2, "Expected one yearly period per requested year")

    name = build_output_name(cfg, periods[0]["label"], resolve_reflectance_bands(cfg))
    _assert(
        "breaks" in name and "indices" in name,
        "Profiled filename is missing expected breaks tokens",
    )

    cfg_default = BAPParameters(
        spatial_extent=_minimal_spatial_extent(),
        export_profile="breaks",
        years=[2020],
        indices_to_export=[],
    )
    cfg_default = apply_profile_defaults(cfg_default)
    validate_user_inputs(cfg_default)
    validate_profile_compatibility(cfg_default)
    _assert(
        cfg_default.indices_to_export == ["SAVI"],
        "breaks should default to SAVI when no index is provided",
    )


def check_manifest_schema():
    cfg = BAPParameters(
        spatial_extent=_minimal_spatial_extent(),
        export_profile="spectral_recovery",
        years=[2020],
    )
    cfg = apply_profile_defaults(cfg)

    entries = [
        {
            "file": "2020.tif",
            "path": "./2020.tif",
            "period_label": "2020_0401_0630",
            "time_label": "2020-04-01",
            "temporal_extent": ["2020-04-01", "2020-06-30"],
            "export_profile": "spectral_recovery",
            "compositing_mode": "yearly",
            "export_payload": "reflectance",
            "reflectance_bands": ["B02", "B03", "B04", "B08", "B11", "B12"],
            "indices": ["NBR", "NDVI", "SAVI"],
            "spatial_resolution": 10,
        }
    ]

    with tempfile.TemporaryDirectory() as tmp_dir:
        write_manifest(tmp_dir, cfg, entries)
        manifest = Path(tmp_dir) / cfg.manifest_filename
        _assert(manifest.exists(), "Manifest file was not written")

        content = json.loads(manifest.read_text(encoding="utf-8"))
        for key in [
            "schema_version",
            "generated_at",
            "export_profile",
            "compositing_mode",
            "export_payload",
            "entries",
        ]:
            _assert(key in content, f"Manifest missing required key: {key}")

        _assert(content["entries"], "Manifest entries should not be empty")
        first = content["entries"][0]
        for key in [
            "file",
            "period_label",
            "time_label",
            "export_profile",
            "export_payload",
        ]:
            _assert(key in first, f"Manifest entry missing required key: {key}")


def main():
    parser = argparse.ArgumentParser(
        description="Run BAP interoperability smoke checks."
    )
    parser.parse_args()

    check_spectral_recovery_profile()
    check_seasonal_sen_profile()
    check_breaks_profile()
    check_manifest_schema()
    print("BAP interoperability smoke checks passed.")


if __name__ == "__main__":
    main()
