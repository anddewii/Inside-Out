"""Subset GEBCO, flip the underwater relief and publish a release to My Drive."""

from datetime import datetime, timezone
from functools import lru_cache
from io import IOBase
from hashlib import sha256
from pathlib import Path
import json
import math
import os
import shutil
import tempfile
import time
from urllib.request import Request, urlopen
import uuid

import numpy as np
import rasterio
from rasterio.windows import Window, from_bounds

SOURCE = "https://data.source.coop/ausantarctic/gebco/GEBCO_2026.tif"
DOI = "https://doi.org/10.5285/4f68d5c7-45eb-f999-e063-7086abc036fa"
CREDIT = "GEBCO Bathymetric Compilation Group (2026), The GEBCO_2026 Grid"
PROJECT_FOLDER = "InsideOut_GEBCO"
NODATA = -32768


def validate_settings(bounds, web_max_side):
    west, south, east, north = bounds
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("Enter ordered WGS84 bounds (west, south, east, north). Split antimeridian-crossing areas.")
    if min(east - west, north - south) < 0.05 - 1e-10:
        raise ValueError("Each side must span at least 0.05 degrees.")
    if (east - west) * (north - south) > 130:
        raise ValueError("Subset exceeds 130 square degrees. Reduce the area to limit RAM and downloads.")
    if type(web_max_side) is not int or not 64 <= web_max_side <= 360:
        raise ValueError("WEB_MAX_SIDE must be an integer from 64 to 360.")


def fetch_range(start, end, total=None):
    request = Request(SOURCE, headers={
        "Range": f"bytes={start}-{end}", "Accept-Encoding": "identity",
    })
    with urlopen(request, timeout=30) as response:
        prefix = f"bytes {start}-{end}/"
        content_range = response.headers.get("Content-Range", "")
        if response.status != 206 or not content_range.startswith(prefix):
            raise OSError("The GEBCO host did not honor HTTP range requests.")
        size = int(content_range[len(prefix):])
        if end >= size or (total is not None and size != total):
            raise OSError("The remote GEBCO file changed during the transfer.")
        data = response.read(end - start + 2)
        if len(data) != end - start + 1:
            raise OSError("The GEBCO host returned an incomplete range.")
        return data, size


class RangeReader(IOBase):
    """Seekable HTTP file for environments where GDAL's HTTPS transport fails."""

    def __init__(self, size):
        self.size = size
        self.offset = 0
        self.block_size = 262_144
        self.block = lru_cache(maxsize=48)(self._fetch_block)

    def _fetch_block(self, index):
        start = index * self.block_size
        return fetch_range(start, min(start + self.block_size, self.size) - 1, self.size)[0]

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.offset

    def seek(self, offset, whence=0):
        origin = (0, self.offset, self.size)[whence]
        if origin + offset < 0:
            raise OSError("Cannot seek before the start of the raster.")
        self.offset = origin + offset
        return self.offset

    def read(self, size=-1):
        stop = self.size if size < 0 else min(self.offset + size, self.size)
        if stop - self.offset > 64_000_000:
            raise OSError("Refusing an unbounded raster read. Choose a smaller subset.")
        chunks = []
        while self.offset < stop:
            index, offset = divmod(self.offset, self.block_size)
            length = min(stop - self.offset, self.block_size - offset)
            chunks.append(self.block(index)[offset:offset + length])
            self.offset += length
        return b"".join(chunks)

    def close(self):
        self.block.cache_clear()
        super().close()



def raster_write(path, values, transform, description):
    with rasterio.open(
        path, "w", driver="GTiff", width=values.shape[1], height=values.shape[0],
        count=1, dtype="int16", crs="EPSG:4326", transform=transform,
        nodata=NODATA, compress="deflate", predictor=2, tiled=True,
    ) as dest:
        dest.write(values, 1)
        dest.set_band_description(1, description)
        dest.set_band_unit(1, "m")
        dest.update_tags(source=CREDIT, source_doi=DOI, units="m")


def read_window(source, bounds):
    t = source.transform
    if (source.crs is None or source.crs.to_epsg() != 4326
            or t.a <= 0 or t.e >= 0 or t.b != 0 or t.d != 0
            or not math.isclose(t.a, 1 / 240, rel_tol=0.001)
            or not math.isclose(-t.e, 1 / 240, rel_tol=0.001)):
        raise ValueError("Expected a north-up WGS84 GEBCO raster at 15 arc-seconds.")
    window = from_bounds(*bounds, transform=t)
    # Include each native cell that intersects the requested bounds.
    x = max(0, math.floor(window.col_off + 1e-8))
    y = max(0, math.floor(window.row_off + 1e-8))
    right = min(source.width, math.ceil(window.col_off + window.width - 1e-8))
    bottom = min(source.height, math.ceil(window.row_off + window.height - 1e-8))
    if x >= right or y >= bottom:
        raise ValueError("The raster does not cover the requested bounds.")
    if (right - x) * (bottom - y) > 8_000_000:
        raise ValueError("The native window exceeds 8 million cells. Reduce the area.")
    window = Window(x, y, right - x, bottom - y)
    values = source.read(1, window=window, masked=True)
    finite = values.compressed()
    if (not finite.size or not np.isfinite(finite).all()
            or finite.min() < -12000 or finite.max() > 10000
            or not np.equal(finite, np.rint(finite)).all()):
        raise ValueError("The source contains no usable data or unexpected elevation values.")
    return values.astype(np.int16).filled(NODATA), source.window_transform(window)


def read_subset(bounds, cache_dir, local_geotiff=None):
    if local_geotiff:
        with rasterio.open(local_geotiff) as source:
            if not (source.bounds.left <= bounds[0] + 1e-8 and source.bounds.bottom <= bounds[1] + 1e-8
                    and source.bounds.right >= bounds[2] - 1e-8 and source.bounds.top >= bounds[3] - 1e-8):
                raise ValueError("The local GEBCO 2026 GeoTIFF must cover the requested area.")
            return read_window(source, bounds)

    cache_dir.mkdir(parents=True, exist_ok=True)
    key = sha256(repr((SOURCE, bounds)).encode()).hexdigest()[:24]
    cached = cache_dir / f"{key}.tif"
    if cached.exists():
        try:
            with rasterio.open(cached) as source:
                result = read_window(source, bounds)
            print("Reusing the GEBCO subset in this Colab runtime.")
            return result
        except (OSError, ValueError, rasterio.errors.RasterioError):
            cached.unlink(missing_ok=True)

    use_gdal_http = True
    for attempt in range(1, 4):
        try:
            print(f"Reading regional GEBCO tiles (attempt {attempt}/3).")
            _, size = fetch_range(0, 15)
            if use_gdal_http:
                try:
                    with rasterio.Env(
                        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_CONNECTTIMEOUT="15",
                        GDAL_HTTP_TIMEOUT="45", GDAL_HTTP_MAX_RETRY="2", GDAL_HTTP_RETRY_DELAY="1",
                        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
                    ):
                        with rasterio.open(f"/vsicurl/{SOURCE}") as source:
                            values, transform = read_window(source, bounds)
                except rasterio.errors.RasterioError as error:
                    print(f"GDAL HTTPS read failed; switching to Python HTTP ranges: {error}")
                    use_gdal_http = False
            if not use_gdal_http:
                def open_http_grid(path, mode="rb"):
                    if path not in {"test", "gebco.tif"}:
                        raise FileNotFoundError(path)
                    return RangeReader(size)

                with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
                    with rasterio.open("gebco.tif", opener=open_http_grid) as source:
                        values, transform = read_window(source, bounds)
            break
        except (OSError, RuntimeError, rasterio.errors.RasterioError) as error:
            print(f"Source read failed: {error}")
            if attempt == 3:
                raise RuntimeError(
                    "GEBCO could not be read after three attempts. Run again or set "
                    "LOCAL_GEOTIFF to a GEBCO_2026 GeoTIFF covering this area."
                ) from error
            time.sleep(2 ** attempt)

    staged = cached.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        raster_write(staged, values, transform, "GEBCO source elevation")
        os.replace(staged, cached)
    finally:
        staged.unlink(missing_ok=True)
    return values, transform


def build_products(values, transform, bounds, web_max_side, region, run_id, output,
                   source_description):
    ocean = (values < 0) & (values != NODATA)
    if not ocean.any():
        raise ValueError("No underwater cells in this area. Adjust BOUNDS.")
    bathymetry = np.where(ocean, values, NODATA).astype(np.int16)
    inverted = np.where(ocean, -values, NODATA).astype(np.int16)
    raster_write(output / "gebco_2026_subset.tif", values, transform, "GEBCO source elevation")
    raster_write(output / "original_bathymetry_m.tif", bathymetry, transform, "Signed seafloor elevation")
    raster_write(output / "inverted_relief_m.tif", inverted, transform, "Artistic height above zero")

    height, width = values.shape
    row_idx = np.rint(np.linspace(0, height - 1, min(height, web_max_side))).astype(int)
    col_idx = np.rint(np.linspace(0, width - 1, min(width, web_max_side))).astype(int)
    # Send rows south to north. Coordinates are the sampled cell centers.
    row_idx = row_idx[::-1]
    sampled = bathymetry[np.ix_(row_idx, col_idx)]
    valid = (sampled != NODATA).astype(np.int8)
    corners = valid[:-1, :-1] + valid[1:, :-1] + valid[:-1, 1:] + valid[1:, 1:]
    if not (corners == 4).any():
        raise ValueError("The web grid has too few adjacent ocean cells. Raise WEB_MAX_SIDE or choose a more marine area.")
    lon = np.round(transform.c + (col_idx + 0.5) * transform.a, 8).tolist()
    lat = np.round(transform.f + (row_idx + 0.5) * transform.e, 8).tolist()
    grid = [[None if v == NODATA else int(v) for v in row] for row in sampled]
    metadata = {
        "schema_version": 1,
        "run_id": run_id,
        "region": region,
        "dataset": "GEBCO_2026",
        "attribution": CREDIT,
        "source_url": source_description,
        "doi": DOI,
        "license_url": "https://www.gebco.net/data-products/gridded-bathymetry/terms-of-use",
        "requested_bounds_wgs84": list(bounds),
        "raster_bounds_wgs84": [transform.c, transform.f + height * transform.e,
                                transform.c + width * transform.a, transform.f],
        "native_shape": [height, width],
        "web_shape": [len(lat), len(lon)],
        "native_resolution_arc_seconds": 15,
        "native_deepest_m": int(values[ocean].min()),
        "web_deepest_m": int(sampled[sampled != NODATA].min()),
        "nodata_geotiff": NODATA,
        "ocean_mask": "Source elevation < 0 m; land, sea level and NoData are masked.",
        "transformation": "Artistic height (m) = -1 × signed seafloor elevation (m).",
        "web_sampling": "Rounded, evenly spaced native row and column indices.",
        "horizontal_datum": "WGS84 / EPSG:4326",
        "vertical_reference": "GEBCO elevation, generally referenced to mean sea level.",
    }
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    payload = {"metadata": metadata, "longitude": lon, "latitude": lat, "elevation_m": grid}
    (output / "web_grid.json").write_text(
        json.dumps(payload, separators=(",", ":"), allow_nan=False, ensure_ascii=False),
        encoding="utf-8",
    )
    return metadata


def copy_checked(source, destination):
    staged = destination.with_name(f".upload-{uuid.uuid4().hex}.part")
    try:
        shutil.copyfile(source, staged)
        if sha256(source.read_bytes()).digest() != sha256(staged.read_bytes()).digest():
            raise OSError(f"Drive copy verification failed for {destination.name}.")
        os.replace(staged, destination)
    finally:
        staged.unlink(missing_ok=True)


def publish_products(output, drive_root, run_id):
    project = Path(drive_root) / PROJECT_FOLDER
    gis_folder = project / "gis" / run_id
    gis_folder.mkdir(parents=True, exist_ok=False)
    for name in ("gebco_2026_subset.tif", "original_bathymetry_m.tif", "inverted_relief_m.tif", "metadata.json"):
        copy_checked(output / name, gis_folder / name)
    # Publish the small web payload last so incomplete GIS runs are invisible to the app.
    release = project / f"terrain-{run_id}.json"
    copy_checked(output / "web_grid.json", release)
    print(f"Published web data to {release}")
    print(f"Published GIS files to {gis_folder}")
    return release


def run_pipeline(bounds, region, web_max_side, drive_root, local_geotiff=None):
    bounds = tuple(float(value) for value in bounds)
    validate_settings(bounds, web_max_side)
    if not isinstance(region, str) or not 1 <= len(region.strip()) <= 100:
        raise ValueError("REGION_NAME must be a short area name (1–100 characters).")
    if not Path(drive_root).is_dir():
        raise OSError("My Drive is unavailable. Mount Google Drive in Colab first.")
    values, transform = read_subset(bounds, Path(tempfile.gettempdir()) / "insideout-gebco-cache", local_geotiff)
    source_description = "Local GEBCO_2026 GeoTIFF" if local_geotiff else SOURCE
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{now}-{uuid.uuid4().hex[:8]}"
    with tempfile.TemporaryDirectory(prefix="insideout-") as tmp:
        output = Path(tmp)
        metadata = build_products(values, transform, bounds, web_max_side, region.strip(), run_id, output, source_description)
        release = publish_products(output, drive_root, run_id)
    print(f"Web grid: {metadata['web_shape']} | Deepest native cell: {metadata['native_deepest_m']:,} m")
    return release
