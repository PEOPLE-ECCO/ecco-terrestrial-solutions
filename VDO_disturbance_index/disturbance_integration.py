import numpy as np
import geopandas as gpd
from rasterstats import zonal_stats

try:
    import rasterio
    from rasterio.features import sieve
except ImportError:  # pragma: no cover - exercised only when rasterio is absent
    rasterio = None
    sieve = None

try:
    from scipy.ndimage import generic_filter
except ImportError:  # pragma: no cover - exercised only when SciPy is absent
    generic_filter = None

try:
    import cv2
except ImportError:  # pragma: no cover - exercised only when OpenCV is absent
    cv2 = None

try:
    from numba import njit
except ImportError:  # pragma: no cover - exercised only when numba is absent
    njit = None


def _coerce_single_band_array(raster):
    """Normalize a single-band raster-like input to a 2D NumPy array."""
    if isinstance(raster, np.ndarray):
        array = np.asarray(raster)
        if array.ndim == 3 and array.shape[0] == 1:
            array = array[0]
        if array.ndim != 2:
            raise ValueError("raster must be a 2D array or a single-band raster")
        return array

    try:
        import xarray as xr
    except ImportError:  # pragma: no cover - exercised only when xarray is absent
        xr = None

    if xr is not None and isinstance(raster, xr.DataArray):
        array = np.asarray(raster)
        if array.ndim == 3 and array.shape[0] == 1:
            array = array[0]
        if array.ndim != 2:
            raise ValueError("raster must be a 2D array or a single-band raster")
        return array

    if rasterio is not None:
        if isinstance(raster, (str, bytes)):
            with rasterio.open(raster) as src:
                return np.asarray(src.read(1))
        if hasattr(raster, "read"):
            return np.asarray(raster.read(1))

    raise TypeError(
        "raster must be a 2D numpy array, an xarray DataArray, a rasterio dataset, or a raster path"
    )


def majority_filter(raster, filter_size, use_numba=False, use_opencv=False):
    """Apply a majority/mode filter to a single-band raster.

    Parameters
    ----------
    raster : numpy.ndarray, rasterio.io.DatasetReader, or str
        A single-band raster represented as a 2D array or a raster source.
    filter_size : int
        Odd filter side size in pixels.
    use_numba : bool, default False
        If True and numba is available, use a JIT-compiled implementation.
    use_opencv : bool, default False
        If True and OpenCV is available, use an OpenCV-backed implementation
        that is especially effective for raster values encoded as a small set
        of integer codes (for example, 0 plus a handful of years).

    Returns
    -------
    numpy.ndarray
        A 2D array with the majority-filtered values.
    """
    if not isinstance(filter_size, (int, np.integer)):
        raise TypeError("filter_size must be an integer")

    if filter_size <= 0 or filter_size % 2 == 0:
        raise ValueError("filter_size must be a positive odd integer")

    try:
        import xarray as xr
    except ImportError:  # pragma: no cover - exercised only when xarray is absent
        xr = None

    source_is_xarray = xr is not None and isinstance(raster, xr.DataArray)
    image = _coerce_single_band_array(raster)
    if image.size == 0:
        result = image.copy()
        if source_is_xarray:
            return xr.DataArray(result, dims=raster.dims, coords={dim: raster.coords[dim] for dim in raster.dims if dim in raster.coords}, attrs=raster.attrs, name=getattr(raster, "name", None))
        return result

    if filter_size == 1:
        result = image.copy()
        if source_is_xarray:
            return xr.DataArray(result, dims=raster.dims, coords={dim: raster.coords[dim] for dim in raster.dims if dim in raster.coords}, attrs=raster.attrs, name=getattr(raster, "name", None))
        return result

    if use_opencv and cv2 is not None and np.issubdtype(image.dtype, np.integer):
        unique_values = np.unique(image)
        if unique_values.size <= 256:
            encoded = np.empty_like(image, dtype=np.uint8)
            for idx, value in enumerate(unique_values):
                encoded[image == value] = idx

            kernel = np.ones((filter_size, filter_size), dtype=np.float32)
            class_counts = []
            for idx in range(unique_values.size):
                class_map = (encoded == idx).astype(np.float32)
                class_counts.append(cv2.filter2D(class_map, -1, kernel))

            counts = np.stack(class_counts, axis=0)
            class_idx = np.argmax(counts, axis=0).astype(np.uint8)
            result = unique_values[class_idx]
            if source_is_xarray:
                return xr.DataArray(result, dims=raster.dims, coords={dim: raster.coords[dim] for dim in raster.dims if dim in raster.coords}, attrs=raster.attrs, name=getattr(raster, "name", None))
            return result

    if use_numba and njit is not None and image.dtype.kind in {"i", "u", "b"}:
        @njit(cache=True)
        def _mode_window_numba(values):
            size = values.size
            if size == 0:
                return np.int64(0)

            # Fast path: small positive integer range -> use a local histogram.
            min_val = values[0]
            max_val = values[0]
            for idx in range(1, size):
                value = values[idx]
                if value < min_val:
                    min_val = value
                elif value > max_val:
                    max_val = value
            value_range = max_val - min_val

            if value_range >= 0 and value_range < 1024:
                counts = np.zeros(value_range + 1, dtype=np.int32)
                for idx in range(size):
                    counts[int(values[idx] - min_val)] += 1
                best_value = min_val
                best_count = -1
                for idx in range(value_range + 1):
                    count = counts[idx]
                    if count > best_count:
                        best_count = count
                        best_value = min_val + idx
                return best_value

            # Fallback for wide integer ranges: sort and count runs.
            sorted_vals = np.sort(values)
            best_value = sorted_vals[0]
            best_count = 1
            current_value = sorted_vals[0]
            current_count = 1
            for idx in range(1, size):
                value = sorted_vals[idx]
                if value == current_value:
                    current_count += 1
                else:
                    if current_count > best_count:
                        best_count = current_count
                        best_value = current_value
                    current_value = value
                    current_count = 1
            if current_count > best_count:
                best_value = current_value
            return best_value

        radius = filter_size // 2
        padded = np.pad(image, pad_width=((radius, radius), (radius, radius)), mode="edge")
        result = np.empty_like(image, dtype=image.dtype)
        for row in range(image.shape[0]):
            for col in range(image.shape[1]):
                window = padded[row : row + filter_size, col : col + filter_size]
                result[row, col] = _mode_window_numba(window.ravel())
        if source_is_xarray:
            return xr.DataArray(result, dims=raster.dims, coords={dim: raster.coords[dim] for dim in raster.dims if dim in raster.coords}, attrs=raster.attrs, name=getattr(raster, "name", None))
        return result

    def _mode_window(values):
        if np.issubdtype(values.dtype, np.floating):
            values = values[~np.isnan(values)]
        if values.size == 0:
            return np.nan
        unique, counts = np.unique(values, return_counts=True)
        return unique[np.argmax(counts)]

    if generic_filter is not None:
        result = generic_filter(image, _mode_window, size=filter_size, mode="nearest")
        if source_is_xarray:
            return xr.DataArray(result, dims=raster.dims, coords={dim: raster.coords[dim] for dim in raster.dims if dim in raster.coords}, attrs=raster.attrs, name=getattr(raster, "name", None))
        return result

    radius = filter_size // 2
    padded = np.pad(image, pad_width=((radius, radius), (radius, radius)), mode="nearest")
    result = np.empty_like(image, dtype=image.dtype)

    for row in range(image.shape[0]):
        for col in range(image.shape[1]):
            window = padded[row : row + filter_size, col : col + filter_size]
            result[row, col] = _mode_window(window.ravel())

    if source_is_xarray:
        return xr.DataArray(result, dims=raster.dims, coords={dim: raster.coords[dim] for dim in raster.dims if dim in raster.coords}, attrs=raster.attrs, name=getattr(raster, "name", None))
    return result


def minimum_mapping_unit_filter(raster, mmu_area, connectivity=8):
    """Remove connected raster regions smaller than an area-based MMU.

    The input must have a CRS and affine transform. The MMU is expressed in
    squared units of the raster CRS. All pixel values, including 0, are
    treated as valid classes and may be used as the replacement class for a
    small neighboring region.

    Parameters
    ----------
    raster : xarray.DataArray, rasterio.io.DatasetReader, or str
        A georeferenced single-band raster. Raster paths and rasterio
        datasets are returned as a georeferenced xarray.DataArray.
    mmu_area : float
        Minimum mapping unit in squared CRS units.
    connectivity : {4, 8}, default 8
        Pixel connectivity used to define contiguous regions.

    Returns
    -------
    xarray.DataArray
        The MMU-filtered raster with georeferencing preserved.
    """
    if sieve is None:
        raise ImportError("minimum_mapping_unit_filter requires rasterio")

    if isinstance(mmu_area, (bool, np.bool_)) or not isinstance(
        mmu_area, (int, float, np.integer, np.floating)
    ):
        raise TypeError("mmu_area must be a positive number")
    if not np.isfinite(mmu_area) or mmu_area <= 0:
        raise ValueError("mmu_area must be a positive finite number")

    if connectivity not in (4, 8):
        raise ValueError("connectivity must be either 4 or 8")

    try:
        import xarray as xr
    except ImportError as exc:  # pragma: no cover - xarray is required here
        raise ImportError(
            "minimum_mapping_unit_filter requires xarray"
        ) from exc

    source_is_xarray = isinstance(raster, xr.DataArray)
    source_transform = None
    source_crs = None

    if source_is_xarray:
        if raster.ndim != 2:
            raise ValueError("raster must be a 2D single-band DataArray")
        try:
            source_crs = raster.rio.crs
            source_transform = raster.rio.transform()
        except (AttributeError, RuntimeError, ValueError) as exc:
            raise ValueError(
                "raster must have CRS and affine transform georeferencing"
            ) from exc
        image = np.asarray(raster)
    elif isinstance(raster, (str, bytes)):
        with rasterio.open(raster) as source:
            image = np.asarray(source.read(1))
            source_crs = source.crs
            source_transform = source.transform
    elif hasattr(raster, "read"):
        image = np.asarray(raster.read(1))
        source_crs = getattr(raster, "crs", None)
        source_transform = getattr(raster, "transform", None)
    else:
        raise TypeError(
            "raster must be a georeferenced xarray DataArray, rasterio dataset, or raster path"
        )

    if source_crs is None or source_transform is None:
        raise ValueError("raster must have CRS and affine transform georeferencing")
    if getattr(source_transform, "is_identity", False):
        raise ValueError("raster must have a non-identity affine transform")

    pixel_area = abs(
        source_transform.a * source_transform.e
        - source_transform.b * source_transform.d
    )
    if not np.isfinite(pixel_area) or pixel_area <= 0:
        raise ValueError("raster must have a valid non-zero pixel area")

    mmu_pixels = max(1, int(np.ceil(mmu_area / pixel_area)))
    filtered = sieve(
        image,
        size=mmu_pixels,
        connectivity=connectivity,
    )

    if source_is_xarray:
        return raster.copy(data=filtered)

    try:
        import rioxarray
    except ImportError as exc:  # pragma: no cover - required for non-xarray input
        raise ImportError(
            "rioxarray is required when raster is a path or rasterio dataset"
        ) from exc

    result = xr.DataArray(filtered, dims=("y", "x"))
    return result.rio.write_crs(source_crs).rio.write_transform(source_transform)


def add_zonal_statistic(zones, raster, statistic, output_field_label):
    """Add a zonal statistic field to a zones GeoDataFrame.

    Parameters
    ----------
    zones : geopandas.GeoDataFrame
        GeoDataFrame containing the zone polygons or lines to summarize.
    raster : xarray.DataArray, str, or rasterio.io.DatasetReader
        A georeferenced xarray DataArray, raster file path, or dataset object
        readable by rasterstats.
    statistic : {'count', 'sum'}
        Statistic to calculate for each zone.
    output_field_label : str
        Base field name. The returned field will be named
        ``<output_field_label>_<statistic>``.

    Returns
    -------
    geopandas.GeoDataFrame
        A copy of the input zones with the new statistic field appended.
    """
    if not isinstance(zones, gpd.GeoDataFrame):
        raise TypeError("zones must be a geopandas GeoDataFrame")

    if not isinstance(output_field_label, str) or not output_field_label.strip():
        raise ValueError("output_field_label must be a non-empty string")

    statistic = statistic.lower()
    if statistic not in {"count", "sum"}:
        raise ValueError("statistic must be either 'count' or 'sum'")

    # make sure crs matches
    raster = raster.rio.reproject(zones.crs) if hasattr(raster, "rio") else raster

    raster_source = raster
    zonal_kwargs = {}
    try:
        import xarray as xr
    except ImportError:  # pragma: no cover - xarray is optional here
        xr = None

    if xr is not None and isinstance(raster, xr.DataArray):
        try:
            affine = raster.rio.transform()
        except (AttributeError, RuntimeError, ValueError) as exc:
            raise ValueError(
                "xarray raster must have an affine transform georeferencing"
            ) from exc
        raster_source = _coerce_single_band_array(raster)
        if np.issubdtype(raster_source.dtype, np.integer):
            raster_source = raster_source.astype(np.float64)
        zonal_kwargs["affine"] = affine

        nodata_value = getattr(raster.rio, "nodata", None)
        if nodata_value is None and np.issubdtype(raster_source.dtype, np.floating):
            if np.isnan(raster_source).any():
                nodata_value = np.nan
        if nodata_value is not None:
            zonal_kwargs["nodata"] = nodata_value

    results = zonal_stats(
        zones,
        raster_source,
        stats=[statistic],
        all_touched=False,
        **zonal_kwargs,
    )

    values = []
    for result in results:
        value = result.get(statistic)
        values.append(np.nan if value is None else value)

    out_field = f"{output_field_label}_{statistic}"
    zones_with_stat = zones.copy()
    zones_with_stat[out_field] = values

    return zones_with_stat

"""
Disturbance index: normalization + weighting, as a plain NumPy function.
 
Method (matches disturbance_index_methodology.md):
  1. log1p-transform each column (compresses right-skew; log1p(0) = 0).
  2. Scale by a per-column "cap" (default: the column's 99th percentile in
     log-space) and clip to 1.0, so a column value of 0 always maps to 0,
     and only the top ~1% of extreme values get clipped to the ceiling.
  3. Combine columns with weights rescaled to sum to 1.
"""


def count_points_within_zones(zones, points, count_column):
    """
    Count points within each zone polygon.

    Parameters
    ----------
    zones : geopandas.GeoDataFrame
        Polygon GeoDataFrame representing the zones.
    points : geopandas.GeoDataFrame
        Point GeoDataFrame to count.
    count_column : str
        Name of the column to add to the returned zones GeoDataFrame.

    Returns
    -------
    geopandas.GeoDataFrame
        A copy of zones with count_column populated with point counts.
    """
    if zones.crs is None or points.crs is None:
        raise ValueError("Both zones and points must have a CRS.")

    zones_out = zones.copy()
    points = points.to_crs(zones_out.crs)

    if points.geometry.duplicated().any():
        raise ValueError(
            "points contains duplicate geometries; remove duplicates before calling count_points_within_zones"
        )

    # Use a temporary positional ID so the function works with any zone index
    zone_id = "_temporary_zone_id"
    while zone_id in zones_out.columns:
        zone_id = "_" + zone_id

    zones_work = zones_out[[zones_out.geometry.name]].copy()
    zones_work[zone_id] = range(len(zones_work))

    # Join each point to the zone containing it
    joined = gpd.sjoin(
        points[[points.geometry.name]],
        zones_work[[zone_id, zones_work.geometry.name]],
        how="inner",
        predicate="within",
    )

    # Count matches for each zone
    counts = joined.groupby(zone_id).size()

    # Populate the output column, retaining zones with zero points
    zones_out[count_column] = (
        zones_work[zone_id]
        .map(counts)
        .fillna(0)
        .astype(int)
        .to_numpy()
    )

    return zones_out

 
def compute_disturbance_index(
    table,
    factor_weights=None,
    cap_percentile=99,
    caps=None,
    return_components=False,
):
    """
    Compute a 0-1 disturbance index from a numeric table.
 
    Parameters
    ----------
    table : pandas.DataFrame or geopandas.GeoDataFrame
        A labeled tabular object whose columns correspond to the factor names
        in `factor_weights`. Values must be >= 0 (counts/sums), since log1p
        is used.
    factor_weights : mapping, optional
        Explicit mapping of factor column names to weights, for example
        ``{"fire_count": 0.3, "disturbance_count": 0.7, "built_sum": 0.9}```.
        If omitted, the default mapping is
        ``{"disturbance_count": 0.3, "fire_count": 0.7, "built_sum": 0.9}``.
    cap_percentile : float, default 99
        Percentile (0-100), computed in log1p-space, used as each column's
        scaling ceiling. Values at or above the cap normalize to 1.0.
        Only used when `caps` is not supplied.
    caps : array-like of float, length n_factors, optional
        Precomputed log1p-space ceilings to use instead of computing them
        from `table`. Pass this to score new/incoming data on the *same*
        scale as a reference dataset (e.g. the caps returned from an
        earlier call on your full historical table) rather than recomputing
        percentiles from a small or biased subset.
    return_components : bool, default False
        If True, also return the per-column normalized values and the caps
        that were used (handy for auditing or for reuse via `caps=`).
 
    Returns
    -------
    index : np.ndarray, shape (n_rows,)
        Disturbance index in [0, 1] (0 = undisturbed on all factors).
    normalized : np.ndarray, shape (n_rows, n_factors)   [only if return_components]
        Per-column normalized values in [0, 1].
    caps_used : np.ndarray, shape (n_factors,)            [only if return_components]
        The log1p-space caps actually used (either computed here, or the
        `caps` you passed in) -- save these to reuse on future data.
 
    Examples
    --------
>>> import numpy as np
>>> X = np.array([[0, 0, 0], [12, 626, 61], [21, 1813, 103117]])
>>> compute_disturbance_index(X)
    array([0., 0.62018375, 1.])
 
    >>> # Score new data on the same scale as a reference table:
>>> idx_ref, _, caps = compute_disturbance_index(X_reference, return_components=True)
>>> idx_new = compute_disturbance_index(X_new, caps=caps)
    """
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - pandas is required here
        raise ImportError("`table` must be a pandas.DataFrame or geopandas.GeoDataFrame") from exc

    if not isinstance(table, pd.DataFrame):
        raise TypeError("`table` must be a pandas.DataFrame or geopandas.GeoDataFrame")

    X = table.to_numpy(dtype=float)
    if X.ndim != 2:
        raise ValueError(f"`table` must be 2-D (n_rows, n_factors); got shape {X.shape}")

    if factor_weights is None:
        factor_weights = {
            "disturbance_count": 0.7,
            "fire_count": 0.3,
            "built_sum": 0.9,
        }
    elif isinstance(factor_weights, dict):
        factor_weights = dict(factor_weights)
    else:
        raise TypeError(
            "`factor_weights` must be a mapping of column names to weights"
        )

    columns = list(table.columns)
    if len(columns) != X.shape[1]:
        raise ValueError(
            f"`table` has {len(columns)} columns but its values have {X.shape[1]} columns"
        )

    missing_columns = [name for name in factor_weights if name not in columns]
    if missing_columns:
        raise ValueError(
            "`factor_weights` contains columns not found in `table`: "
            f"{missing_columns}"
        )

    n_factors = X.shape[1]
    if len(factor_weights) != n_factors:
        raise ValueError(
            f"`factor_weights` has {len(factor_weights)} entries but `table` has {n_factors} columns"
        )

    weights = np.asarray([factor_weights[name] for name in columns], dtype=float)
    if np.any(weights < 0):
        raise ValueError("all factor weights must be non-negative")
    if np.isclose(weights.sum(), 0):
        raise ValueError("factor weights must sum to a positive value")

    if np.any(X < 0):
        raise ValueError("`table` must be non-negative (counts/sums) to use log1p")

    log_X = np.log1p(X)

    if caps is None:
        caps_used = np.percentile(log_X, cap_percentile, axis=0)
    else:
        caps_used = np.asarray(caps, dtype=float)
        if caps_used.shape[0] != n_factors:
            raise ValueError(
                f"`caps` has {caps_used.shape[0]} entries but `table` has {n_factors} columns"
            )

    # Avoid divide-by-zero if a column is constant at 0.
    safe_caps = np.where(caps_used == 0, 1.0, caps_used)
    normalized = np.clip(log_X / safe_caps, 0.0, 1.0)

    norm_weights = weights / weights.sum()
    index = normalized @ norm_weights

    if return_components:
        return index, normalized, caps_used
    return index
