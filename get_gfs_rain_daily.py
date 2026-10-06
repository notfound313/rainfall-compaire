#!/usr/bin/env python3
"""
Hujan harian (akumulasi 24 jam) dari NOAA GFS 0.25 deg -> NetCDF.

Install:
    pip install requests xarray cfgrib netcdf4
    (butuh eccodes, mis. `conda install -c conda-forge eccodes`)

Cara pakai: edit bagian KONFIGURASI di bawah, lalu jalankan:
    python get_gfs_rain_daily.py

Logika:
  Untuk setiap tanggal D dari START_DATE sampai END_DATE dipakai run D jam RUN_HOUR UTC.
  APCP di GFS adalah bucket 6 jam (f006 = 0-6 jam, f012 = 6-12 jam, f018 = 12-18 jam, ...),
  sehingga hujan harian = jumlah 4 bucket 6 jam berurutan:
    LEAD_DAYS = 0 -> f006 + f012 + f018 + f024   (jam 0-24)
    LEAD_DAYS = 1 -> f030 + f036 + f042 + f048   (jam 24-48), dst. (maks. LEAD_DAYS = 15)
  Hanya 4 file kecil per hari yang diunduh.
"""
import os
import time
from datetime import datetime, timedelta

import numpy as np
import requests
import xarray as xr

# ======================= KONFIGURASI  =======================
START_DATE = "2026-10-01"      # tanggal awal  (YYYY-MM-DD)
END_DATE   = "2026-10-04"      # tanggal akhir (YYYY-MM-DD), ikut dihitung
RUN_HOUR   = 0                 # jam run UTC: 0, 6, 12, atau 18
LEAD_DAYS  = 0                 # 0 = jam 0-24, 1 = jam 24-48, dst.

# Domain (default: Indonesia)
WEST, EAST, SOUTH, NORTH = 95.0, 141.0, -11.0, 6.0

# Sumber data:
#   "nomads" = NOMADS grib filter (hanya ~10 hari terakhir, potong domain di server)
#   "aws"    = bucket publik NOAA di AWS S3 (arsip panjang, ambil APCP via byte-range)
SOURCE = "aws"

OUTDIR = "gfs_out"
OUTPUT_NC = "gfs_rain_daily.nc"
# ==========================================================================

NOMADS_URL = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
AWS_BASE = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"


def daterange(start, end):
    d = datetime.strptime(start, "%Y-%m-%d")
    last = datetime.strptime(end, "%Y-%m-%d")
    while d <= last:
        yield d
        d += timedelta(days=1)


def _save_if_grib(content, path):
    if not content.startswith(b"GRIB"):
        return False
    with open(path, "wb") as f:
        f.write(content)
    return True


def fetch_nomads(run_dt, fh, path):
    params = {
        "file": f"gfs.t{run_dt:%H}z.pgrb2.0p25.f{fh:03d}",
        "var_APCP": "on",
        "subregion": "",
        "leftlon": WEST,
        "rightlon": EAST,
        "toplat": NORTH,
        "bottomlat": SOUTH,
        "dir": f"/gfs.{run_dt:%Y%m%d}/{run_dt:%H}/atmos",
    }
    r = requests.get(NOMADS_URL, params=params, timeout=120)
    return r.status_code == 200 and _save_if_grib(r.content, path)


def fetch_aws(run_dt, fh, path):
    url = (f"{AWS_BASE}/gfs.{run_dt:%Y%m%d}/{run_dt:%H}/atmos/"
           f"gfs.t{run_dt:%H}z.pgrb2.0p25.f{fh:03d}")
    idx = requests.get(url + ".idx", timeout=60)
    if idx.status_code != 200:
        return False
    lines = [ln for ln in idx.text.splitlines() if ln.strip()]
    for i, ln in enumerate(lines):
        p = ln.split(":")
        if len(p) > 4 and p[3] == "APCP" and p[4] == "surface":
            start = int(p[1])
            end = int(lines[i + 1].split(":")[1]) - 1 if i + 1 < len(lines) else ""
            r = requests.get(url, headers={"Range": f"bytes={start}-{end}"}, timeout=120)
            return r.status_code in (200, 206) and _save_if_grib(r.content, path)
    return False


def fetch_step(run_dt, fh, path):
    for attempt in range(3):
        try:
            ok = fetch_nomads(run_dt, fh, path) if SOURCE == "nomads" else fetch_aws(run_dt, fh, path)
            if ok:
                return True
        except requests.RequestException as e:
            print(f"    percobaan {attempt + 1} gagal: {e}")
        time.sleep(2)
    return False


def read_bucket(path):
    ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": ""})
    da = ds["tp"].load()
    drop = [c for c in ("step", "time", "valid_time", "surface") if c in da.coords]
    da = da.drop_vars(drop)
    return da.sel(latitude=slice(NORTH, SOUTH), longitude=slice(WEST, EAST))


def daily_for_run(run_dt):
    """Jumlahkan 4 bucket 6 jam untuk jendela 24 jam sesuai LEAD_DAYS."""
    base = 24 * LEAD_DAYS
    total = None
    for fh in (base + 6, base + 12, base + 18, base + 24):
        path = os.path.join(OUTDIR, f"gfs_apcp_{run_dt:%Y%m%d%H}_f{fh:03d}.grib2")
        if not os.path.exists(path) and not fetch_step(run_dt, fh, path):
            raise RuntimeError(f"f{fh:03d} tidak tersedia")
        bucket = read_bucket(path)
        total = bucket if total is None else total + bucket
    return total.clip(min=0)


def main():
    os.makedirs(OUTDIR, exist_ok=True)

    layers, times = [], []
    for day in daterange(START_DATE, END_DATE):
        run_dt = day.replace(hour=RUN_HOUR)
        valid_start = run_dt + timedelta(days=LEAD_DAYS)
        print(f"Proses run {run_dt:%Y-%m-%d %H}Z ...")
        try:
            layers.append(daily_for_run(run_dt))
            times.append(np.datetime64(valid_start))
            print(f"  OK  hujan harian {valid_start:%Y-%m-%d}")
        except Exception as e:
            print(f"  GAGAL: {e}")

    if not layers:
        raise SystemExit("Tidak ada data yang berhasil diproses.")

    precip = xr.concat(layers, dim="time").assign_coords(time=("time", times))
    precip.name = "precip_daily_mm"
    precip.attrs.update(units="mm/day", long_name="Daily (24 h) accumulated precipitation")

    out = precip.to_dataset()
    out.attrs.update(
        source=f"NOAA GFS 0.25deg ({SOURCE})",
        note=f"run {RUN_HOUR:02d}Z, lead {LEAD_DAYS} day(s); 'time' = awal jendela 24 jam (UTC)",
    )
    nc_path = os.path.join(OUTDIR, OUTPUT_NC)
    out.to_netcdf(nc_path, encoding={"precip_daily_mm": {"zlib": True, "complevel": 4}})
    print("NetCDF disimpan:", nc_path)
    print(out)


if __name__ == "__main__":
    main()
