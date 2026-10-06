from io import BytesIO

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
import app.main as main


@pytest.fixture(scope="module")
def client():
    # Regression dataset is fixed independently of the user's live configuration.
    baseline={"products":{
        "A":{"name":"ECMWF IFS","path":"../ecmwf_rain/rain24h_2026093018.nc","variable":"rain_24h","period_hours":24},
        "B":{"name":"NOAA GFS","path":"../gfs_rain/GFS_DAILY_20260930_18z_ALL.nc","variable":"precipitation","period_hours":24},
    },"grid":"coarser","time_alignment":"exact","cache_dir":"app/cache","overlays":[]}
    original=main.engine
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(main,"engine",lambda config=None:original(config or baseline))
        with TestClient(app) as client:
            yield client


def test_real_metadata(client):
    result=client.get("/api/products").json()
    for key in ("A","B"):
        meta=result["products"][key]
        assert meta["shape"]==[5,71,193]
        assert meta["units"]=="mm"
        assert meta["bbox"]==[94,-11.5,142,6]
    assert result["products"]["A"]["negative_values_clipped"]==114


def test_real_baseline(client):
    results=client.get("/api/compare/summary?start=0&end=4").json()["summaries"]
    assert [s["products"]["A"]["grids_exceed"] for s in results]==[0,0,1,1,0]
    assert [s["products"]["B"]["grids_exceed"] for s in results]==[0,11,9,1,2]
    assert all(s["agreement"]["both"]==0 for s in results)
    assert all(s["valid_grids"]==13703 for s in results)


def test_aggregation_and_partial_periods(client):
    periods=client.get("/api/periods?agg=3daily").json()["periods"]
    assert [p["hours"] for p in periods]==[72,48]
    assert [p["partial"] for p in periods]==[False,True]
    analysis=app.state.engine[1]
    native=[analysis.frame(i,"native")[0] for i in range(5)]
    np.testing.assert_allclose(analysis.frame(0,"3daily")[0],sum(native[:3]),rtol=1e-6)
    np.testing.assert_allclose(analysis.frame(0,"max")[0],np.maximum.reduce(native))
    assert all(p["partial"] for p in client.get("/api/periods?agg=monthly").json()["periods"])


@pytest.mark.parametrize("product",["A","B","diff","agreement"])
def test_tiles_and_etag(client,product):
    url=f"/api/tiles/{product}/4/13/8.png?time=1"
    response=client.get(url)
    assert response.status_code==200
    image=Image.open(BytesIO(response.content))
    assert image.size==(256,256) and image.mode=="RGBA"
    assert client.get(url,headers={"If-None-Match":response.headers["etag"]}).status_code==304


def test_grid_bbox_and_limit(client):
    grid=client.get("/api/grid/B?time=1&limit=3").json()
    assert len(grid["features"])==3
    assert grid["metadata"]["truncated"]
    assert all(f["properties"]["B"]>50 for f in grid["features"])
    assert not client.get("/api/grid/B?time=1&bbox=0,0,1,1").json()["features"]


@pytest.mark.parametrize("url",[
    "/api/compare/summary?time=7","/api/compare/summary?threshold=nan",
    "/api/compare/summary?agg=bad","/api/compare/summary?bbox=bad",
    "/api/tiles/X/4/13/8.png","/api/tiles/A/4/16/8.png",
    "/api/compare/timeseries?lat=0&lon=0","/api/compare/point?lat=nan&lon=100",
])
def test_invalid_inputs(client,url):
    assert client.get(url).status_code==422


def test_area_and_timeseries(client):
    polygon={"type":"Polygon","coordinates":[[[99,-6],[106,-6],[106,1],[99,1],[99,-6]]]}
    result=client.post("/api/compare/area",json={"geometry":polygon,"time":1}).json()
    assert 0<result["valid_grids"]<13703
    point=client.get("/api/compare/point?lat=-2&lon=105&time=1").json()
    series=client.get("/api/compare/timeseries?lat=-2&lon=105").json()
    assert len(series["A"])==len(series["B"])==5
    assert point["A"]==series["A"][1]
    assert point["category_A"]
    assert client.post("/api/compare/area",json={"geometry":{"bad":True}}).status_code==422


def test_exports(client):
    geojson=client.get("/api/export/geojson?product=B&time=1")
    assert len(geojson.json()["features"])==11
    csv=client.get("/api/export/csv").text
    assert "period_start_utc" in csv and len(csv.strip().splitlines())==11
    image=Image.open(BytesIO(client.get("/api/export/png?product=diff").content))
    assert image.width>=700


def test_diff_agreement_and_static(client):
    assert client.get("/api/diff?bbox=100,-2,101,-1").json()["features"]
    assert client.get("/api/agreement?bbox=100,-2,101,-1").json()["features"]
    assert client.get("/").status_code==200
    assert client.get("/static/map.js").status_code==200


def test_failed_registration_preserves_sources(client,tmp_path):
    before=app.state.engine[0].version
    bad=tmp_path/"invalid.nc"
    bad.write_text("not netcdf",encoding="utf-8")
    response=client.post("/api/products/register",json={"product":"A","path":str(bad),"name":"Invalid"})
    assert response.status_code==202
    job=client.get(response.json()["status_url"]).json()
    assert job["status"]=="failed"
    assert app.state.engine[0].version==before
