import csv
import hashlib
import io
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import yaml
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, ORJSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.core.settings import CATEGORIES, CONFIG_PATH, NOTE, ROOT, load_config
from app.services.analysis import Analysis
from app.services.loader import DataStore, LOCK, resolve_source
from app.services.render import Renderer


def engine(config=None):
    store = DataStore(config)
    store.prepare()
    analysis = Analysis(store)
    return store, analysis, Renderer(analysis)


@asynccontextmanager
async def lifespan(app):
    app.state.engine = await run_in_threadpool(engine)
    app.state.jobs = {}
    yield
    app.state.engine[0].close()


app = FastAPI(title="Rainfall NC Compare", version="1.0.0", default_response_class=ORJSONResponse, lifespan=lifespan)
app.add_middleware(GZipMiddleware,minimum_size=1000)
app.mount("/static",StaticFiles(directory=ROOT/"frontend"),name="static")


@app.exception_handler(ValueError)
async def invalid(request, error):
    return ORJSONResponse(status_code=422,content={"detail":str(error)})


def services():
    return app.state.engine


def product_check(product):
    if product not in ("A","B","diff","agreement"):
        raise ValueError("Produk harus A, B, diff atau agreement")


def checked(time,agg,threshold):
    if not np.isfinite(threshold) or threshold<0:
        raise ValueError("Ambang harus finite dan >= 0 mm")
    services()[1].frame(time,agg)


@app.get("/")
def index():
    return FileResponse(ROOT/"frontend/index.html")


@app.get("/api/products")
def products():
    store,analysis,renderer = services()
    # Source changes are explicitly checked when metadata is refreshed.
    if store.prepare():
        analysis.clear()
        renderer.tile.cache_clear()
    return {"products":store.metadata(),"note":NOTE,"grid":store.config.get("grid"),
            "time_alignment":store.config.get("time_alignment","exact"),
            "time_note":analysis.periods("native")[0]["time_note"],
            "overlays":store.config.get("overlays",[])}


@app.get("/api/meta/{product}")
def meta(product:str):
    if product not in ("A","B"):
        raise ValueError("Metadata tersedia untuk A atau B")
    return services()[0].metadata()[product]


@app.get("/api/categories")
def categories(profile:str="daily"):
    if profile not in CATEGORIES:
        raise ValueError("Profil harus daily atau hourly")
    return {"profile":profile,"categories":CATEGORIES[profile],"threshold_operator":">",
            "note":"Kategori harian BMKG; agregasi multi-hari memakai batas numerik yang sama, bukan intensitas harian."}


@app.get("/api/periods")
def periods(agg:str="daily"):
    return {"agg":agg,"periods":services()[1].periods(agg),"timezone":"UTC"}


@app.get("/api/tiles/{product}/{z}/{x}/{y}.png")
def tile(request:Request,product:str,z:int,x:int,y:int,time:int=0,agg:str="daily",threshold:float=50,
         muted:bool=True,hidden:str="",palette:str="default",v:str=""):
    checked(time,agg,threshold)
    product_check(product)
    if palette not in ("default","accessible") or any(not h.isdigit() or int(h)>5 for h in filter(None,hidden.split(","))):
        raise ValueError("Palet/kategori tidak valid")
    store,_,renderer = services()
    content = renderer.tile(product,z,x,y,time,agg,threshold,muted,hidden,palette,store.version)
    etag = '"'+hashlib.sha256(content).hexdigest()[:24]+'"'
    headers = {"ETag":etag,"Cache-Control":"public, max-age=60"}
    if request.headers.get("if-none-match")==etag:
        return Response(status_code=304,headers=headers)
    return Response(content,media_type="image/png",headers=headers)


@app.get("/api/grid/{product}")
def grid(product:str,time:int=0,agg:str="daily",threshold:float=50,bbox:str|None=None,limit:int=Query(50000,ge=1,le=50000)):
    product_check(product)
    checked(time,agg,threshold)
    return services()[2].grid(product,time,agg,threshold,bbox,limit)


@app.get("/api/diff")
def diff(time:int=0,agg:str="daily",bbox:str|None=None,limit:int=Query(50000,ge=1,le=50000)):
    checked(time,agg,50)
    return services()[2].grid("diff",time,agg,50,bbox,limit,all_cells=True)


@app.get("/api/agreement")
def agreement(time:int=0,agg:str="daily",threshold:float=50,bbox:str|None=None,limit:int=Query(50000,ge=1,le=50000)):
    checked(time,agg,threshold)
    return services()[2].grid("agreement",time,agg,threshold,bbox,limit,all_cells=True)


@app.get("/api/compare/summary")
def summary(time:int=0,agg:str="daily",threshold:float=50,bbox:str|None=None,start:int|None=None,end:int|None=None):
    checked(time,agg,threshold)
    analysis = services()[1]
    if start is not None or end is not None:
        start = 0 if start is None else start
        end = len(analysis.periods(agg))-1 if end is None else end
        if start<0 or end<start or end>=len(analysis.periods(agg)):
            raise ValueError("Rentang start/end tidak valid (indeks periode inklusif)")
        return {"summaries":[analysis.summary(i,agg,threshold,bbox) for i in range(start,end+1)],"note":NOTE}
    return analysis.summary(time,agg,threshold,bbox)


class AreaRequest(BaseModel):
    geometry: dict
    time: int = 0
    agg: str = "daily"
    threshold: float = Field(50,ge=0,allow_inf_nan=False)


@app.post("/api/compare/area")
def area(body:AreaRequest):
    checked(body.time,body.agg,body.threshold)
    return services()[1].summary(body.time,body.agg,body.threshold,geometry=body.geometry)


@app.get("/api/compare/point")
def point(lat:float,lon:float,time:int=0,agg:str="daily"):
    return services()[1].point(lat,lon,time,agg)


@app.get("/api/compare/timeseries")
def timeseries(lat:float,lon:float,threshold:float=50):
    checked(0,"native",threshold)
    return services()[1].timeseries(lat,lon,threshold)


@app.get("/api/export/{format}")
def export(format:str,product:str="A",time:int=0,agg:str="daily",threshold:float=50,bbox:str|None=None):
    checked(time,agg,threshold)
    product_check(product)
    _,analysis,renderer = services()
    filename=f"rainfall_{product}_{agg}_{time}_gt{threshold:g}.{format}"
    headers={"Content-Disposition":f'attachment; filename="{filename}"'}
    if format=="geojson":
        result=renderer.grid(product,time,agg,threshold,bbox)
        return ORJSONResponse(result,media_type="application/geo+json",headers=headers)
    if format=="png":
        return Response(renderer.png(product,time,agg,threshold,bbox),media_type="image/png",headers=headers)
    if format=="csv":
        buffer=io.StringIO()
        writer=csv.writer(buffer)
        writer.writerow(["product","name","period_start_utc","period_end_utc","hours","partial","agg","threshold_mm",
                         "comparison_date_start","comparison_date_end","time_alignment","windows_match","time_note",
                         "grids_exceed","area_km2","area_pct","mean_mm","max_mm","p95_mm","p99_mm",
                         "both","only_A","only_B","neither","sensitivity_ratio_A_over_B",
                         "heavy_mean_difference_mm","heavy_relative_bias_pct","bbox","cache_version"])
        for i,p in enumerate(analysis.periods(agg)):
            record=analysis.summary(i,agg,threshold,bbox)
            for k,m in record["products"].items():
                source=p["source_periods"][k]
                writer.writerow([k,m["name"],source["start"],source["end"],source["hours"],p["partial"],agg,threshold,
                                 p["comparison_date_start"],p["comparison_date_end"],p["time_alignment"],p["windows_match"],p["time_note"],
                                 *[m[c] for c in ("grids_exceed","area_km2","area_pct","mean_mm","max_mm","p95_mm","p99_mm")],
                                 *[record["agreement"][c] for c in ("both","only_A","only_B","neither")],
                                 record["sensitivity_ratio_A_over_B"],record["heavy_mean_difference_mm"],record["heavy_relative_bias_pct"],
                                 bbox or "full domain",services()[0].version])
        return Response("\ufeff"+buffer.getvalue(),media_type="text/csv; charset=utf-8",headers=headers)
    raise ValueError("Format ekspor: geojson, csv, png")


class Registration(BaseModel):
    product: str
    path: str
    name: str
    variable: str|None = None
    units: str|None = None
    period_hours: float|None = Field(None,gt=0,allow_inf_nan=False)
    cumulative: bool = False
    time_label: str = "end"
    time_alignment: str|None = None
    grid: str = "coarser"


def register_job(job_id,body):
    jobs=app.state.jobs
    jobs[job_id]["status"]="processing"
    try:
        # Serialize writes; publish only a complete, validated pair.
        with LOCK:
            config=load_config()
            config["products"][body.product]={k:v for k,v in body.model_dump().items() if k not in ("product","grid","time_alignment") and v is not None}
            config["grid"]=body.grid
            if body.time_alignment is not None:
                config["time_alignment"]=body.time_alignment
            new_engine=engine(config)
            temporary=CONFIG_PATH.with_suffix(".yaml.tmp")
            temporary.write_text(yaml.safe_dump(config,allow_unicode=True,sort_keys=False),encoding="utf-8")
            temporary.replace(CONFIG_PATH)
            app.state.engine=new_engine
            # In-flight requests may still read old Zarr handles; don't close them here.
            jobs[job_id].update(status="complete",version=new_engine[0].version)
    except Exception as error:
        jobs[job_id].update(status="failed",detail=str(error))


@app.post("/api/products/register",status_code=202)
def register(body:Registration,background:BackgroundTasks):
    if body.product not in ("A","B") or body.grid not in ("A","B","coarser"):
        raise ValueError("product A/B dan grid A/B/coarser diperlukan")
    if body.time_label not in ("start","end") or body.time_alignment not in (None,"exact","date"):
        raise ValueError("time_label: start/end; time_alignment: exact/date")
    path=resolve_source({"path":body.path})
    if path.suffix.lower()!=".nc":
        raise ValueError("Sumber harus file NetCDF .nc")
    job_id=uuid.uuid4().hex
    app.state.jobs[job_id]={"status":"queued","product":body.product}
    background.add_task(register_job,job_id,body)
    return {"job_id":job_id,"status":"queued","status_url":f"/api/jobs/{job_id}"}


@app.get("/api/jobs/{job_id}")
def job(job_id:str):
    if job_id not in app.state.jobs:
        raise HTTPException(404,"Job tidak ditemukan")
    return app.state.jobs[job_id]


@app.get("/api/overlays/{index}")
def overlay(index:int):
    overlays=services()[0].config.get("overlays",[])
    if index<0 or index>=len(overlays):
        raise HTTPException(404,"Layer tidak dikonfigurasi")
    path=Path(overlays[index]["path"])
    path=path if path.is_absolute() else ROOT/path
    if not path.is_file():
        raise HTTPException(404,"File layer tidak ditemukan")
    return FileResponse(path,media_type="application/geo+json")
