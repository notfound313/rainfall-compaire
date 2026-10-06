import os
import xarray as xr
from ecmwf.opendata import Client

# ================== KONFIGURASI ==================
OUT_DIR = "ecmwf_rain"
N_DAYS = 5                                   # jumlah hari forecast (maks 15 untuk run 00/12 UTC)
BBOX = dict(north=6, south=-11.5, west=94, east=142)  # Indonesia
RUN_TIME = None                              # None = run terbaru; atau 0, 6, 12, 18
# =================================================

os.makedirs(OUT_DIR, exist_ok=True)
steps = [24 * d for d in range(1, N_DAYS + 1)]   # 24, 48, 72, ... jam

client = Client(source="ecmwf", model="ifs", resol="0p25")

kwargs = dict(type="fc", stream="oper", step=steps, param="tp")
if RUN_TIME is not None:
    kwargs["time"] = RUN_TIME

# --- 1. Download (GRIB2) ---
grib_path = os.path.join(OUT_DIR, "tp_cumulative.grib2")
result = client.retrieve(target=grib_path, **kwargs)
run_dt = result.datetime
print(f"Run forecast: {run_dt:%Y-%m-%d %H} UTC")

# --- 2. Baca & potong ke domain ---
ds = xr.open_dataset(grib_path, engine="cfgrib")
ds = ds.sel(
    latitude=slice(BBOX["north"], BBOX["south"]),   # lat di ECMWF urut menurun
    longitude=slice(BBOX["west"], BBOX["east"]),
)

# --- 3. Kumulatif -> akumulasi per 24 jam (mm) ---
cum = ds["tp"] * 1000.0                              # m -> mm
daily = xr.concat([cum.isel(step=[0]), cum.diff("step")], dim="step")
daily.attrs.update(units="mm", long_name="Rainfall accumulation per 24 h")
daily.name = "rain_24h"

# --- 4. Simpan ---
nc_path = os.path.join(OUT_DIR, f"rain24h_{run_dt:%Y%m%d%H}.nc")
daily.to_dataset().to_netcdf(nc_path)
print("Tersimpan:", nc_path)

# (opsional) ekspor GeoTIFF per hari: pip install rioxarray
# import rioxarray
# for i, step in enumerate(daily.step.values):
#     da = daily.isel(step=i).rio.write_crs("EPSG:4326")
#     da.rio.to_raster(os.path.join(OUT_DIR, f"rain24h_{run_dt:%Y%m%d%H}_d{i+1}.tif"))