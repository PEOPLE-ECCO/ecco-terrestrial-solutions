# Create SAVI growing season composites from S2 cube from seasonal sen

import numpy as np
import dask.array as da
import rioxarray
import xarray as xr
from .breaks_detection import callbreak

## Cube definitions

CUBENAME = 'subdomain_results'  # the xr.dataset variable naming the xr.dataarray
BANDS = ['B08', 'B04', 'SCL']

## Functions

def mask_by_scl(arr, arr_scl, scl_to_mask):
    arr2 = arr.copy()
    arr2.data = da.where(
        da.isin(arr_scl.data, scl_to_mask),
        np.nan,
        arr.data,
    )
    return arr2

## Prepare SAVI

def savi_growing_season_composites(path, growing_months=(9, 10, 11)):
    """Build yearly growing-season SAVI composites from a multi-temporal S2 cube.

    Not needed for BAP inputs: BAP already delivers masked yearly composites.
    """
    S2 = rioxarray.open_rasterio(path)
    #S2 = xr.open_zarr('S2_samplecube.zarr')[CUBENAME]

    ## pre-corrections and SCL masking
    # 0s -> nan
    stack = S2.where(lambda x: x > 0, other=np.nan)  #  sentinel-2 uses 0 as nodata

    # correct baseline values after Jan 25th, 2022
    chg_date = np.datetime64('2022-01-25')
    if stack.time.max().values > chg_date:
        stack_corrected = xr.where(stack.time > chg_date, stack - 1000, stack)
        stack.data = stack_corrected.data

    # get scene masking info
    scl = S2.sel(band=BANDS[-1])
    scl = scl.expand_dims('band').transpose(*S2.dims).astype(np.int8)
    cube_masked = mask_by_scl(stack, scl, [3, 8, 9, 10])

    # Calculate masked SAVI

    b8, b4 = [cube_masked.sel(band=b) for b in BANDS[:2]]

    # https://www.indexdatabase.de/db/i-single.php?id=87
    L = 0.5
    index = ((b8 - b4) / (b8 + b4 + L) * (1 + L)).astype(np.float32)
    index.name = 'SAVI'

    ## Create growing season composites
    growing_months_savi = index.sel(time=index.time.dt.month.isin(list(growing_months)))
    return growing_months_savi.groupby("time.year").median(dim="time").chunk({'year': -1, 'y': 256, 'x': 256})

## Compute

METRICS = ['ChgYr', 'ChgMag']
BAND_NAMES = ['year', 'magnitudeX1000']
NODATA = -1e6


def detect_breaks(composites, minyear, maxyear, crs, threshold=0.025, scale=1000):
    """Run the breaks detection over a (year, y, x) stack of index composites.

    Returns a (metric, y, x) DataArray with CRS and nodata set, ready to write.
    """
    # Apply breaks detection function and enforce the desired shape/order
    result = xr.apply_ufunc(
        lambda e: callbreak(e, minyear=minyear, maxyear=maxyear, threshold=threshold)[METRICS].values,
        composites * scale,
        input_core_dims=[["year"]], # Apply function along 'year' dimension
        output_core_dims=[["metric"]], # Name the new dimension 'metric'
        vectorize=True,
        dask="parallelized",
        output_dtypes=[np.float64],
        dask_gufunc_kwargs={"output_sizes": {"metric": len(METRICS)}}, # Ensure metric has exactly 2 slices
    )

    # Transpose to (metric, y, x) order
    result = result.transpose("metric", "y", "x")

    final = result.persist()

    ## Prepare final output
    # name the coordinates
    final = final.assign_coords(metric=BAND_NAMES).chunk({'metric': -1, 'y': 512, 'x': 512})

    # name the output bands
    final.attrs['long_name'] = tuple(final.metric.values.tolist())

    # set CRS and nodata
    final = final.rio.write_crs(crs).fillna(NODATA).rio.write_nodata(NODATA)

    return final


if __name__ == '__main__':
    growing_season_composites = savi_growing_season_composites('your_file.tif')
    final = detect_breaks(growing_season_composites, minyear=2020, maxyear=2024, crs=32633)

    ## Write output files

    final.rio.to_raster('AP_site1_breaks.tif', compress='deflate', predictor='3')
    final.where(final[1] <= -200).rio.to_raster('AP_site1_breaks_lemi200.tif', compress='deflate', predictor='3')
