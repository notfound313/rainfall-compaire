import csv
import io

import numpy as np
import pytest
import xarray as xr
from fastapi.testclient import TestClient

import app.main as main
from app.core.settings import ROOT
from app.services.loader import DataStore


@pytest.fixture(scope="module")
def client():
    config={"products":{
        "A":{"name":"ECMWF IFS","path":"../ecmwf_rain/rain24h_2026093018.nc","variable":"rain_24h","period_hours":24,"time_label":"end"},
        "B":{"name":"ECMWF daily 00Z","path":"../ecmwf_out/ecmwf_rain_daily.nc","variable":"precip_daily_mm","period_hours":24,"time_label":"start"},
    },"grid":"coarser","time_alignment":"date","cache_dir":"app/cache","overlays":[]}
    original=main.engine
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(main,"engine",lambda config_override=None:original(config_override or config))
        with TestClient(main.app) as client:
            yield client


def test_actual_ecmwf_date_pair(client):
    meta=client.get("/api/products").json()
    assert meta["time_alignment"]=="date"
    assert meta["products"]["B"]["name"]=="ECMWF daily 00Z"
    for key in ("A","B"):
        assert meta["products"][key]["shape"]==[5,69,185]
        assert meta["products"][key]["comparison_dates"]==[f"2026-10-0{i}" for i in range(1,6)]
    periods=client.get("/api/periods").json()["periods"]
    first=periods[0]
    assert first["source_periods"]["A"]["start"]=="2026-09-30T18:00:00Z"
    assert first["source_periods"]["A"]["end"]=="2026-10-01T18:00:00Z"
    assert first["source_periods"]["B"]["start"]=="2026-10-01T00:00:00Z"
    assert first["source_periods"]["B"]["end"]=="2026-10-02T00:00:00Z"
    assert not first["windows_match"]
    assert first["index_window_only"]
    assert "jendela akumulasi" in first["time_note"]


def test_source_values_not_shifted_or_changed(client):
    store,analysis,_=main.app.state.engine
    with xr.open_dataset(ROOT.parent/"ecmwf_out/ecmwf_rain_daily.nc") as source:
        expected=source.precip_daily_mm.sortby("latitude").values
        for i in range(5):
            np.testing.assert_array_equal(analysis.frame(i)[1],expected[i])
    with xr.open_dataset(ROOT.parent/"ecmwf_rain/rain24h_2026093018.nc") as source:
        expected=source.rain_24h.sel(latitude=slice(6,-11),longitude=slice(95,141)).sortby("latitude").clip(min=0).values.astype(np.float32)
        for i in range(5):
            np.testing.assert_array_equal(analysis.frame(i)[0],expected[i])
    summaries=client.get("/api/compare/summary?start=0&end=4").json()["summaries"]
    assert all(s["valid_grids"]==69*185 for s in summaries)
    assert all(s["parameters"]["time_alignment"]=="date" for s in summaries)
    assert all(s["time_note"] for s in summaries)


def test_date_month_aggregation_and_series(client):
    periods=client.get("/api/periods?agg=monthly").json()["periods"]
    assert len(periods)==1 and periods[0]["hours"]==120 and periods[0]["partial"]
    assert periods[0]["comparison_date_start"]=="2026-10-01"
    assert periods[0]["comparison_date_end"]=="2026-10-05"
    series=client.get("/api/compare/timeseries?lat=-2&lon=105").json()
    assert series["times"][0]=="2026-10-01T00:00:00Z"
    assert series["source_periods"]["B"][-1]["end"]=="2026-10-06T00:00:00Z"
    assert series["time_note"]


def test_exports_record_real_windows(client):
    records=list(csv.DictReader(io.StringIO(client.get("/api/export/csv").text.lstrip("\ufeff"))))
    assert len(records)==10
    assert records[0]["period_start_utc"]=="2026-09-30T18:00:00Z"
    assert records[1]["period_start_utc"]=="2026-10-01T00:00:00Z"
    assert records[1]["period_end_utc"]=="2026-10-02T00:00:00Z"
    assert records[1]["comparison_date_start"]=="2026-10-01"
    assert records[1]["windows_match"]=="False"
    assert records[1]["name"]=="ECMWF daily 00Z"
    geojson=client.get("/api/export/geojson?product=B&threshold=20").json()
    assert geojson["metadata"]["period"]["source_periods"]["B"]["start"]=="2026-10-01T00:00:00Z"
    assert geojson["metadata"]["period"]["time_note"]
    assert geojson["metadata"]["product_names"]["B"]=="ECMWF daily 00Z"
    assert client.get("/api/export/png?product=diff").status_code==200


def test_actual_pair_exact_mode_still_rejects_different_windows(client):
    config=dict(main.app.state.engine[0].config,time_alignment="exact")
    with pytest.raises(ValueError,match="mode exact.*A:.*B:"):
        DataStore(config).prepare()
