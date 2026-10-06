"""Lazy normalization and persistent, source-versioned common-grid cache."""
import glob
import hashlib
import json
import threading
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from app.core.settings import ROOT, load_config

SCHEMA_VERSION = 4
LOCK = threading.RLock()
VARIABLES = ("precipitation", "rain_24h", "precip", "tp", "rain", "APCP", "apcp")


def resolve_source(spec):
    pattern = Path(spec["path"])
    pattern = str(pattern if pattern.is_absolute() else ROOT / pattern)
    matches = sorted(glob.glob(pattern))
    if not matches:
        raise ValueError(f"File NetCDF tidak ditemukan: {pattern}")
    return Path(matches[-1]).resolve()


def convert_mm(data, units, hours):
    """Convert amount/rate to millimetres for the actual interval duration."""
    unit = units.lower().replace(" ", "").replace("⁻", "-").replace("²", "2").replace("¹", "1")
    unit = unit.replace("**", "^")
    if unit in ("mm", "kgm^-2", "kgm-2", "kg/m2"):
        factor = 1
    elif unit in ("m", "metre", "meter"):
        factor = 1000
    elif unit in ("mm/h", "mm/hr", "mm/hour", "mmh-1", "mmh^-1", "mm/jam"):
        factor = hours
    elif unit in ("mm/day", "mmd-1", "mmd^-1", "mm/hari"):
        factor = hours / 24
    elif unit in ("kgm^-2s^-1", "kgm-2s-1", "kg/m2/s", "mm/s"):
        factor = hours * 3600
    else:
        raise ValueError(f"Satuan hujan ambigu/tidak didukung: {units!r}; isi units di konfigurasi.")
    return data * factor


def normalize(ds, spec):
    variable = spec.get("variable") or next((v for v in VARIABLES if v in ds), None)
    if variable not in ds:
        raise ValueError(f"Variabel hujan tidak ditemukan: {variable}; tersedia {list(ds.data_vars)}")
    rain = ds[variable]
    rename = {}
    for target, options in (("lat", ("latitude", "lat")), ("lon", ("longitude", "lon"))):
        source = next((v for v in options if v in rain.coords), None)
        if source is None or rain[source].ndim != 1:
            raise ValueError("Hanya grid latitude/longitude reguler 1-D yang didukung.")
        if source != target:
            rename[source] = target
    rain = rain.rename(rename)
    time_label = spec.get("time_label", "end")
    if time_label not in ("start", "end"):
        raise ValueError("time_label harus start atau end")
    if "step" in rain.dims:
        if time_label != "end":
            raise ValueError("Data step/valid_time memakai time_label: end")
        if "valid_time" not in ds:
            raise ValueError("Data forecast membutuhkan valid_time.")
        times = ds.valid_time.values.astype("datetime64[ns]")
        date_labels = times.astype("datetime64[D]").astype("datetime64[ns]")
        starts = (ds.accumulation_start.values.astype("datetime64[ns]")
                  if "accumulation_start" in ds else None)
        initialization = pd.Timestamp(np.asarray(ds.time.values).astype("datetime64[ns]").reshape(-1)[0])
        rain = rain.drop_vars([v for v in rain.coords if v not in ("step", "lat", "lon")])
        rain = rain.rename(step="time").assign_coords(time=times)
    elif "time" in rain.dims:
        times = rain.time.values.astype("datetime64[ns]")
        date_labels = times.astype("datetime64[D]").astype("datetime64[ns]")
        initialization = None
        starts = (ds.accumulation_start.values.astype("datetime64[ns]")
                  if "accumulation_start" in ds else None)
        if time_label == "start":
            duration_hours = spec.get("period_hours")
            if duration_hours is None and len(times) > 1 and np.all(np.diff(times) == np.diff(times)[0]):
                duration_hours = float(np.diff(times)[0] / np.timedelta64(1, "h"))
            if duration_hours is None or not np.isfinite(duration_hours) or duration_hours <= 0:
                raise ValueError("time_label: start membutuhkan durasi period_hours yang valid")
            if starts is not None and not np.array_equal(starts, times):
                raise ValueError("time_label: start tidak cocok dengan accumulation_start")
            starts = times.copy()
            times = times + np.timedelta64(int(float(duration_hours) * 3600), "s")
            rain = rain.assign_coords(time=times)
    else:
        raise ValueError("Dimensi waktu tidak ditemukan; dukungan time atau step diperlukan.")
    extra = [d for d in rain.dims if d not in ("time", "lat", "lon")]
    if any(rain.sizes[d] != 1 for d in extra):
        raise ValueError(f"Dimensi tambahan perlu dipilih manual: {extra}")
    rain = rain.squeeze(extra, drop=True).transpose("time", "lat", "lon")
    if starts is None:
        hours = spec.get("period_hours")
        if spec.get("cumulative") and initialization is not None:
            starts = np.concatenate(([initialization.to_datetime64()], times[:-1]))
        elif hours:
            starts = times - np.timedelta64(int(float(hours) * 3600), "s")
        elif len(times) > 1 and np.all(np.diff(times) == np.diff(times)[0]):
            starts = times - np.diff(times)[0]
        else:
            raise ValueError("Periode akumulasi ambigu; isi period_hours di konfigurasi.")
    hours = (times - starts) / np.timedelta64(1, "h")
    if (hours <= 0).any() or (np.diff(times) <= np.timedelta64(0, "s")).any():
        raise ValueError("Waktu harus meningkat dan durasi akumulasi harus positif.")
    if len(times) > 1 and not np.array_equal(starts[1:], times[:-1]):
        raise ValueError("Periode akumulasi harus kontinu, tidak tumpang tindih atau berlubang.")
    if spec.get("cumulative"):
        rain = xr.concat([rain.isel(time=[0]), rain.diff("time")], dim="time")
    units = spec.get("units") or ds[variable].attrs.get("units", "")
    duration = xr.DataArray(hours, dims="time", coords={"time": times})
    rain = convert_mm(rain, units, duration).astype("float32")
    minimum = float(rain.min().compute())
    if minimum < -0.1:
        raise ValueError(f"Hujan negatif signifikan: {minimum:.4f} mm")
    corrected = int(((rain < 0) & np.isfinite(rain)).sum().compute())
    rain = rain.clip(min=0)
    rain = rain.assign_coords(lon=((rain.lon + 180) % 360) - 180).sortby(["lat", "lon"])
    for coord in ("lat", "lon"):
        values = rain[coord].values
        if len(values) < 2 or not np.allclose(np.diff(values), np.diff(values)[0]):
            raise ValueError(f"Grid {coord} harus reguler dan memiliki minimal dua sel.")
    rain = rain.assign_coords(accumulation_start=("time", starts), period_hours=("time", hours),
                              comparison_date=("time", date_labels))
    rain.attrs = {"units": "mm", "original_units": units, "original_variable": variable,
                  "negative_values_clipped": corrected,
                  "initialization_utc": str(initialization) if initialization is not None else "",
                  "time_label": time_label}
    return rain


def align_time(a, b, mode="exact"):
    """Exact physical intervals, or explicitly selected UTC source-date labels.

    Date mode uses synthetic calendar intervals only as shared indexing keys.
    Original intervals remain separate coordinates and are never shifted.
    """
    if mode not in ("exact", "date"):
        raise ValueError("time_alignment harus exact atau date")
    if mode == "date":
        for key, rain in (("A", a), ("B", b)):
            if not np.allclose(rain.period_hours, 24):
                raise ValueError("time_alignment: date hanya untuk akumulasi 24 jam")
            if len(np.unique(rain.comparison_date)) != rain.time.size:
                raise ValueError(f"Produk {key} memiliki beberapa interval pada tanggal sama; gunakan exact")
        dates = np.intersect1d(a.comparison_date.values, b.comparison_date.values)
        if not dates.size:
            raise ValueError("Tidak ada label tanggal bersama untuk time_alignment: date")
        if len(dates) > 1 and not np.all(np.diff(dates) == np.timedelta64(1, "D")):
            raise ValueError("Label tanggal bersama tidak kontinu")
        aligned = []
        for key, rain in (("A", a), ("B", b)):
            indices = pd.Index(rain.comparison_date.values).get_indexer(dates)
            rain = rain.isel(time=indices)
            original_end = rain.time.values.copy()
            rain = rain.rename({"accumulation_start": f"accumulation_start_{key}"})
            rain = rain.assign_coords(
                time=dates + np.timedelta64(24, "h"),
                accumulation_start=("time", dates),
                **{f"accumulation_end_{key}": ("time", original_end)},
            )
            aligned.append(rain)
        return tuple(aligned)
    def describe(rain):
        return f"{pd.Timestamp(rain.time.values[0])} ... {pd.Timestamp(rain.time.values[-1])} UTC"
    source_times = f"A: {describe(a)}; B: {describe(b)}"
    a, b = xr.align(a, b, join="inner")
    if not a.time.size:
        raise ValueError("Tidak ada waktu forecast bersama pada mode exact. "
                         "Timestamp akhir periode harus identik; gunakan time_alignment: date "
                         f"untuk membandingkan label tanggal dengan jendela berbeda. {source_times}")
    if not np.array_equal(a.accumulation_start, b.accumulation_start):
        raise ValueError("Batas periode akumulasi kedua produk berbeda pada mode exact. "
                         "Gunakan data berjendela identik atau time_alignment: date secara eksplisit.")
    if a.time.size > 1 and not np.array_equal(a.accumulation_start.values[1:], a.time.values[:-1]):
        raise ValueError("Periode bersama pada mode exact tidak kontinu")
    # A source's start/end label may differ even when its physical interval matches.
    shared_dates = a.time.values.astype("datetime64[D]").astype("datetime64[ns]")
    return tuple(r.assign_coords(comparison_date=("time", shared_dates),
                                 **{f"accumulation_start_{key}": ("time", r.accumulation_start.values),
                                    f"accumulation_end_{key}": ("time", r.time.values)})
                 for key, r in (("A", a), ("B", b)))


def regrid_pair(a, b, choice="coarser", time_alignment="exact"):
    """Bilinear common grid within overlap; no extrapolation."""
    if choice not in ("coarser", "A", "B"):
        raise ValueError("grid harus coarser, A atau B")
    resolutions = [abs(float(r.lat[1] - r.lat[0]) * float(r.lon[1] - r.lon[0])) for r in (a, b)]
    target = a if choice == "A" or (choice == "coarser" and resolutions[0] >= resolutions[1]) else b
    lat = target.lat.sel(lat=slice(max(float(a.lat.min()), float(b.lat.min())), min(float(a.lat.max()), float(b.lat.max()))))
    lon = target.lon.sel(lon=slice(max(float(a.lon.min()), float(b.lon.min())), min(float(a.lon.max()), float(b.lon.max()))))
    if lat.size < 2 or lon.size < 2:
        raise ValueError("Produk tidak memiliki overlap grid yang cukup.")
    aligned = []
    for rain in (a, b):
        if np.isin(lat, rain.lat).all() and np.isin(lon, rain.lon).all():
            aligned.append(rain.sel(lat=lat, lon=lon))
        else:
            aligned.append(rain.interp(lat=lat, lon=lon, method="linear"))
    return align_time(*aligned, mode=time_alignment)


class DataStore:
    def __init__(self, config=None):
        self.config = config or load_config()
        self.dataset = None
        self.sources = {}
        self.version = ""

    def prepare(self):
        with LOCK:
            sources = {k: resolve_source(self.config["products"][k]) for k in ("A", "B")}
            identity = {"schema": SCHEMA_VERSION, "config": self.config, "sources": {
                k: [str(p), p.stat().st_size, p.stat().st_mtime_ns] for k, p in sources.items()}}
            version = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]
            if self.dataset is not None and self.version == version:
                return False
            cache = ROOT / self.config.get("cache_dir", "app/cache") / f"{version}.zarr"
            if not cache.exists():
                cache.parent.mkdir(parents=True, exist_ok=True)
                opened = []
                try:
                    arrays = []
                    for key in ("A", "B"):
                        ds = xr.open_dataset(sources[key], chunks="auto")
                        opened.append(ds)
                        arrays.append(normalize(ds, self.config["products"][key]))
                    a, b = regrid_pair(*arrays, self.config.get("grid", "coarser"),
                                       self.config.get("time_alignment", "exact"))
                    ds = xr.Dataset({"A": a, "B": b}).chunk({"time": 1, "lat": 128, "lon": 128})
                    ds.attrs.update(version=version, grid_method="bilinear within common overlap",
                                    time_alignment=self.config.get("time_alignment", "exact"))
                    for v in ds.variables:
                        ds[v].encoding = {}
                    temporary = cache.with_suffix(".building.zarr")
                    ds.to_zarr(temporary, mode="w", zarr_format=2, consolidated=True)
                    temporary.rename(cache)
                finally:
                    for ds in opened:
                        ds.close()
            old = self.dataset
            self.dataset = xr.open_zarr(cache, consolidated=True)
            self.version = version
            self.sources = sources
            if old is not None:
                old.close()
            return True

    def close(self):
        if self.dataset is not None:
            self.dataset.close()

    def metadata(self):
        ds = self.dataset
        return {k: {"id": k, "name": self.config["products"][k]["name"],
                    "path": str(self.sources[k]), "variable": ds[k].attrs["original_variable"],
                    "units": "mm", "original_units": ds[k].attrs["original_units"],
                    "negative_values_clipped": ds[k].attrs["negative_values_clipped"],
                    "initialization_utc": ds[k].attrs["initialization_utc"],
                    "time_label": ds[k].attrs["time_label"],
                    "time_alignment": ds.attrs.get("time_alignment", "exact"),
                    "comparison_dates": [pd.Timestamp(t).strftime("%Y-%m-%d") for t in ds.comparison_date.values],
                    "accumulation_starts": [pd.Timestamp(t).isoformat()+"Z" for t in ds[f"accumulation_start_{k}"].values],
                    "accumulation_ends": [pd.Timestamp(t).isoformat()+"Z" for t in ds[f"accumulation_end_{k}"].values],
                    "bbox": [float(ds.lon.min()), float(ds.lat.min()), float(ds.lon.max()), float(ds.lat.max())],
                    "shape": list(ds[k].shape), "resolution": [float(ds.lon[1]-ds.lon[0]), float(ds.lat[1]-ds.lat[0])],
                    "times": [pd.Timestamp(t).isoformat()+"Z" for t in ds[f"accumulation_end_{k}"].values],
                    "period_hours": ds.period_hours.values.tolist(), "cache_version": self.version}
                for k in ("A", "B")}
