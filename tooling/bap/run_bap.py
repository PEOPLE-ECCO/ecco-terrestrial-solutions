#!/usr/bin/env python3
"""Plain Python CLI wrapper for the BAP workflow."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import types
from datetime import datetime
from pathlib import Path

import openeo
import pystac


def _find_repo_root(script_file: Path) -> Path:
    for candidate in (script_file.parent, *script_file.parents):
        if (candidate / "bap" / "main.py").exists():
            return candidate
    raise FileNotFoundError(
        "Unable to locate repository root containing bap/main.py "
        f"starting from {script_file}"
    )


def _load_algorithm_class(repo_root: Path):
    src_dir = repo_root / "bap"
    main_path = src_dir / "main.py"
    if not main_path.exists():
        raise FileNotFoundError(f"Unable to find algorithm entrypoint: {main_path}")

    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)

    package_name = "_bap_cli"
    package_module = sys.modules.get(package_name)
    if package_module is None:
        package_module = types.ModuleType(package_name)
        package_module.__path__ = [str(src_dir)]
        sys.modules[package_name] = package_module

    module_name = f"{package_name}.main"
    spec = importlib.util.spec_from_file_location(module_name, str(main_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {main_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    if not hasattr(module, "Algorithm"):
        raise AttributeError("Algorithm class not found in bap/main.py")

    return module.Algorithm


def _resolve_secret(value: str | None, env_names: list[str]) -> str | None:
    if value:
        return value
    for env_name in env_names:
        env_value = os.getenv(env_name)
        if env_value:
            return env_value
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the hatfield BAP workflow without CWL."
    )
    parser.add_argument(
        "--parameters",
        required=True,
        help="Path to JSON parameter file (for example: tooling/bap/bap_run_parameters.json).",
    )
    parser.add_argument(
        "--output-dir",
        default="./runs",
        help="Directory to write the run catalog and metadata.",
    )
    parser.add_argument(
        "--run-name",
        default=datetime.utcnow().strftime("run_%Y%m%d_%H%M%S"),
        help="Run folder name under --output-dir.",
    )
    parser.add_argument(
        "--catalog-id",
        default=None,
        help="Optional STAC catalog id. Defaults to run-name.",
    )
    parser.add_argument(
        "--catalog-description",
        default="BAP CLI run",
        help="STAC catalog description.",
    )
    parser.add_argument(
        "--api-url",
        default="https://openeofed.dataspace.copernicus.eu/",
        help="openEO API URL.",
    )
    parser.add_argument(
        "--provider-id",
        default="CDSE",
        help="OIDC provider id.",
    )
    parser.add_argument(
        "--cdse-client-id",
        default=None,
        help="CDSE client id. If omitted, uses env OPENEO_AUTH_CLIENT_ID or CDSE_CLIENT_ID.",
    )
    parser.add_argument(
        "--cdse-client-secret",
        default=None,
        help="CDSE client secret. If omitted, uses env OPENEO_AUTH_CLIENT_SECRET or CDSE_CLIENT_SECRET.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    repo_root = _find_repo_root(Path(__file__).resolve())
    parameters_path = Path(args.parameters).expanduser().resolve()
    if not parameters_path.exists():
        raise FileNotFoundError(f"Parameters file not found: {parameters_path}")

    with open(parameters_path, "r", encoding="utf-8") as f:
        parameters = json.load(f)

    client_id = _resolve_secret(
        args.cdse_client_id,
        ["OPENEO_AUTH_CLIENT_ID", "CDSE_CLIENT_ID"],
    )
    client_secret = _resolve_secret(
        args.cdse_client_secret,
        ["OPENEO_AUTH_CLIENT_SECRET", "CDSE_CLIENT_SECRET"],
    )

    if not client_id or not client_secret:
        raise ValueError(
            "Missing credentials. Provide --cdse-client-id/--cdse-client-secret "
            "or set OPENEO_AUTH_CLIENT_ID and OPENEO_AUTH_CLIENT_SECRET."
        )

    run_dir = Path(args.output_dir).expanduser().resolve() / args.run_name
    run_output_dir = run_dir / "output"
    run_output_dir.mkdir(parents=True, exist_ok=True)

    parameters["output_dir"] = str(run_output_dir)

    Algorithm = _load_algorithm_class(repo_root)

    print(f"Loading parameters from: {parameters_path}")
    print(f"Connecting to openEO API: {args.api_url}")

    conn = openeo.connect(args.api_url).authenticate_oidc_client_credentials(
        client_id=client_id,
        client_secret=client_secret,
        provider_id=args.provider_id,
    )

    catalog = pystac.Catalog(
        id=args.catalog_id or args.run_name,
        description=args.catalog_description,
    )

    print("Running algorithm...")
    Algorithm.run(conn, catalog, parameters)

    catalog.normalize_and_save(
        root_href=str(run_dir),
        catalog_type=pystac.CatalogType.SELF_CONTAINED,
    )

    print(f"Run finished. Catalog saved to: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
