from types import SimpleNamespace

import numpy as np
import pytest
import xarray as xr

from app.services.analysis import Analysis, cell_areas, classify
from app.services.loader import align_time, convert_mm, normalize, regrid_pair


@pytest.mark.parametrize("units,value,hours,expected",[
    ("mm",12,24,12),("m",0.012,24,12),("kg m**-2",12,24,12),
    ("mm/h",2,6,12),("mm/day",12,6,3),("kg m⁻² s⁻¹",0.001,6,21.6),
])
def test_conversion(units,value,hours,expected):
    assert float(convert_mm(np.array(value),units,hours))==pytest.approx(expected)


def test_ambiguous_units_rejected():
    with pytest.raises(ValueError,match="Satuan"):
        convert_mm(np.array(1),"unknown",24)


def test_category_boundaries_and_nodata():
    values=np.array([0,4.99,5,19.99,20,49.99,50,99.99,100,150,150.01,np.nan])
    np.testing.assert_array_equal(classify(values),[0,0,1,1,2,2,3,3,4,4,5,-1])


def source():
    return xr.Dataset({"tp":(("step","latitude","longitude"),np.array([[[.001,-.000001],[.002,.003]],[[.003,.002],[.005,.007]]]),{"units":"m"})},
                      coords={"step":np.array([24,48],dtype="timedelta64[h]"),
                              "latitude":[1,0],"longitude":[350,351],
                              "time":np.datetime64("2026-01-01"),
                              "valid_time":("step",np.array(["2026-01-02","2026-01-03"],dtype="datetime64[ns]"))})


def test_normalization_cumulative_and_coordinates():
    result=normalize(source(),{"cumulative":True})
    np.testing.assert_array_equal(result.lat,[0,1])
    np.testing.assert_array_equal(result.lon,[-10,-9])
    assert result.attrs["negative_values_clipped"]==1
    assert result.sel(time="2026-01-03",lat=0,lon=-10).item()==pytest.approx(3)
    assert result.min().item()==0
    np.testing.assert_array_equal(result.period_hours,[24,24])


def test_significant_negative_rejected():
    ds=source();ds.tp.values[0,0,0]=-.001
    with pytest.raises(ValueError,match="negatif"):
        normalize(ds,{"period_hours":24})


def test_noncontiguous_time_rejected():
    ds=source().assign_coords(valid_time=("step",np.array(["2026-01-02","2026-01-04"],dtype="datetime64[ns]")))
    with pytest.raises(ValueError,match="kontinu"):
        normalize(ds,{"period_hours":24})


def linear_grid(coords,offset=0):
    lat,lon=np.meshgrid(coords,coords,indexing="ij")
    return xr.DataArray((lat+2*lon+offset)[None,:,:],dims=("time","lat","lon"),
                        coords={"time":[np.datetime64("2026-01-02")],"lat":coords,"lon":coords,
                                "accumulation_start":("time",[np.datetime64("2026-01-01")]),
                                "period_hours":("time",[24])})


def test_bilinear_regrid_analytic_field():
    a,b=regrid_pair(linear_grid(np.array([0,1,2])),linear_grid(np.array([0,.5,1,1.5,2]),10))
    assert a.shape==b.shape==(1,3,3)
    np.testing.assert_allclose(b-a,10)


def test_regrid_excludes_extrapolation():
    a,b=regrid_pair(linear_grid(np.array([0,1,2,3])),linear_grid(np.array([1,2,3,4])))
    np.testing.assert_array_equal(a.lat,[1,2,3])
    np.testing.assert_array_equal(b.lon,[1,2,3])


def test_cell_area_varies_with_latitude():
    areas=cell_areas(np.array([0,60]),np.array([0,1]))
    assert areas[0,0]>areas[1,0]
    assert (areas>0).all()


def analysis_for_values(a,b):
    dims=("time","lat","lon")
    ds=xr.Dataset({"A":(dims,np.array([a],dtype=float)),"B":(dims,np.array([b],dtype=float))},
                  coords={"time":[np.datetime64("2026-01-02")],"lat":[0,1],"lon":[100,101],
                          "accumulation_start":("time",[np.datetime64("2026-01-01")]),"period_hours":("time",[24])})
    store=SimpleNamespace(dataset=ds,config={"products":{"A":{"name":"A"},"B":{"name":"B"}},"grid":"coarser"},version="test")
    return Analysis(store)


def test_metrics_strict_threshold_common_mask_and_zero_ratio():
    analysis=analysis_for_values([[50,51],[np.nan,80]],[[50,0],[100,np.nan]])
    result=analysis.summary()
    assert result["valid_grids"]==2
    assert result["products"]["A"]["grids_exceed"]==1
    assert result["products"]["B"]["grids_exceed"]==0
    assert result["sensitivity_ratio_A_over_B"] is None
    assert result["agreement"]=={"both":0,"only_A":1,"only_B":0,"neither":1}
    assert result["heavy_mean_difference_mm"]==51
    assert result["heavy_relative_bias_pct"] is None


def test_empty_bbox_and_polygon_mask():
    analysis=analysis_for_values([[70,70],[70,70]],[[0,0],[0,0]])
    assert analysis.summary(bbox="1,1,2,2")["valid_grids"]==0
    polygon={"type":"Polygon","coordinates":[[[99.9,-.1],[100.1,-.1],[100.1,.1],[99.9,.1],[99.9,-.1]]]}
    result=analysis.summary(geometry=polygon)
    assert result["valid_grids"]==1
    assert result["products"]["A"]["grids_exceed"]==1


def date_source(hour=0,label="end"):
    times=np.array(["2026-10-01","2026-10-02"],dtype="datetime64[ns]")+np.timedelta64(hour,"h")
    ds=xr.Dataset({"rain":(("time","lat","lon"),np.arange(8).reshape(2,2,2),{"units":"mm/day"})},
                  coords={"time":times,"lat":[0,1],"lon":[100,101]})
    return normalize(ds,{"variable":"rain","period_hours":24,"time_label":label})


def test_time_label_start_preserves_original_dates_and_amounts():
    rain=date_source(0,"start")
    assert rain.accumulation_start.values[0]==np.datetime64("2026-10-01T00:00")
    assert rain.time.values[0]==np.datetime64("2026-10-02T00:00")
    assert rain.comparison_date.values[0]==np.datetime64("2026-10-01")
    np.testing.assert_array_equal(rain.values,np.arange(8).reshape(2,2,2))


def test_date_alignment_preserves_different_windows():
    a,b=align_time(date_source(18,"end"),date_source(0,"start"),"date")
    np.testing.assert_array_equal(a.time,b.time)
    np.testing.assert_array_equal(a.comparison_date,[np.datetime64("2026-10-01"),np.datetime64("2026-10-02")])
    assert a.accumulation_start_A.values[0]==np.datetime64("2026-09-30T18:00")
    assert a.accumulation_end_A.values[0]==np.datetime64("2026-10-01T18:00")
    assert b.accumulation_start_B.values[0]==np.datetime64("2026-10-01T00:00")
    assert b.accumulation_end_B.values[0]==np.datetime64("2026-10-02T00:00")
    np.testing.assert_array_equal(a.values,b.values)


def test_exact_mode_rejects_shifted_windows_with_details():
    with pytest.raises(ValueError,match="A:.*B:"):
        align_time(date_source(18,"end"),date_source(0,"start"),"exact")


def test_exact_mode_accepts_different_label_conventions_for_same_window():
    start=date_source(0,"start")
    end=start.copy().assign_coords(comparison_date=("time",start.time.values.astype("datetime64[D]")))
    a,b=align_time(start,end,"exact")
    np.testing.assert_array_equal(a.time,b.time)
    np.testing.assert_array_equal(a.comparison_date,b.comparison_date)


def test_date_mode_rejects_subdaily_and_duplicate_dates():
    hourly=date_source().assign_coords(period_hours=("time",[1,1]))
    with pytest.raises(ValueError,match="24 jam"):
        align_time(hourly,hourly,"date")
    duplicate=date_source().assign_coords(comparison_date=("time",[np.datetime64("2026-10-01")]*2))
    with pytest.raises(ValueError,match="beberapa interval"):
        align_time(duplicate,duplicate,"date")
