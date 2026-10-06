"""Live Chromium checks. Run uvicorn first, then python scripts/browser_check.py."""
from pathlib import Path
import json
import atexit
import subprocess
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright
from PIL import Image
import numpy as np

ROOT=Path(__file__).resolve().parents[1]


if __name__=="__main__":
    server=subprocess.Popen([sys.executable,"-B","-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8000"],
                            cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    atexit.register(server.terminate)
    for attempt in range(100):
        try:
            urllib.request.urlopen("http://127.0.0.1:8000/api/products",timeout=1)
            break
        except OSError:
            time.sleep(.1)
    else:
        raise RuntimeError("Uvicorn tidak siap")
    metadata=json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/products"))
    baseline=json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/compare/summary?start=0&end=4"))["summaries"]
    valid_grids=metadata["products"]["A"]["shape"][1]*metadata["products"]["A"]["shape"][2]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=["--enable-webgl","--use-gl=angle","--use-angle=swiftshader","--enable-unsafe-swiftshader"])
        page=browser.new_page(viewport={"width":1440,"height":960},device_scale_factor=1)
        errors=[]
        http_errors=[]
        page.on("pageerror",lambda error:errors.append(str(error)))
        page.on("console",lambda message:print("BROWSER",message.text) if message.type=="error" else None)
        page.on("response",lambda response:http_errors.append((response.status,response.url)) if response.status>=400 and "127.0.0.1" in response.url else None)
        page.goto("http://127.0.0.1:8000/?basemap=none",wait_until="networkidle",timeout=60000)
        try:
            page.wait_for_function("window.rainfall && window.allSummaries && window.lastSummary",timeout=30000)
        except Exception:
            print("ERRORS",errors,"UI",page.locator("#error-text").inner_text())
            page.screenshot(path=str(ROOT/"screenshots/startup-error.png"))
            raise
        assert page.locator("#error").is_hidden(),page.locator("#error-text").inner_text()
        assert page.locator("#count-a").inner_text()==str(baseline[0]["products"]["A"]["grids_exceed"])
        assert page.locator("#metric-name-b").inner_text()=="B · "+metadata["products"]["B"]["name"]
        assert page.locator("#product-subtitle").inner_text()==metadata["products"]["A"]["name"]+" × "+metadata["products"]["B"]["name"]
        if metadata["time_alignment"]=="date":
            assert "label tanggal UTC" in page.locator("#time-label").inner_text()
            assert "jendela akumulasi" in page.locator("#time-alignment-note").inner_text()
            assert "18.00" in page.locator("#summary-period").inner_text()
            assert "00.00" in page.locator("#summary-period").inner_text()
        page.locator("#next").click()
        page.wait_for_function("window.lastSummary.time === 1")
        assert page.locator("#count-b").inner_text()==str(baseline[1]["products"]["B"]["grids_exceed"])
        for mode in ("single","side","swipe","diff","agreement"):
            page.locator(f"button[data-mode='{mode}']").click()
            page.wait_for_function("window.rainfall.maps.every(m => m.loaded())",timeout=30000)
            assert page.locator("#map-stage").get_attribute("data-mode")==mode
        page.locator("button[data-mode='side']").click()
        page.evaluate("rainfall.maps[0].jumpTo({center:[110,-2],zoom:5})")
        page.wait_for_function("Math.abs(rainfall.maps[0].getZoom()-rainfall.maps[1].getZoom())<0.01")
        page.locator("button[data-threshold='20']").click()
        page.wait_for_function("window.lastSummary.threshold_mm === 20")
        assert page.locator("#threshold-value").inner_text()=="20"
        page.locator("button[data-threshold='50']").click()
        page.wait_for_function("window.lastSummary.threshold_mm === 50")
        page.locator("#aggregation").select_option("3daily")
        page.wait_for_function("window.lastSummary.agg === '3daily'")
        page.locator("#previous").click()
        page.wait_for_function("window.lastSummary.time === 0 && window.lastSummary.agg === '3daily'")
        page.locator("#next").click()
        page.wait_for_function("window.lastSummary.time === 1 && window.lastSummary.agg === '3daily'")
        assert "Parsial" in page.locator("#partial-label").inner_text()
        page.locator("#aggregation").select_option("daily")
        page.wait_for_function("window.lastSummary.agg === 'daily'")
        page.evaluate("rainfall.maps[0].jumpTo({center:[105,-2],zoom:8})")
        page.wait_for_function("rainfall.maps.every(m=>m.loaded())",timeout=30000)
        rect=page.locator("#map-a").bounding_box()
        pos=page.evaluate("rainfall.maps[0].project([105,-2])")
        page.mouse.move(rect["x"]+pos["x"],rect["y"]+pos["y"])
        page.wait_for_selector("#tooltip:not([hidden])")
        assert "ECMWF" in page.locator("#tooltip").inner_text()
        page.locator("#map-a canvas").click(position={"x":pos["x"],"y":pos["y"]})
        page.wait_for_function("rainfall.state.point && !document.getElementById('point-chart-wrap').hidden")
        assert "105.00" in page.locator("#point-label").inner_text()
        for draw in ("rectangle","polygon","circle"):
            page.locator(f"button[data-draw='{draw}']").click()
            coords=[[104.8,-2.2],[105.2,-1.8]] if draw=="rectangle" else [[105,-2],[105.3,-2]] if draw=="circle" else [[104.8,-2.2],[105.2,-2.2],[105.2,-1.8]]
            for coord in coords:
                pos=page.evaluate("coord => rainfall.maps[0].project(coord)",coord)
                page.locator("#map-a canvas").click(position={"x":pos["x"],"y":pos["y"]})
            if draw=="polygon":
                page.locator("#finish-draw").click()
            page.wait_for_function("n => window.lastSummary.parameters.geometry && window.lastSummary.valid_grids < n",arg=valid_grids)
            assert page.locator("#error").is_hidden()
            page.locator("#clear-area").click()
            page.wait_for_function("n => window.lastSummary.valid_grids === n",arg=valid_grids)
        page.evaluate("rainfall.setArea({type:'Polygon',coordinates:[[[99,-6],[106,-6],[106,1],[99,1],[99,-6]]]})")
        page.wait_for_function("n => window.lastSummary.parameters.geometry && window.lastSummary.valid_grids < n",arg=valid_grids)
        assert page.locator("#area-badge").inner_text()=="Area terpilih"
        page.locator("#clear-area").click()
        page.wait_for_function("n => window.lastSummary.valid_grids === n",arg=valid_grids)
        page.locator("#fit-domain").click()
        page.wait_for_function("rainfall.maps.every(m=>m.loaded())",timeout=30000)
        page.evaluate("document.getElementById('controls').scrollTop=0")
        page.screenshot(path=str(ROOT/"screenshots/desktop.png"),full_page=True)
        with page.expect_download(timeout=30000) as event:
            page.locator("#export-png").click()
        event.value.save_as(str(ROOT/"screenshots/map-export.png"))
        exported=np.asarray(Image.open(ROOT/"screenshots/map-export.png"))
        assert (exported[120:-300,:100,:3]<230).any(),"Exported map canvas is blank"
        with page.expect_download(timeout=30000) as event:
            page.locator("button[data-export='csv']").click()
        assert event.value.suggested_filename.endswith(".csv")
        saved_url=page.url
        page.reload(wait_until="networkidle")
        page.wait_for_function("window.rainfall && window.lastSummary")
        assert "mode=side" in page.url
        page.set_viewport_size({"width":390,"height":844})
        page.reload(wait_until="networkidle")
        page.wait_for_function("window.rainfall && window.lastSummary")
        assert page.locator("#controls").is_hidden()
        assert page.locator("#summary-panel").is_hidden()
        page.locator("#toggle-left").click()
        assert page.locator("#controls").is_visible()
        page.locator("#toggle-right").click()
        assert page.locator("#controls").is_hidden()
        assert page.locator("#summary-panel").is_visible()
        page.locator("#toggle-right").click()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(ROOT/"screenshots/mobile.png"),full_page=True)
        page.set_viewport_size({"width":320,"height":740})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert not errors,errors
        assert not http_errors,http_errors
        page.set_viewport_size({"width":1440,"height":960})
        page.goto("http://127.0.0.1:8000/?basemap=light&mode=single&product=B&time=1",wait_until="networkidle",timeout=60000)
        page.wait_for_function("window.rainfall && window.lastSummary",timeout=30000)
        page.screenshot(path=str(ROOT/"screenshots/desktop-basemap.png"),full_page=True)
        print(json.dumps({"status":"PASS","checks":["five modes","real baseline","threshold","sync pan/zoom",
                         "aggregation/partial","hover/point chart","rectangle/polygon/circle area","URL restore","PNG/CSV download",
                         "mobile panels","320px overflow"],"browser_errors":errors},indent=2))
        browser.close()
