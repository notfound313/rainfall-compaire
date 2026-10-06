"""Raster tiles and vector cells on the shared grid."""
from functools import lru_cache
from io import BytesIO

import numpy as np
from PIL import Image, ImageDraw

from app.core.settings import CATEGORIES
from app.services.analysis import cell_edges, classify

AGREEMENT_COLORS = ["#dbe3ea", "#f4a261", "#3483c9", "#71368a"]
ALTERNATIVE = ["#cbd5e1", "#56b4e9", "#009e73", "#e69f00", "#d55e00", "#cc79a7"]


def rgb(color):
    return tuple(int(color[i:i+2],16) for i in (1,3,5))


class Renderer:
    def __init__(self, analysis):
        self.analysis = analysis

    def colors(self, product, time, agg, threshold, muted=True, hidden="", palette="default"):
        a, b = self.analysis.frame(time, agg)
        finite = np.isfinite(a) & np.isfinite(b)
        if product in ("A", "B"):
            values = a if product == "A" else b
            profile = "hourly" if self.analysis.periods(agg)[time]["hours"] <= 1 else "daily"
            indices = classify(values, profile)
            colors = (ALTERNATIVE[:len(CATEGORIES[profile])] if palette == "accessible"
                      else [c["color"] for c in CATEGORIES[profile]])
            table = np.array([rgb(c) for c in colors], dtype=np.uint8)
            result = np.zeros((*values.shape,4),dtype=np.uint8)
            result[:,:,:3] = table[np.clip(indices,0,len(colors)-1)]
            result[:,:,3] = np.where(values > threshold, 235, 75 if muted else 0)
            for index in filter(None, hidden.split(",")):
                result[indices == int(index),3] = 0
            result[~np.isfinite(values),3] = 0
        elif product == "diff":
            values = a-b
            strength = np.minimum(abs(values)/100,1)
            white = np.array([245,245,245])
            red, blue = np.array([192,44,48]), np.array([39,101,182])
            target = np.where((values >= 0)[:,:,None],red,blue)
            result = np.zeros((*values.shape,4),dtype=np.uint8)
            result[:,:,:3] = np.nan_to_num(white+(target-white)*strength[:,:,None]).astype(np.uint8)
            result[:,:,3] = np.where(np.maximum(a,b)>threshold,235,110 if muted else 0)
            result[~finite,3] = 0
        elif product == "agreement":
            indices = (a>threshold).astype(int)+2*(b>threshold).astype(int)
            result = np.zeros((*a.shape,4),dtype=np.uint8)
            result[:,:,:3] = np.array([rgb(c) for c in AGREEMENT_COLORS],dtype=np.uint8)[indices]
            result[:,:,3] = np.where(indices>0,235,60 if muted else 0)
            result[~finite,3] = 0
        else:
            raise ValueError("Produk harus A, B, diff atau agreement")
        return result

    @lru_cache(maxsize=384)
    def tile(self, product, z, x, y, time, agg, threshold, muted, hidden, palette, version):
        if z < 0 or z > 18 or x < 0 or y < 0 or x >= 2**z or y >= 2**z:
            raise ValueError("Koordinat tile tidak valid")
        pixels = self.colors(product,time,agg,threshold,muted,hidden,palette)
        ds = self.analysis.store.dataset
        n = 2**z
        lon = (x+(np.arange(256)+.5)/256)/n*360-180
        merc_y = np.pi*(1-2*(y+(np.arange(256)+.5)/256)/n)
        lat = np.rad2deg(np.arctan(np.sinh(merc_y)))
        le, oe = cell_edges(ds.lat.values), cell_edges(ds.lon.values)
        li, oi = np.searchsorted(le,lat,side="right")-1, np.searchsorted(oe,lon,side="right")-1
        valid = ((li>=0)&(li<len(ds.lat)))[:,None] & ((oi>=0)&(oi<len(ds.lon)))[None,:]
        rgba = pixels[np.clip(li,0,len(ds.lat)-1)[:,None],np.clip(oi,0,len(ds.lon)-1)[None,:]].copy()
        rgba[~valid,3] = 0
        buffer = BytesIO()
        Image.fromarray(rgba).save(buffer,format="PNG")
        return buffer.getvalue()

    def grid(self, product="A", time=0, agg="daily", threshold=50, bbox=None, limit=50000, geometry=None, all_cells=False):
        a,b = self.analysis.frame(time,agg)
        mask = self.analysis.mask(bbox,geometry) & np.isfinite(a) & np.isfinite(b)
        if not all_cells:
            mask &= ((a>threshold) if product=="A" else (b>threshold) if product=="B" else ((a>threshold)|(b>threshold)))
        ds = self.analysis.store.dataset
        rows,cols = np.nonzero(mask)
        total = len(rows)
        rows,cols = rows[:limit],cols[:limit]
        le,oe = cell_edges(ds.lat.values),cell_edges(ds.lon.values)
        rgba = self.colors(product,time,agg,threshold)
        profile = "hourly" if self.analysis.periods(agg)[time]["hours"] <= 1 else "daily"
        ca,cb = classify(a,profile),classify(b,profile)
        features = []
        for i,j in zip(rows,cols):
            ring = [[float(oe[j]),float(le[i])],[float(oe[j+1]),float(le[i])],
                    [float(oe[j+1]),float(le[i+1])],[float(oe[j]),float(le[i+1])],[float(oe[j]),float(le[i])]]
            features.append({"type":"Feature","id":int(i*len(ds.lon)+j),
                             "geometry":{"type":"Polygon","coordinates":[ring]},
                             "properties":{"lat":float(ds.lat[i]),"lon":float(ds.lon[j]),
                                           "A":float(a[i,j]),"B":float(b[i,j]),"diff":float(a[i,j]-b[i,j]),
                                           "category_A":CATEGORIES[profile][ca[i,j]]["label"],
                                           "category_B":CATEGORIES[profile][cb[i,j]]["label"],
                                           "category_index":int(ca[i,j] if product=="A" else cb[i,j]),
                                           "agreement":int(a[i,j]>threshold)+2*int(b[i,j]>threshold),
                                           "color":"#"+"".join(f"{v:02x}" for v in rgba[i,j,:3])}})
        return {"type":"FeatureCollection","features":features,
                "metadata":{"product":product,"time":time,"agg":agg,"threshold_mm":threshold,
                            "product_names":{key:self.analysis.store.config["products"][key]["name"] for key in ("A","B")},
                            "bbox":bbox,"geometry":geometry,"period":self.analysis.periods(agg)[time],
                            "total_features":total,"returned_features":len(features),"truncated":total>limit,
                            "selection":"cell centre","cache_version":self.analysis.store.version}}

    def png(self, product, time, agg, threshold, bbox=None):
        ds = self.analysis.store.dataset
        rgba = self.colors(product,time,agg,threshold)
        mask = self.analysis.mask(bbox)
        rgba[~mask,3] = 0
        ys,xs = np.nonzero(mask)
        if not len(ys):
            raise ValueError("Area ekspor tidak memiliki grid")
        rgba = rgba[ys.min():ys.max()+1,xs.min():xs.max()+1][::-1]
        image = Image.fromarray(rgba).resize((rgba.shape[1]*4,rgba.shape[0]*4),Image.Resampling.NEAREST)
        canvas = Image.new("RGB",(max(700,image.width),image.height+160),"#ffffff")
        canvas.paste(image,(0,128),image)
        draw = ImageDraw.Draw(canvas)
        draw.text((14,12),f"Rainfall NC Compare | {product} | {agg} | threshold > {threshold:g} mm",fill="#172c3e")
        draw.text((14,32),self.analysis.periods(agg)[time]["label"],fill="#172c3e")
        period = self.analysis.periods(agg)[time]
        for i,key in enumerate(("A","B")):
            window = period["source_periods"][key]
            name = self.analysis.store.config["products"][key]["name"]
            draw.text((14,52+18*i),f"{key} - {name}: {window['start']} to {window['end']}",fill="#172c3e")
        if period["time_note"]:
            draw.text((14,94),period["time_note"],fill="#8d5822")
        draw.text((14,canvas.height-24),"Grid rainfall (mm); UTC; no basemap. Source names/windows shown above.",fill="#172c3e")
        buffer = BytesIO()
        canvas.save(buffer,format="PNG")
        return buffer.getvalue()
