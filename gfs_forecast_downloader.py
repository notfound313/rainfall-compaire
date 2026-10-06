"""Download GFS 0.25-degree daily rainfall aligned with local ECMWF NetCDF.

APCP is selected from the accumulation starting at forecast hour zero.
Differences between consecutive 24-hour endpoints give daily rainfall in mm.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
from herbie import Herbie


BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "gfs_rain"
ECMWF_DIR = BASE_DIR / "ecmwf_rain"
PRODUCT = "pgrb2.0p25"
PRIORITY = ["aws", "nomads", "google"]
ENCODING = {
    "precipitation": {"zlib": True, "complevel": 4, "dtype": "float32"}
}


def read_ecmwf_reference():
    """Read the latest initialization, forecast endpoints, and actual grid."""
    candidates = []
    for path in ECMWF_DIR.glob("rain24h_*.nc"):
        try:
            run = pd.to_datetime(path.stem.removeprefix("rain24h_"), format="%Y%m%d%H")
        except ValueError:
            continue
        candidates.append((run, path))
    if not candidates:
        raise FileNotFoundError(f"NetCDF rain24h_YYYYMMDDHH.nc tidak ditemukan di {ECMWF_DIR}")

    _, path = max(candidates)
    with xr.open_dataset(path) as ds:
        if "rain_24h" not in ds:
            raise ValueError(f"Variable rain_24h tidak ditemukan: {path}")
        if ds.time.size != 1:
            raise ValueError("Acuan ECMWF harus memiliki satu waktu inisialisasi.")
        run = pd.Timestamp(ds.time.values.item(), unit="ns")
        steps = ds.step.values.astype("timedelta64[ns]")
        hours = steps / np.timedelta64(1, "h")
        if (
            steps.size == 0
            or not np.array_equal(hours, np.arange(1, steps.size + 1) * 24)
            or hours[-1] > 384
        ):
            raise ValueError("Step ECMWF harus 24, 48, ... jam, maksimum 384 jam.")
        if run.hour not in (0, 6, 12, 18) or run != run.floor("6h"):
            raise ValueError(f"Waktu ECMWF bukan cycle GFS yang valid: {run}")
        valid_times = run.to_datetime64() + steps
        if not np.array_equal(ds.valid_time.values, valid_times):
            raise ValueError("valid_time ECMWF tidak cocok dengan time + step.")
        latitude = ds.latitude.values.copy()
        longitude = ds.longitude.values.copy()
        if latitude.ndim != 1 or longitude.ndim != 1 or not latitude.size or not longitude.size:
            raise ValueError("Grid latitude/longitude ECMWF tidak valid.")

    return path, run, hours.astype(int), latitude, longitude


def get_cumulative_precip(run, forecast_hour, latitude, longitude):
    """Download only cumulative APCP, then select the exact ECMWF grid."""
    h = Herbie(
        run,
        model="gfs",
        product=PRODUCT,
        fxx=int(forecast_hour),
        priority=PRIORITY,
        save_dir=OUT_DIR / "grib",
        verbose=False,
    )
    if not h.grib:
        raise RuntimeError(
            f"GFS {run:%Y-%m-%d %H} UTC f{forecast_hour:03d} tidak tersedia. "
            "Run tidak diganti agar tetap cocok dengan ECMWF."
        )

    day = forecast_hour // 24
    searches = (
        rf":APCP:surface:0-{day} day acc fcst:",
        rf":APCP:surface:0-{forecast_hour} hour acc fcst:",
    )
    for search in searches:
        inventory = h.inventory(search)
        if inventory is not None and len(inventory):
            if len(inventory) != 1:
                raise RuntimeError(f"APCP kumulatif ambigu untuk f{forecast_hour:03d}.")
            break
    else:
        raise RuntimeError(f"APCP kumulatif 0-{forecast_hour} jam tidak ditemukan.")

    print(f"f{forecast_hour:03d}: {search}", flush=True)
    ds = h.xarray(search, remove_grib=False)
    if not isinstance(ds, xr.Dataset):
        raise RuntimeError("Subset APCP menghasilkan beberapa dataset; pilihan tidak unik.")
    try:
        name = next((v for v in ("tp", "APCP", "apcp") if v in ds.data_vars), None)
        if name is None:
            raise RuntimeError(f"Variable APCP tidak ditemukan: {list(ds.data_vars)}")
        rain = ds[name]
        if pd.Timestamp(ds.time.values.item(), unit="ns") != run:
            raise RuntimeError("Waktu inisialisasi GRIB tidak cocok dengan ECMWF.")
        if ds.step.values != np.timedelta64(int(forecast_hour), "h"):
            raise RuntimeError("Step GRIB tidak cocok dengan forecast hour yang diminta.")
        units = rain.attrs.get("units", rain.attrs.get("GRIB_units", ""))
        if units not in ("kg m**-2", "kg m-2", "kg m^-2", "mm"):
            raise RuntimeError(f"Unit APCP tidak dikenal: {units!r}")

        # Exact selection also preserves the ECMWF latitude order. No interpolation.
        rain = rain.squeeze(drop=True).sel(latitude=latitude, longitude=longitude)
        rain = rain.transpose("latitude", "longitude").load()
        result = xr.DataArray(
            rain.values.astype(np.float32),
            dims=("latitude", "longitude"),
            coords={"latitude": latitude, "longitude": longitude},
            name="precipitation_cumulative",
            attrs={"units": "mm", "forecast_hour": int(forecast_hour)},
        )
        if not np.isfinite(result.values).all():
            raise RuntimeError("APCP mengandung nilai hilang atau tidak finite.")
        return result
    finally:
        ds.close()


def make_daily(cumulative, previous, run, forecast_hour, day):
    """Convert cumulative rainfall to a 24-hour amount with ECMWF time axes."""
    daily = cumulative.copy() if previous is None else cumulative - previous
    # Small negative values can arise from independent GRIB packing.
    if float(daily.min()) < -0.1:
        raise RuntimeError(
            f"Selisih APCP negatif signifikan pada D{day:02d}: {float(daily.min()):.3f} mm"
        )
    daily = daily.clip(min=0).astype(np.float32)
    daily.name = "precipitation"
    start_time = run + pd.Timedelta(hours=int(forecast_hour) - 24)
    end_time = run + pd.Timedelta(hours=int(forecast_hour))
    daily = daily.expand_dims(step=[np.timedelta64(int(forecast_hour), "h")])
    daily = daily.assign_coords(
        time=run.to_datetime64(),
        valid_time=("step", [end_time.to_datetime64()]),
        accumulation_start=("step", [start_time.to_datetime64()]),
    )
    daily.attrs = {
        "long_name": "GFS rainfall accumulation per 24 h",
        "standard_name": "precipitation_amount",
        "units": "mm",
        "cell_methods": "time: sum (interval: 24 hours)",
        "source": "NOAA Global Forecast System",
        "model": "GFS",
        "resolution": "0.25 degree",
        "initialization_time_utc": run.strftime("%Y-%m-%d %H:%M UTC"),
        "forecast_day": day,
        "forecast_hour_start": int(forecast_hour) - 24,
        "forecast_hour_end": int(forecast_hour),
        "accumulation_start_utc": str(start_time),
        "accumulation_end_utc": str(end_time),
    }
    return daily


def main():
    reference, run, hours, latitude, longitude = read_ecmwf_reference()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    run_label = run.strftime("%Y%m%d_%H") + "z"
    print(f"Acuan ECMWF: {reference}", flush=True)
    print(f"GFS run: {run:%Y-%m-%d %H:00} UTC | {len(hours)} hari", flush=True)
    print(
        f"BBOX: west={longitude.min()}, east={longitude.max()}, "
        f"south={latitude.min()}, north={latitude.max()} | "
        f"grid={latitude.size} x {longitude.size}",
        flush=True,
    )

    daily_list = []
    previous = None
    for day, forecast_hour in enumerate(hours, start=1):
        cumulative = get_cumulative_precip(run, forecast_hour, latitude, longitude)
        daily = make_daily(cumulative, previous, run, forecast_hour, day)
        output = OUT_DIR / f"GFS_DAILY_{run_label}_D{day:02d}.nc"
        ds = daily.to_dataset()
        ds.attrs["ecmwf_reference"] = reference.name
        ds.to_netcdf(output, engine="netcdf4", encoding=ENCODING)
        print(
            f"SAVED: {output.name} | min={float(daily.min()):.2f}, "
            f"max={float(daily.max()):.2f}, mean={float(daily.mean()):.2f} mm/24h",
            flush=True,
        )
        daily_list.append(daily)
        previous = cumulative

    combined = xr.concat(daily_list, dim="step", join="exact")
    # Per-day attributes must not describe the entire combined forecast.
    for key in (
        "forecast_day", "forecast_hour_start", "forecast_hour_end",
        "accumulation_start_utc", "accumulation_end_utc",
    ):
        combined.attrs.pop(key, None)
    ds = combined.to_dataset()
    ds.attrs.update(ecmwf_reference=reference.name, forecast_days=len(hours))
    output = OUT_DIR / f"GFS_DAILY_{run_label}_ALL.nc"
    ds.to_netcdf(output, engine="netcdf4", encoding=ENCODING)
    print(f"SELESAI: {output} | shape={combined.shape}", flush=True)
    print(f"Valid time: {combined.valid_time.values}", flush=True)


if __name__ == "__main__":
    main()
