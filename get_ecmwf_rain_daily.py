#!/usr/bin/env python3
"""
Hujan harian (akumulasi 24 jam) dari ECMWF IFS Open Data 0.25 deg -> NetCDF.

Install:
    pip install ecmwf-opendata xarray cfgrib netcdf4
    (butuh eccodes, mis. `conda install -c conda-forge eccodes`)

Cara pakai: edit bagian KONFIGURASI di bawah, lalu jalankan:
    python get_ecmwf_rain_daily.py

Logika:
  Untuk setiap tanggal D dari START_DATE sampai END_DATE dipakai run D jam RUN_HOUR UTC.
  Hujan harian = tp(24*(LEAD_DAYS+1) jam) - tp(24*LEAD_DAYS jam), karena tp di ECMWF
  adalah akumulasi sejak awal run (meter -> dikonversi ke mm).
    LEAD_DAYS = 0 -> jam 0-24 dari run (hari yang sama, analisis/prakiraan terdekat)
    LEAD_DAYS = 1 -> jam 24-48 (prakiraan H+1), dst. (maks. LEAD_DAYS = 9)
"""
import os
from datetime import datetime, timedelta

import numpy as np
import xarray as xr
from ecmwf.opendata import Client

# ======================= KONFIGURASI (EDIT DI SINI) =======================
START_DATE = "2026-10-01"      # tanggal awal  (YYYY-MM-DD)
END_DATE   = "2026-10-05"      # tanggal akhir (YYYY-MM-DD), ikut dihitung
RUN_HOUR   = 0                 # jam run UTC: 0, 6, 12, atau 18
LEAD_DAYS  = 0                 # 0 = jam 0-24, 1 = jam 24-48, dst.

# Domain (default: Indonesia)
WEST, EAST, SOUTH, NORTH = 95.0, 141.0, -11.0, 6.0

# Sumber data:
#   "ecmwf" = server ECMWF (hanya menyimpan beberapa hari terakhir)
#   "aws" / "google" / "azure" = mirror dengan arsip lebih panjang (pakai ini untuk tanggal lama)
SOURCE = "google"

OUTDIR = "ecmwf_out"
OUTPUT_NC = "ecmwf_rain_daily.nc"
# ==========================================================================


def daterange(start, end):
    d = datetime.strptime(start, "%Y-%m-%d")
    last = datetime.strptime(end, "%Y-%m-%d")
    while d <= last:
        yield d
        d += timedelta(days=1)


def download_day(client, run_dt, grib_path):
    """Unduh tp untuk run_dt pada step awal dan akhir jendela 24 jam."""
    end_step = 24 * (LEAD_DAYS + 1)
    start_step = 24 * LEAD_DAYS
    steps = [end_step] if start_step == 0 else [start_step, end_step]
    client.retrieve(
        date=run_dt.strftime("%Y-%m-%d"),
        time=run_dt.hour,
        type="fc",
        stream="oper",
        param="tp",
        step=steps,
        target=grib_path,
    )


def daily_from_grib(grib_path):
    ds = xr.open_dataset(grib_path, engine="cfgrib", backend_kwargs={"indexpath": ""})
    tp = ds["tp"].load()
    if "step" in tp.dims and tp.sizes["step"] > 1:
        tp = tp.sortby("step")
        daily = tp.isel(step=-1) - tp.isel(step=0)
    elif "step" in tp.dims:
        daily = tp.isel(step=0)
    else:
        daily = tp
    daily = (daily * 1000.0).clip(min=0)  # m -> mm
    drop = [c for c in ("step", "time", "valid_time", "surface", "number") if c in daily.coords]
    daily = daily.drop_vars(drop)
    daily = daily.sel(latitude=slice(NORTH, SOUTH), longitude=slice(WEST, EAST))
    return daily


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    client = Client(source=SOURCE)

    layers, times = [], []
    for day in daterange(START_DATE, END_DATE):
        run_dt = day.replace(hour=RUN_HOUR)
        valid_start = run_dt + timedelta(days=LEAD_DAYS)
        grib_path = os.path.join(OUTDIR, f"ecmwf_tp_{run_dt:%Y%m%d%H}_L{LEAD_DAYS}.grib2")
        try:
            if not os.path.exists(grib_path):
                print(f"Unduh run {run_dt:%Y-%m-%d %H}Z ...")
                download_day(client, run_dt, grib_path)
            layers.append(daily_from_grib(grib_path))
            times.append(np.datetime64(valid_start))
            print(f"  OK  hujan harian {valid_start:%Y-%m-%d}")
        except Exception as e:
            print(f"  GAGAL {run_dt:%Y-%m-%d %H}Z: {e}")

    if not layers:
        raise SystemExit("Tidak ada data yang berhasil diproses.")

    precip = xr.concat(layers, dim="time").assign_coords(time=("time", times))
    precip.name = "precip_daily_mm"
    precip.attrs.update(units="mm/day", long_name="Daily (24 h) accumulated precipitation")

    out = precip.to_dataset()
    out.attrs.update(
        source=f"ECMWF IFS Open Data 0.25deg ({SOURCE})",
        note=f"run {RUN_HOUR:02d}Z, lead {LEAD_DAYS} day(s); 'time' = awal jendela 24 jam (UTC)",
    )
    nc_path = os.path.join(OUTDIR, OUTPUT_NC)
    out.to_netcdf(nc_path, encoding={"precip_daily_mm": {"zlib": True, "complevel": 4}})
    print("NetCDF disimpan:", nc_path)
    print(out)


if __name__ == "__main__":
    main()
