"""Benchmark local ASGI API, repeatable on the real configured files."""
import json
import sys
import time
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.main import app


if __name__=="__main__":
    report={}
    with TestClient(app) as client:
        for name,url in {
            "tile":"/api/tiles/B/4/13/8.png?time=1",
            "grid":"/api/grid/B?time=1",
            "summary":"/api/compare/summary?time=1",
            "full_forecast_summary":"/api/compare/summary?start=0&end=4",
        }.items():
            timings=[]
            for i in range(31):
                start=time.perf_counter();response=client.get(url);elapsed=(time.perf_counter()-start)*1000
                response.raise_for_status();timings.append(elapsed)
            report[name]={"first_ms":round(timings[0],2),"warm_p50_ms":round(float(np.median(timings[1:])),2),
                          "warm_p95_ms":round(float(np.percentile(timings[1:],95)),2)}
    print(json.dumps(report,indent=2))
