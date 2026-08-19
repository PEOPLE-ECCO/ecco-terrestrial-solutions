import os
from pathlib import Path
from typing import Dict

from openeo.rest.connection import Connection
from pystac import Catalog

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
            # Set some sane default parameters
            if (parameters.get("rangestart") and parameters.get("rangeend")):
                TEMP_EXTENT = (parameters.get("rangestart"), parameters.get("rangeend"))
            else:
                TEMP_EXTENT = ("2023-01-01", "2023-05-31")
            SPATIAL_EXTENT = parameters.get("spatial_   extent") or {
                "west": 118.54074,
                "south": 4.31173,
                "east": 118.78351,
                "north": 4.54025,
                "crs": "EPSG:4326",
            }

            # Prepare Parameters
            params = Parameters(BANDS, TEMP_EXTENT, SPATIAL_EXTENT)
            print(f"Running with parameters {params}")

            # Run BAP to generate input images
            source_dir = Path(__file__).resolve().parent
            print(f"Triggering download")

            # Run algorithm
            download(conn, source_dir, params)
            print(f"Triggering prediction")
            predict(Path(source_dir), catalog, chunk_size=parameters.get("chunk_size") or 512)

        except Exception as e:
            # TODO: improve error logging
            print(e)
