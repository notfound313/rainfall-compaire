from functools import lru_cache

import numpy as np
import pandas as pd
from shapely import contains_xy
from shapely.geometry import shape

from app.core.settings import CATEGORIES, NOTE

TIME_NOTE = "Pencocokan per label tanggal; jendela akumulasi A/B berbeda, bukan periode hujan identik."


def classify(values, profile="daily"):
    # Exact 150 remains very heavy; extreme requires >150 per PRD.
    edges = [c["lower"] for c in CATEGORIES[profile]][1:]
    indices = np.digitize(values, edges)
    if profile == "daily":
        indices = np.where(values == 150, 4, indices)
    return np.where(np.isfinite(values), indices, -1)


def cell_edges(centers):
    centers = np.asarray(centers, dtype=float)
    mid = (centers[:-1] + centers[1:]) / 2
    return np.r_[centers[0] - (mid[0]-centers[0]), mid, centers[-1]+(centers[-1]-mid[-1])]


def cell_areas(lat, lon):
    # Exact spherical quadrilateral area, not constant km² per pixel.
    lat_edges = np.deg2rad(np.clip(cell_edges(lat), -90, 90))
    lon_edges = np.deg2rad(cell_edges(lon))
    return 6371.0088**2 * np.abs(np.diff(np.sin(lat_edges)))[:, None] * np.abs(np.diff(lon_edges))[None, :]


def iso(value):
    return pd.Timestamp(value).isoformat() + "Z"


class Analysis:
    def __init__(self, store):
        self.store = store

    def clear(self):
        self.periods.cache_clear()
        self.frame.cache_clear()

    @lru_cache(maxsize=20)
    def periods(self, agg="daily"):
        if agg not in ("native", "daily", "3daily", "monthly", "max"):
            raise ValueError("Agregasi: native, daily, 3daily, monthly atau max")
        ds = self.store.dataset
        ends = ds.time.values
        starts = ds.accumulation_start.values
        hours = ds.period_hours.values
        if agg == "native":
            groups = [[i] for i in range(len(ends))]
        elif agg == "max":
            groups = [list(range(len(ends)))]
        elif agg == "monthly":
            # Assign intervals by their start. Never split a 24-hour amount across midnight.
            keys = pd.DatetimeIndex(starts).strftime("%Y-%m")
            groups = [np.flatnonzero(keys == key).tolist() for key in dict.fromkeys(keys)]
        else:
            span = 24 if agg == "daily" else 72
            if (hours > 24).any() or not np.allclose(24 / hours, np.round(24 / hours)):
                raise ValueError("Akumulasi harian membutuhkan interval yang membagi 24 jam.")
            elapsed = (starts - starts[0]) / np.timedelta64(1, "h")
            if np.any((elapsed % span) + hours > span):
                raise ValueError("Interval melintasi batas agregasi; tidak dapat dibagi tanpa data subperiode.")
            keys = (elapsed // span).astype(int)
            groups = [np.flatnonzero(keys == key).tolist() for key in dict.fromkeys(keys)]
        result = []
        for n, ids in enumerate(groups):
            total = float(hours[ids].sum())
            start, end = starts[ids[0]], ends[ids[-1]]
            partial = (agg in ("daily", "3daily") and total != (24 if agg == "daily" else 72))
            if agg == "monthly":
                month_start = pd.Timestamp(start).to_period("M").start_time
                partial = pd.Timestamp(start) != month_start or pd.Timestamp(end) != month_start + pd.offsets.MonthBegin(1)
            source_periods = {}
            for key in ("A", "B"):
                actual_start = ds.get(f"accumulation_start_{key}", ds.accumulation_start).values
                actual_end = ds.get(f"accumulation_end_{key}", ds.time).values
                source_periods[key] = {"start": iso(actual_start[ids[0]]), "end": iso(actual_end[ids[-1]]),
                                       "hours": total}
            windows_match = source_periods["A"] == source_periods["B"]
            alignment = ds.attrs.get("time_alignment", "exact")
            comparison_dates = ds.get("comparison_date", ds.time).values
            date_start = pd.Timestamp(comparison_dates[ids[0]]).strftime("%Y-%m-%d")
            date_end = pd.Timestamp(comparison_dates[ids[-1]]).strftime("%Y-%m-%d")
            result.append({"index": n, "indices": ids, "start": iso(start), "end": iso(end),
                           "hours": total, "partial": bool(partial), "operation": "max" if agg == "max" else "sum",
                           "time_alignment": alignment, "index_window_only": alignment == "date",
                           "comparison_date_start": date_start, "comparison_date_end": date_end,
                           "source_periods": source_periods, "windows_match": windows_match,
                           "time_note": TIME_NOTE if not windows_match else "",
                           "label": (f"{date_start} – {date_end} · label tanggal UTC" if alignment == "date"
                                     else f"{pd.Timestamp(start):%d %b %H:%M} – {pd.Timestamp(end):%d %b %H:%M} UTC")})
        return result

    @lru_cache(maxsize=100)
    def frame(self, time=0, agg="daily"):
        periods = self.periods(agg)
        if time < 0 or time >= len(periods):
            raise ValueError("Indeks waktu di luar periode tersedia.")
        period = periods[time]
        ds = self.store.dataset.isel(time=period["indices"])
        # min_count avoids silently treating a wholly missing interval as zero.
        if agg == "max":
            arrays = [ds[k].max("time", skipna=False) for k in ("A", "B")]
        else:
            arrays = [ds[k].sum("time", skipna=False) for k in ("A", "B")]
        return tuple(np.asarray(r.compute().values, dtype=np.float32) for r in arrays)

    def mask(self, bbox=None, geometry=None):
        ds = self.store.dataset
        xx, yy = np.meshgrid(ds.lon.values, ds.lat.values)
        mask = np.ones(xx.shape, dtype=bool)
        if bbox:
            if isinstance(bbox, str):
                bbox = [float(v) for v in bbox.split(",")]
            if len(bbox) != 4 or not np.isfinite(bbox).all() or bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
                raise ValueError("bbox harus west,south,east,north yang valid")
            mask &= (xx >= bbox[0]) & (xx <= bbox[2]) & (yy >= bbox[1]) & (yy <= bbox[3])
        if geometry:
            try:
                polygon = shape(geometry.get("geometry", geometry))
            except Exception as error:
                raise ValueError("GeoJSON area tidak valid") from error
            if polygon.geom_type not in ("Polygon", "MultiPolygon") or not polygon.is_valid:
                raise ValueError("Area harus Polygon/MultiPolygon GeoJSON valid")
            mask &= contains_xy(polygon, xx, yy)
        return mask

    def summary(self, time=0, agg="daily", threshold=50, bbox=None, geometry=None):
        a, b = self.frame(time, agg)
        common = self.mask(bbox, geometry) & np.isfinite(a) & np.isfinite(b)
        areas = cell_areas(self.store.dataset.lat, self.store.dataset.lon)
        total_area = float(areas[common].sum())
        heavy_a, heavy_b = (a > threshold) & common, (b > threshold) & common
        products = {}
        for key, values, heavy in (("A", a, heavy_a), ("B", b, heavy_b)):
            sample = values[common]
            area = float(areas[heavy].sum())
            products[key] = {"name": self.store.config["products"][key]["name"],
                             "grids_exceed": int(heavy.sum()), "area_km2": area,
                             "area_pct": 100 * area / total_area if total_area else None,
                             "mean_mm": float(sample.mean()) if sample.size else None,
                             "max_mm": float(sample.max()) if sample.size else None,
                             "p95_mm": float(np.percentile(sample, 95)) if sample.size else None,
                             "p99_mm": float(np.percentile(sample, 99)) if sample.size else None}
        na, nb = int(heavy_a.sum()), int(heavy_b.sum())
        union = heavy_a | heavy_b
        diff = float(np.mean(a[union] - b[union])) if union.any() else None
        denom = float(np.mean(b[union])) if union.any() else 0
        if not common.any():
            conclusion = "Tidak ada grid valid bersama di area ini."
        elif na == nb:
            conclusion = f"Kedua produk mendeteksi {na} grid di atas {threshold:g} mm."
        elif not nb:
            conclusion = f"A mendeteksi {na} grid; B tidak mendeteksi grid di atas ambang. Rasio tidak terdefinisi."
        elif not na:
            conclusion = f"B mendeteksi {nb} grid; A tidak mendeteksi grid di atas ambang."
        else:
            winner, baseline, high = ("A", nb, na) if na > nb else ("B", na, nb)
            conclusion = f"Produk {winner} mendeteksi {(high / baseline - 1) * 100:.1f}% lebih banyak grid di atas {threshold:g} mm."
        period = self.periods(agg)[time]
        return {"threshold_mm": threshold, "agg": agg, "time": time, "period": period,
                "products": products, "valid_grids": int(common.sum()), "study_area_km2": total_area,
                "agreement": {"both": int((heavy_a & heavy_b).sum()), "only_A": int((heavy_a & ~heavy_b).sum()),
                              "only_B": int((heavy_b & ~heavy_a).sum()), "neither": int((common & ~union).sum())},
                "sensitivity_ratio_A_over_B": na / nb if nb else None,
                "heavy_mean_difference_mm": diff,
                "heavy_relative_bias_pct": 100 * diff / denom if denom and diff is not None else None,
                "conclusion": conclusion, "note": NOTE,
                "time_note": period["time_note"],
                "parameters": {"bbox": bbox, "geometry": geometry, "grid": self.store.config.get("grid", "coarser"),
                               "area_method": "spherical cell area, selected by cell centre", "timezone": "UTC",
                               "time_alignment": period["time_alignment"], "windows_match": period["windows_match"],
                               "cache_version": self.store.version}}

    def point(self, lat, lon, time=0, agg="daily"):
        ds = self.store.dataset
        if not np.isfinite([lat,lon]).all():
            raise ValueError("Latitude/longitude harus finite.")
        if not float(ds.lat.min()) <= lat <= float(ds.lat.max()) or not float(ds.lon.min()) <= lon <= float(ds.lon.max()):
            raise ValueError("Titik di luar domain data.")
        i, j = int(abs(ds.lat.values - lat).argmin()), int(abs(ds.lon.values - lon).argmin())
        a, b = self.frame(time, agg)
        profile = "hourly" if self.periods(agg)[time]["hours"] <= 1 else "daily"
        av, bv = float(a[i,j]), float(b[i,j])
        def category(v):
            index = int(classify(np.array(v), profile))
            return CATEGORIES[profile][index]["label"] if index >= 0 else "NoData"
        return {"lat": float(ds.lat[i]), "lon": float(ds.lon[j]), "A": av if np.isfinite(av) else None,
                "B": bv if np.isfinite(bv) else None, "diff": av-bv if np.isfinite(av+bv) else None,
                "category_A": category(av), "category_B": category(bv), "indices": [i,j]}

    def timeseries(self, lat, lon, threshold=50):
        point = self.point(lat, lon, 0, "native")
        i, j = point.pop("indices")
        ds = self.store.dataset.isel(lat=i, lon=j).compute()
        values = {k: ds[k].values for k in ("A", "B")}
        date_mode = ds.attrs.get("time_alignment", "exact") == "date"
        return {"lat": point["lat"], "lon": point["lon"],
                "times": [iso(t) for t in (ds.comparison_date.values if date_mode else ds.time.values)],
                "starts": [iso(t) for t in ds.accumulation_start.values],
                "time_alignment": ds.attrs.get("time_alignment", "exact"), "index_window_only": date_mode,
                "source_periods": {k: [{"start": iso(start), "end": iso(end)} for start,end in zip(
                    ds.get(f"accumulation_start_{k}", ds.accumulation_start).values,
                    ds.get(f"accumulation_end_{k}", ds.time).values)] for k in ("A", "B")},
                "time_note": self.periods("native")[0]["time_note"],
                "A": values["A"].tolist(), "B": values["B"].tolist(), "units": "mm",
                "threshold_mm": threshold,
                "stats": {k: {"max_mm": float(np.nanmax(v)) if np.isfinite(v).any() else None,
                               "periods_exceed": int((v > threshold).sum())} for k,v in values.items()}}
