import * as maplibregl from '/static/node_modules/maplibre-gl/dist/maplibre-gl.mjs';
const $ = id => document.getElementById(id);
const qs = new URLSearchParams(location.search);
const number = (key, fallback) => Number.isFinite(Number(qs.get(key))) && qs.has(key) ? Number(qs.get(key)) : fallback;
const state = {
  mode: ['single','side','swipe','diff','agreement'].includes(qs.get('mode')) ? qs.get('mode') : 'side',
  product: qs.get('product') === 'B' ? 'B' : 'A',
  agg: ['native','daily','3daily','monthly','max'].includes(qs.get('agg')) ? qs.get('agg') : 'daily',
  time: Math.max(0, Math.floor(number('time',0))), threshold: Math.min(200,Math.max(10,number('threshold',50))),
  opacity: Math.min(100,Math.max(10,number('opacity',80))), muted: qs.get('muted') !== 'false',
  palette: qs.get('palette') === 'accessible' ? 'accessible' : 'default',
  basemap: ['light','dark','satellite','none'].includes(qs.get('basemap')) ? qs.get('basemap') : 'light',
  timezone: qs.get('tz') === 'Asia/Jakarta' ? 'Asia/Jakarta' : 'UTC',
  hidden: new Set((qs.get('hidden') || '').split(',').filter(x => /^\d$/.test(x)).map(Number)),
  geometry: null, point: null, periods: [], metadata: null, categories: [], swipe: number('swipe',50),
  drawing: null, vertices: [], activeOverlays: new Set(), speed: 1,
};
let maps = [], overviewChart, pointChart, playback, syncBusy=false, revision=0, hoverController, gridRevision=0;
let refreshTimer, hoverTimer, toastTimer, fitted=false;
const escapeHtml = text => String(text).replace(/[&<>"']/g,c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = (value,digits=1) => value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('id-ID',{maximumFractionDigits:digits});
const date = value => new Intl.DateTimeFormat('id-ID',{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',hourCycle:'h23',timeZone:state.timezone}).format(new Date(value));
const zone = () => state.timezone === 'UTC' ? 'UTC' : 'WIB';
const productName = key => state.metadata?.products[key]?.name || `Produk ${key}`;
const productLabel = key => `${key} · ${productName(key)}`;
const labelDate = value => new Intl.DateTimeFormat('id-ID',{day:'2-digit',month:'short',year:'numeric',timeZone:'UTC'}).format(new Date(value+'T00:00:00Z'));
function periodLabel(p){return p.time_alignment==='date'?`${labelDate(p.comparison_date_start)}${p.comparison_date_start===p.comparison_date_end?'':' – '+labelDate(p.comparison_date_end)} · label tanggal UTC`:`${date(p.start)} – ${date(p.end)} ${zone()}`;}
function sourceWindow(p,key){const w=p.source_periods[key];return `${productLabel(key)}: ${date(w.start)} – ${date(w.end)} ${zone()}`;}
function updateSourceMeta(){const meta=state.metadata.products[state.product];$('source-meta').innerHTML=`${meta.initialization_utc?'Run '+escapeHtml(meta.initialization_utc)+' UTC':'Run tunggal tidak tercatat'}<br>${meta.times.length} periode · ${meta.shape[1]} × ${meta.shape[2]} grid · ${meta.resolution[0]}°<br>Label waktu: ${meta.time_label==='start'?'awal':'akhir'} akumulasi`;}
function toast(message){ $('status').textContent=message; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('status').textContent='',4000); }
function fail(error){ if(error.name==='AbortError') return; $('error-text').textContent=error.message || String(error); $('error').hidden=false; console.error(error); }
async function api(path,options={}){
  const response=await fetch(path,options);
  if(!response.ok){ const error=await response.json().catch(()=>({detail:`HTTP ${response.status}`})); throw new Error(typeof error.detail === 'string' ? error.detail : JSON.stringify(error.detail)); }
  return response.json();
}
function query(extra={}){
  return new URLSearchParams({time:state.time,agg:state.agg,threshold:state.threshold,...extra}).toString();
}
function saveURL(){
  if(!maps[0]) return;
  const center=maps[0].getCenter();
  const p=new URLSearchParams({mode:state.mode,product:state.product,agg:state.agg,time:state.time,threshold:state.threshold,
    opacity:state.opacity,muted:state.muted,palette:state.palette,basemap:state.basemap,tz:state.timezone,
    lng:center.lng.toFixed(5),lat:center.lat.toFixed(5),zoom:maps[0].getZoom().toFixed(2),swipe:state.swipe});
  if(state.hidden.size) p.set('hidden',[...state.hidden].join(','));
  if(state.geometry) p.set('area',JSON.stringify(state.geometry));
  history.replaceState(null,'',`?${p}`);
}
function basemapStyle(){
  const options={light:'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}',dark:'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
    satellite:'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'};
  const sources={}, layers=[{id:'background',type:'background',paint:{'background-color':state.basemap==='dark'?'#233640':'#e8eff1'}}];
  if(state.basemap!=='none'){
    sources.base={type:'raster',tiles:[options[state.basemap]],tileSize:256,maxzoom:state.basemap==='satellite'?19:16,
      attribution:'Tiles © <a href="https://www.esri.com/">Esri</a> · Esri, HERE, Garmin, FAO, NOAA, USGS, OpenStreetMap contributors'};
    layers.push({id:'basemap',type:'raster',source:'base'});
  }
  return {version:8,sources,layers};
}
function mapProduct(index){ return index===1?'B':state.mode==='single'?state.product:state.mode==='diff'?'diff':state.mode==='agreement'?'agreement':'A'; }
function tileURL(product,time=state.time){
  return `${location.origin}/api/tiles/${product}/{z}/{x}/{y}.png?${query({time,muted:state.muted,hidden:[...state.hidden].join(','),palette:state.palette,v:state.metadata.products.A.cache_version})}`;
}
function blank(){ return {type:'FeatureCollection',features:[]}; }
function ensureMapLayers(map,index){
  if(!map.isStyleLoaded()) return;
  if(!map.getSource('rain')){
    map.addSource('rain',{type:'raster',tiles:[tileURL(mapProduct(index))],tileSize:256,minzoom:0,maxzoom:18});
    map.addLayer({id:'rain',type:'raster',source:'rain',paint:{'raster-opacity':state.opacity/100,'raster-resampling':'nearest','raster-fade-duration':150}});
    map.addSource('heavy-grid',{type:'geojson',data:blank()});
    map.addLayer({id:'heavy-fill',type:'fill',source:'heavy-grid',minzoom:8,paint:{'fill-color':['get','color'],'fill-opacity':state.opacity/100}});
    map.addLayer({id:'heavy-outline',type:'line',source:'heavy-grid',minzoom:8,paint:{'line-color':'#ffffff','line-opacity':.6,'line-width':.7}});
    map.addSource('selected-grid',{type:'geojson',data:blank()});
    map.addLayer({id:'selected-outline',type:'line',source:'selected-grid',paint:{'line-color':'#173b47','line-width':2}});
    map.addSource('study-area',{type:'geojson',data:blank()});
    map.addLayer({id:'area-fill',type:'fill',source:'study-area',paint:{'fill-color':'#146e70','fill-opacity':.08}});
    map.addLayer({id:'area-outline',type:'line',source:'study-area',paint:{'line-color':'#146e70','line-width':2,'line-dasharray':[3,2]}});
    map.addSource('drawing',{type:'geojson',data:blank()});
    map.addLayer({id:'drawing',type:'line',source:'drawing',paint:{'line-color':'#146e70','line-width':2}});
    for(const [i,overlay] of (state.metadata.overlays||[]).entries()){
      map.addSource(`overlay-${i}`,{type:'geojson',data:`/api/overlays/${i}`});
      map.addLayer({id:`overlay-${i}`,type:'line',source:`overlay-${i}`,layout:{visibility:state.activeOverlays.has(i)?'visible':'none'},paint:{'line-color':overlay.color||'#586d78','line-width':1.2}});
    }
  }
  map.getSource('study-area').setData(state.geometry?{type:'Feature',properties:{},geometry:state.geometry}:blank());
  map.setPaintProperty('rain','raster-opacity',state.opacity/100);
  updateGridFilter(map,index);
}
function updateGridFilter(map,index){
  if(!map.getLayer('heavy-fill'))return;
  const product=mapProduct(index);
  const hidden=['A','B'].includes(product)?[...state.hidden]:[];
  const filter=hidden.length?['!', ['in',['get','category_index'],['literal',hidden]]]:null;
  map.setFilter('heavy-fill',filter);map.setFilter('heavy-outline',filter);
  map.setPaintProperty('heavy-fill','fill-opacity',state.opacity/100);
}
function updateMaps(){
  maps.forEach((map,i)=>{
    ensureMapLayers(map,i);
    if(map.getSource('rain')) map.getSource('rain').setTiles([tileURL(mapProduct(i))]);
    updateGridFilter(map,i);
  });
  updateVectors();
}
async function updateVectors(){
  const current=++gridRevision;
  await Promise.all(maps.map(async(map,i)=>{
    if(!map.getSource('heavy-grid')) return;
    if(map.getZoom()<8){ map.getSource('heavy-grid').setData(blank()); return; }
    const bounds=map.getBounds(),bbox=[bounds.getWest(),bounds.getSouth(),bounds.getEast(),bounds.getNorth()].join(',');
    try{
      const data=await api(`/api/grid/${mapProduct(i)}?${query({bbox})}`);
      if(current===gridRevision){
        if(state.palette==='accessible'&&['A','B'].includes(mapProduct(i))){
          const colors=['#cbd5e1','#56b4e9','#009e73','#e69f00','#d55e00','#cc79a7'];
          data.features.forEach(f=>f.properties.color=colors[f.properties.category_index]);
        }
        map.getSource('heavy-grid').setData(data);
      }
    }catch(error){fail(error);}
  }));
}
function setMode(mode){
  state.mode=mode;
  $('map-stage').dataset.mode=mode;
  $('swipe-control').hidden=mode!=='swipe';
  document.querySelectorAll('[data-mode]').forEach(el=>{if(el.tagName==='BUTTON'){el.classList.toggle('active',el.dataset.mode===mode);el.setAttribute('aria-pressed',el.dataset.mode===mode);}});
  $('label-b').hidden=!['side','swipe'].includes(mode);
  $('label-a').textContent=mode==='diff'?'Selisih · A − B':mode==='agreement'?'Kesepakatan · A / B':productLabel(mapProduct(0));
  $('label-b').textContent=productLabel('B');
  maps.forEach(m=>m.resize());
  applySwipe(); buildLegend(); if(maps.length)updateMaps(); saveURL();
}
function applySwipe(){state.swipe=Number($('swipe').value);$('map-b').style.clipPath=state.mode==='swipe'?`inset(0 0 0 ${state.swipe}%)`:'';}
function buildLegend(){
  const container=$('legend-items');container.replaceChildren();
  const mode=state.mode;
  $('legend-title').innerHTML=mode==='diff'?'Selisih A − B <span>mm / periode · skala ±100</span>':mode==='agreement'?'Kesepakatan deteksi <span>nilai &gt; ambang</span>':'Akumulasi hujan <span>mm / periode</span>';
  const cats=mode==='agreement'?[{label:'Keduanya > ambang',color:'#71368a'},{label:'Hanya '+productLabel('A'),color:'#f4a261'},{label:'Hanya '+productLabel('B'),color:'#3483c9'},{label:'Tidak ada',color:'#dbe3ea'}]:mode==='diff'?[{label:'A lebih tinggi (+100)',color:'#c02c30'},{label:'Sama (0)',color:'#f5f5f5'},{label:'B lebih tinggi (−100)',color:'#2765b6'}]:state.categories;
  const bounds=state.categories.length===5?['<1','1–<5','5–<10','10–<20','≥20']:['<5','5–<20','20–<50','50–<100','100–150','>150'];
  cats.forEach((c,i)=>{
    const interactive=!['diff','agreement'].includes(mode);
    const row=document.createElement(interactive?'button':'div');row.className='legend-item';
    const color=interactive&&state.palette==='accessible'?['#cbd5e1','#56b4e9','#009e73','#e69f00','#d55e00','#cc79a7'][i]:c.color;
    row.innerHTML=`<span class="swatch" style="background:${color}"></span><span>${escapeHtml(c.label)}${interactive?' · '+bounds[i]:''}</span>`;
    if(interactive){row.classList.toggle('off',state.hidden.has(i));row.setAttribute('aria-pressed',!state.hidden.has(i));row.onclick=()=>{state.hidden.has(i)?state.hidden.delete(i):state.hidden.add(i);buildLegend();updateMaps();saveURL();};}
    container.append(row);
  });
  $('legend-note').textContent=['diff','agreement'].includes(mode)?'Hanya grid valid bersama.':state.agg==='daily'?'Klik kategori untuk sembunyikan.':'Batas kategori numerik; bukan intensitas harian.';
}
function timeline(){
  const p=state.periods[state.time];
  $('time').max=state.periods.length-1;$('time').value=state.time;
  $('time-label').textContent=periodLabel(p);
  $('partial-label').textContent=p.partial?`Parsial · ${fmt(p.hours,0)} jam`:state.agg==='max'?'Maksimum interval asli':`${fmt(p.hours,0)} jam`;
  $('time-ticks').replaceChildren(...state.periods.map(p=>{const el=document.createElement('span');el.textContent=new Intl.DateTimeFormat('id-ID',{day:'2-digit',month:'short',timeZone:p.time_alignment==='date'?'UTC':state.timezone}).format(new Date(p.time_alignment==='date'?p.comparison_date_end+'T00:00:00Z':p.end));return el;}));
  $('previous').disabled=state.time===0;$('next').disabled=state.time===state.periods.length-1;
  $('play').disabled=state.periods.length<2;
  $('summary-period').innerHTML=['A','B'].map(key=>escapeHtml(sourceWindow(p,key))).join('<br>')+(p.partial?'<br>Periode parsial':'');
  $('time-alignment-note').textContent=p.time_note;$('time-alignment-note').hidden=!p.time_note;
  $('aggregation-note').textContent=state.agg==='daily'?'Akumulasi mengikuti batas forecast asli, bukan hari kalender.':state.agg==='monthly'?'Jumlah interval tersedia per bulan mulai akumulasi; bulan tidak lengkap ditandai parsial.':state.agg==='3daily'?'Kelompok 72 jam dari awal forecast; kelompok terakhir bisa parsial.':state.agg==='max'?'Maksimum akumulasi interval asli pada seluruh forecast.':'Nilai per interval akumulasi asli.';
  if(p.time_alignment==='date')$('aggregation-note').textContent=(state.agg==='monthly'?'Jumlah berdasarkan bulan label tanggal UTC. ':'Pasangan berdasarkan label tanggal UTC. ')+ 'Jendela asli masing-masing produk tetap ditampilkan.';
}
function renderSummary(data){
  const a=data.products.A,b=data.products.B;
  $('count-a').textContent=fmt(a.grids_exceed,0);$('count-b').textContent=fmt(b.grids_exceed,0);
  $('ratio').textContent=data.sensitivity_ratio_A_over_B==null?'Tidak terdefinisi':fmt(data.sensitivity_ratio_A_over_B,2)+'×';
  const rows=[['Luas lebat · km²','area_km2',0],['Area lebat · %','area_pct',2],['Rata-rata · mm','mean_mm',2],['Maksimum · mm','max_mm',2],['P95 · mm','p95_mm',2],['P99 · mm','p99_mm',2]];
  $('metrics-body').innerHTML=rows.map(([label,key,d])=>`<tr><td>${label}</td><td>${fmt(a[key],d)}</td><td>${fmt(b[key],d)}</td></tr>`).join('');
  const agreements=[['Keduanya',data.agreement.both,'#71368a'],['Hanya A',data.agreement.only_A,'#f4a261'],['Hanya B',data.agreement.only_B,'#3483c9'],['Tidak ada',data.agreement.neither,'#dbe3ea']];
  $('agreement-stats').innerHTML=agreements.map(([label,count,color])=>`<dt><span class="swatch" style="background:${color}"></span>${label}</dt><dd>${fmt(count,0)}</dd>`).join('');
  $('agreement-bar').innerHTML=agreements.map(([,count,color])=>`<span style="width:${data.valid_grids?count/data.valid_grids*100:0}%;background:${color}"></span>`).join('');
  $('conclusion').textContent=data.conclusion;
  $('valid-grids').textContent=`${fmt(data.valid_grids,0)} grid valid bersama · ${fmt(data.study_area_km2,0)} km². Selisih rata-rata pada grid lebat: ${fmt(data.heavy_mean_difference_mm,2)} mm. Bias relatif terhadap B: ${fmt(data.heavy_relative_bias_pct,2)}%. Luas dipilih berdasarkan pusat sel.`;
  $('area-badge').textContent=state.geometry?'Area terpilih':'Domain';
  window.lastSummary=data;
}
async function summaryFor(time){
  return state.geometry?api('/api/compare/area',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({geometry:state.geometry,time,agg:state.agg,threshold:state.threshold})}):api(`/api/compare/summary?${query({time})}`);
}
async function refresh(includeOverview=true){
  const current=++revision;$('loading').hidden=false;
  timeline();updateMaps();saveURL();
  try{
    const result=await summaryFor(state.time);
    if(current!==revision)return;
    renderSummary(result);
    if(includeOverview){
      const summaries=state.geometry?await Promise.all(state.periods.map((_,i)=>summaryFor(i))):(await api(`/api/compare/summary?${query({start:0,end:state.periods.length-1})}`)).summaries;
      if(current!==revision)return;
      renderOverview(summaries);
    }
    if(state.point&&includeOverview)await inspect(state.point.lat,state.point.lon);
    prefetch();
  }catch(error){fail(error);}finally{if(current===revision)$('loading').hidden=true;}
}
function scheduleRefresh(overview=true){clearTimeout(refreshTimer);refreshTimer=setTimeout(()=>refresh(overview),170);}
function chartOptions(yLabel){return {responsive:true,maintainAspectRatio:false,animation:false,plugins:{legend:{labels:{font:{size:10},boxWidth:9,boxHeight:9}}},scales:{x:{ticks:{font:{size:9},maxRotation:0},grid:{display:false}},y:{beginAtZero:true,title:{display:true,text:yLabel,font:{size:9}},ticks:{font:{size:9},precision:0},grid:{color:'#eaf0f3'}}}};}
function renderOverview(summaries){
  window.allSummaries=summaries;
  if(overviewChart)overviewChart.destroy();
  overviewChart=new Chart($('overview-chart'),{type:'bar',data:{labels:summaries.map((_,i)=>`P${i+1}`),datasets:[{label:productLabel('A'),data:summaries.map(s=>s.products.A.grids_exceed),backgroundColor:'#c98449',borderRadius:2},{label:productLabel('B'),data:summaries.map(s=>s.products.B.grids_exceed),backgroundColor:'#3683b9',borderRadius:2}]},options:chartOptions('Grid > ambang')});
  $('overview-text').textContent=summaries.map((s,i)=>`Periode ${i+1}: A ${s.products.A.grids_exceed}, B ${s.products.B.grids_exceed}`).join('; ');
}
let inspectRevision=0;
async function inspect(lat,lon){
  const current=++inspectRevision;
  try{
    const [data,point]=await Promise.all([api(`/api/compare/timeseries?${new URLSearchParams({lat,lon,threshold:state.threshold})}`),api(`/api/compare/point?${query({lat,lon})}`)]);
    if(current!==inspectRevision)return;
    state.point={lat:data.lat,lon:data.lon};
    $('point-label').textContent=`${data.lat.toFixed(2)}°, ${data.lon.toFixed(2)}° · A ${fmt(point.A,2)} / B ${fmt(point.B,2)} mm`;
    $('point-chart-wrap').hidden=false;
    if(pointChart)pointChart.destroy();
    pointChart=new Chart($('point-chart'),{type:'line',data:{labels:data.times.map(t=>new Intl.DateTimeFormat('id-ID',{day:'2-digit',month:'short',timeZone:data.time_alignment==='date'?'UTC':state.timezone}).format(new Date(t))),datasets:[{label:productLabel('A'),data:data.A,borderColor:'#b9672c',backgroundColor:'#b9672c',pointRadius:3,borderWidth:2},{label:productLabel('B'),data:data.B,borderColor:'#2677b4',backgroundColor:'#2677b4',pointRadius:3,borderWidth:2},{label:`Ambang ${state.threshold}`,data:data.times.map(()=>state.threshold),borderColor:'#80949e',borderDash:[4,4],pointRadius:0,borderWidth:1}]},options:chartOptions('mm / interval asli')});
    $('point-stats').textContent=`Maksimum A ${fmt(data.stats.A.max_mm,2)} mm / B ${fmt(data.stats.B.max_mm,2)} mm. Periode > ambang: A ${data.stats.A.periods_exceed}, B ${data.stats.B.periods_exceed}.`;
    if(data.time_note)$('point-stats').textContent+=' '+data.time_note;
    highlight(data.lat,data.lon);
  }catch(error){fail(error);}
}
function highlight(lat,lon){
  const [dx,dy]=state.metadata.products.A.resolution;
  const ring=[[lon-dx/2,lat-dy/2],[lon+dx/2,lat-dy/2],[lon+dx/2,lat+dy/2],[lon-dx/2,lat+dy/2],[lon-dx/2,lat-dy/2]];
  maps.forEach(m=>m.getSource('selected-grid')?.setData({type:'Feature',properties:{},geometry:{type:'Polygon',coordinates:[ring]}}));
}
function hover(event,map){
  $('cursor').textContent=`${event.lngLat.lat.toFixed(3)}°, ${event.lngLat.lng.toFixed(3)}° · zoom ${map.getZoom().toFixed(1)}`;
  if(state.drawing)return;
  clearTimeout(hoverTimer);
  const {lat,lng:lon}=event.lngLat;
  const bbox=state.metadata.products.A.bbox;
  if(lon<bbox[0]||lon>bbox[2]||lat<bbox[1]||lat>bbox[3]){hoverController?.abort();$('tooltip').hidden=true;return;}
  hoverTimer=setTimeout(async()=>{
    hoverController?.abort();hoverController=new AbortController();
    try{
      const p=await api(`/api/compare/point?${query({lat,lon})}`,{signal:hoverController.signal});
      const tooltip=$('tooltip');
      tooltip.innerHTML=`<strong>${p.lat.toFixed(2)}°, ${p.lon.toFixed(2)}°</strong>${escapeHtml(productLabel('A'))}: ${fmt(p.A,2)} mm<br>${escapeHtml(productLabel('B'))}: ${fmt(p.B,2)} mm<br>Selisih: ${fmt(p.diff,2)} mm<br><small>A: ${escapeHtml(p.category_A)}<br>B: ${escapeHtml(p.category_B)}</small>`;
      tooltip.hidden=false;
      const shell=document.querySelector('.map-shell').getBoundingClientRect(),rect=map.getContainer().getBoundingClientRect();
      tooltip.style.left=Math.min(rect.left-shell.left+event.point.x+15,shell.width-tooltip.offsetWidth-8)+'px';
      tooltip.style.top=Math.max(8,Math.min(event.point.y+10,shell.height-tooltip.offsetHeight-35))+'px';
    }catch(error){if(error.name!=='AbortError')fail(error);}
  },100);
}
function sync(source,target){if(syncBusy)return;syncBusy=true;target.jumpTo({center:source.getCenter(),zoom:source.getZoom(),bearing:source.getBearing(),pitch:source.getPitch()});syncBusy=false;}
function fitDomain(){const b=state.metadata.products.A.bbox;maps[0].fitBounds([[b[0],b[1]],[b[2],b[3]]],{padding:{top:65,bottom:65,left:20,right:20},duration:0});fitted=true;}
function prefetch(){
  if(!maps[0]||state.periods.length<2)return;
  const time=(state.time+1)%state.periods.length;
  const center=maps[0].getCenter(),z=Math.max(0,Math.floor(maps[0].getZoom())),n=2**z;
  const x=Math.max(0,Math.min(n-1,Math.floor((center.lng+180)/360*n))),y=Math.max(0,Math.min(n-1,Math.floor((1-Math.asinh(Math.tan(center.lat*Math.PI/180))/Math.PI)/2*n)));
  ['A','B'].forEach(p=>fetch(tileURL(p,time).replace('{z}',z).replace('{x}',x).replace('{y}',y)).catch(()=>{}));
}
function setTime(value){state.time=Math.max(0,Math.min(state.periods.length-1,Number(value)));refresh(false);}
function pause(){clearInterval(playback);playback=null;$('play').textContent='▶';$('play').setAttribute('aria-label','Putar animasi');$('play').setAttribute('aria-pressed','false');}
function play(){pause();if(state.periods.length<2)return;$('play').textContent='Ⅱ';$('play').setAttribute('aria-label','Jeda animasi');$('play').setAttribute('aria-pressed','true');playback=setInterval(()=>setTime((state.time+1)%state.periods.length),1600/state.speed);}
function beginDraw(type){
  state.drawing=type;state.vertices=[];$('tooltip').hidden=true;$('draw-status').hidden=false;$('finish-draw').hidden=type!=='polygon';
  const text=type==='polygon'?'Klik titik poligon, lalu Selesai. Esc membatalkan.':type==='rectangle'?'Klik dua sudut kotak. Esc membatalkan.':'Klik pusat, lalu titik radius lingkaran. Esc membatalkan.';
  $('draw-status').textContent=text;$('area-hint').textContent=text;
  maps.forEach(m=>{m.getCanvas().style.cursor='crosshair';m.doubleClickZoom.disable();});
}
function cancelDraw(){state.drawing=null;state.vertices=[];$('draw-status').hidden=true;$('finish-draw').hidden=true;maps.forEach(m=>{m.getCanvas().style.cursor='';m.doubleClickZoom.enable();m.getSource('drawing')?.setData(blank());});}
function setArea(geometry){
  state.geometry=geometry;cancelDraw();maps.forEach((m,i)=>ensureMapLayers(m,i));
  $('area-hint').textContent=geometry?'Statistik memakai pusat grid di dalam area terpilih.':'Statistik default: seluruh domain.';
  refresh(true);
}
function finishPolygon(){if(state.vertices.length<3){toast('Poligon perlu minimal 3 titik.');return;}setArea({type:'Polygon',coordinates:[[...state.vertices,state.vertices[0]]]});}
function drawClick(coords){
  state.vertices.push(coords);const v=state.vertices;
  if(state.drawing==='rectangle'&&v.length===2){const [a,b]=v;setArea({type:'Polygon',coordinates:[[[a[0],a[1]],[b[0],a[1]],[b[0],b[1]],[a[0],b[1]],[a[0],a[1]]]]});}
  else if(state.drawing==='circle'&&v.length===2){
    const [a,b]=v, rad=Math.PI/180, dlat=(b[1]-a[1])*rad,dlon=(b[0]-a[0])*rad;
    const distance=2*Math.asin(Math.min(1,Math.sqrt(Math.sin(dlat/2)**2+Math.cos(a[1]*rad)*Math.cos(b[1]*rad)*Math.sin(dlon/2)**2)));
    const ring=Array.from({length:65},(_,i)=>{const bearing=i/64*2*Math.PI,lat=Math.asin(Math.sin(a[1]*rad)*Math.cos(distance)+Math.cos(a[1]*rad)*Math.sin(distance)*Math.cos(bearing));const lon=a[0]*rad+Math.atan2(Math.sin(bearing)*Math.sin(distance)*Math.cos(a[1]*rad),Math.cos(distance)-Math.sin(a[1]*rad)*Math.sin(lat));return [lon/rad,lat/rad];});
    setArea({type:'Polygon',coordinates:[ring]});
  }else if(v.length>1){maps.forEach(m=>m.getSource('drawing')?.setData({type:'Feature',properties:{},geometry:{type:'LineString',coordinates:v}}));}
}
function viewportBBox(){const b=maps[0].getBounds();return [Math.max(-180,b.getWest()),Math.max(-90,b.getSouth()),Math.min(180,b.getEast()),Math.min(90,b.getNorth())].join(',');}
function download(url){const link=document.createElement('a');link.href=url;link.download='';link.click();}
async function exportMap(){
  try{
    toast('Menyiapkan PNG…');
    await Promise.all(maps.filter((_,i)=>i===0||['side','swipe'].includes(state.mode)).map(m=>new Promise((resolve,reject)=>{
      if(m.loaded()){resolve();return;}
      const timeout=setTimeout(()=>reject(new Error('Layer peta belum siap. Coba lagi atau pilih basemap offline.')),20000);
      m.once('idle',()=>{clearTimeout(timeout);resolve();});
    })));
    const stage=$('map-stage'),rect=stage.getBoundingClientRect(),ratio=window.devicePixelRatio||1;
    const canvas=document.createElement('canvas');canvas.width=Math.round(rect.width*ratio);canvas.height=Math.round((rect.height+146)*ratio);
    const ctx=canvas.getContext('2d');ctx.fillStyle='#fff';ctx.fillRect(0,0,canvas.width,canvas.height);
    ctx.scale(ratio,ratio);ctx.fillStyle='#182f3b';ctx.font='bold 14px Segoe UI';ctx.fillText(`Rainfall NC Compare · ${state.mode} · > ${state.threshold} mm`,14,22);ctx.font='11px Segoe UI';ctx.fillText($('time-label').textContent,14,42);
    const period=state.periods[state.time];ctx.font='10px Segoe UI';['A','B'].forEach((key,i)=>ctx.fillText(sourceWindow(period,key),14,62+i*18));
    if(period.time_note){ctx.fillStyle='#80531f';ctx.fillText(period.time_note,14,102);}
    const top=128;
    const c0=maps[0].getCanvas();ctx.drawImage(c0,0,0,c0.width,c0.height,0,top,state.mode==='side'?rect.width/2:rect.width,rect.height);
    if(['side','swipe'].includes(state.mode)){
      const c1=maps[1].getCanvas();ctx.save();if(state.mode==='swipe'){ctx.beginPath();ctx.rect(rect.width*state.swipe/100,top,rect.width*(1-state.swipe/100),rect.height);ctx.clip();}
      ctx.drawImage(c1,0,0,c1.width,c1.height,state.mode==='side'?rect.width/2:0,top,state.mode==='side'?rect.width/2:rect.width,rect.height);ctx.restore();
    }
    ctx.font='bold 10px Segoe UI';const textA=$('label-a').textContent.trim(),widthA=ctx.measureText(textA).width+18;ctx.fillStyle='#fff';ctx.fillRect(10,top+10,widthA,24);ctx.fillStyle='#182f3b';ctx.fillText(textA,18,top+26);
    if(['side','swipe'].includes(state.mode)){const textB=productLabel('B'),widthB=ctx.measureText(textB).width+18;ctx.fillStyle='#fff';ctx.fillRect(rect.width-widthB-10,top+10,widthB,24);ctx.fillStyle='#182f3b';ctx.fillText(textB,rect.width-widthB-2,top+26);}
    // Include the same numeric legend and source attribution in the exported viewport.
    const items=[...$('legend-items').children].filter(el=>!el.classList.contains('off'));
    const boxHeight=items.length*18+37;ctx.fillStyle='#fffffff0';ctx.fillRect(10,canvas.height/ratio-boxHeight-18,235,boxHeight);
    ctx.fillStyle='#182f3b';ctx.font='bold 10px Segoe UI';ctx.fillText($('legend-title').textContent,20,canvas.height/ratio-boxHeight);
    items.forEach((el,i)=>{const y=canvas.height/ratio-boxHeight+18+i*18;ctx.fillStyle=el.querySelector('.swatch').style.background;ctx.fillRect(20,y-8,10,10);ctx.fillStyle='#182f3b';ctx.font='10px Segoe UI';ctx.fillText(el.textContent.trim(),36,y);});
    ctx.fillStyle='#fff';ctx.fillRect(0,canvas.height/ratio-16,rect.width,16);ctx.fillStyle='#526772';ctx.font='9px Segoe UI';ctx.fillText(`Data: ${productName('A')} / ${productName('B')} · ${state.basemap==='none'?'Tanpa basemap':'Basemap © Esri and contributors'}`,8,canvas.height/ratio-5);
    const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=`rainfall-map-${state.mode}-P${state.time+1}.png`;a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);toast('PNG peta tersimpan.');
  }catch(error){fail(error);toast('Ekspor raster alternatif tersedia lewat /api/export/png.');}
}
async function searchLocation(event){
  event.preventDefault();const text=$('search').value.trim(),parts=text.split(',').map(Number);
  if(text.includes(',')&&parts.length===2&&parts.every(Number.isFinite)){
    const [lat,lon]=parts;if(Math.abs(lat)>90||Math.abs(lon)>180){fail(new Error('Koordinat tidak valid. Format: latitude, longitude.'));return;}
    maps[0].flyTo({center:[lon,lat],zoom:8,duration:matchMedia('(prefers-reduced-motion: reduce)').matches?0:800});await inspect(lat,lon);$('search-results').hidden=true;return;
  }
  if(!text)return;
  try{
    const response=await fetch(`https://nominatim.openstreetmap.org/search?${new URLSearchParams({q:text,format:'json',countrycodes:'id',limit:5})}`);
    if(!response.ok)throw new Error('Pencarian wilayah gagal. Gunakan koordinat latitude, longitude.');
    const results=await response.json();$('search-results').replaceChildren();$('search-results').hidden=false;
    if(!results.length){toast('Wilayah tidak ditemukan.');$('search-results').hidden=true;return;}
    results.forEach(r=>{const button=document.createElement('button');button.textContent=r.display_name;button.onclick=()=>{maps[0].flyTo({center:[+r.lon,+r.lat],zoom:8});inspect(+r.lat,+r.lon);$('search-results').hidden=true;};$('search-results').append(button);});
  }catch(error){fail(error);}
}
function initControls(){
  $('product').value=state.product;$('aggregation').value=state.agg;$('threshold').value=state.threshold;$('threshold-value').textContent=state.threshold;
  $('opacity').value=state.opacity;$('opacity-value').textContent=`${state.opacity}%`;$('muted').checked=state.muted;$('accessible').checked=state.palette==='accessible';$('basemap').value=state.basemap;$('timezone').value=state.timezone;$('swipe').value=state.swipe;
  document.querySelectorAll('[data-mode]').forEach(el=>{if(el.tagName==='BUTTON')el.onclick=()=>setMode(el.dataset.mode);});
  $('product').onchange=e=>{state.product=e.target.value;updateSourceMeta();setMode(state.mode);};
  const setThreshold=value=>{state.threshold=Number(value);$('threshold').value=value;$('threshold-value').textContent=value;document.querySelectorAll('[data-threshold]').forEach(b=>b.classList.toggle('active',+b.dataset.threshold===state.threshold));scheduleRefresh(true);};
  $('threshold').oninput=e=>setThreshold(e.target.value);document.querySelectorAll('[data-threshold]').forEach(el=>el.onclick=()=>setThreshold(el.dataset.threshold));
  $('aggregation').onchange=async e=>{
    pause();clearTimeout(refreshTimer);const current=++revision,agg=e.target.value;
    try{
      const periods=(await api(`/api/periods?agg=${agg}`)).periods;
      const categories=(await api(`/api/categories?profile=${periods[0].hours<=1?'hourly':'daily'}`)).categories;
      if(current!==revision)return;
      state.agg=agg;state.periods=periods;state.categories=categories;state.time=Math.min(state.time,periods.length-1);
      buildLegend();await refresh(true);
    }catch(error){fail(error);}
  };
  $('timezone').onchange=e=>{state.timezone=e.target.value;timeline();saveURL();if(state.point)inspect(state.point.lat,state.point.lon);};
  $('opacity').oninput=e=>{state.opacity=Number(e.target.value);$('opacity-value').textContent=state.opacity+'%';maps.forEach((m,i)=>{if(m.getLayer('rain'))m.setPaintProperty('rain','raster-opacity',state.opacity/100);updateGridFilter(m,i);});saveURL();};
  $('muted').onchange=e=>{state.muted=e.target.checked;updateMaps();saveURL();};$('accessible').onchange=e=>{state.palette=e.target.checked?'accessible':'default';buildLegend();updateMaps();saveURL();};
  $('basemap').onchange=e=>{state.basemap=e.target.value;maps.forEach(m=>m.setStyle(basemapStyle()));saveURL();};
  $('time').oninput=e=>setTime(e.target.value);$('previous').onclick=()=>setTime(state.time-1);$('next').onclick=()=>setTime(state.time+1);$('play').onclick=()=>playback?pause():play();$('speed').onchange=e=>{state.speed=Number(e.target.value);if(playback)play();};
  $('jump-heavy').onclick=()=>{const values=window.allSummaries||[];if(!values.length)return;const peak=values.map(v=>v.products.A.grids_exceed+v.products.B.grids_exceed);setTime(peak.indexOf(Math.max(...peak)));};
  $('swipe').oninput=()=>{applySwipe();saveURL();};$('fit-domain').onclick=fitDomain;$('search-form').onsubmit=searchLocation;
  $('legend-toggle').onclick=()=>{const hidden=!$('legend-items').hidden;$('legend-items').hidden=hidden;$('legend-note').hidden=hidden;$('legend-toggle').textContent=hidden?'+':'−';$('legend-toggle').setAttribute('aria-expanded',!hidden);};
  $('toggle-left').onclick=()=>togglePanel('left');$('toggle-right').onclick=()=>togglePanel('right');
  $('share').onclick=async()=>{saveURL();try{await navigator.clipboard.writeText(location.href);toast('Tautan tampilan disalin.');}catch{toast('Salin URL dari bilah alamat.');}};
  document.querySelectorAll('[data-draw]').forEach(b=>b.onclick=()=>beginDraw(b.dataset.draw));$('finish-draw').onclick=finishPolygon;$('clear-area').onclick=()=>setArea(null);
  $('geojson-upload').onchange=async e=>{try{const data=JSON.parse(await e.target.files[0].text());const features=data.type==='FeatureCollection'?data.features.map(f=>f.geometry):[data.type==='Feature'?data.geometry:data];if(features.some(g=>!['Polygon','MultiPolygon'].includes(g.type)))throw new Error('Area perlu Polygon atau MultiPolygon.');const polygons=features.flatMap(g=>g.type==='Polygon'?[g.coordinates]:g.coordinates);setArea({type:'MultiPolygon',coordinates:polygons});}catch(error){fail(error);}};
  document.querySelectorAll('[data-export]').forEach(b=>b.onclick=()=>download(`/api/export/${b.dataset.export}?${query({product:mapProduct(0),bbox:viewportBBox()})}`));$('export-png').onclick=exportMap;$('dismiss-error').onclick=()=>$('error').hidden=true;
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){cancelDraw();$('search-results').hidden=true;$('tooltip').hidden=true;}if(['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName))return;if(e.key==='ArrowRight'){e.preventDefault();setTime(state.time+1);}if(e.key==='ArrowLeft'){e.preventDefault();setTime(state.time-1);}});
}
function togglePanel(which){
  const left=which==='left',className=left?'panel-collapsed':'summary-collapsed';$('workspace').classList.toggle(className);
  if(innerWidth<=650&&!$('workspace').classList.contains(className))$('workspace').classList.add(left?'summary-collapsed':'panel-collapsed');
  $('toggle-left').setAttribute('aria-expanded',!$('workspace').classList.contains('panel-collapsed'));$('toggle-right').setAttribute('aria-expanded',!$('workspace').classList.contains('summary-collapsed'));maps.forEach(m=>m.resize());
}
async function init(){
  try{
    state.metadata=await api('/api/products');state.periods=(await api(`/api/periods?agg=${state.agg}`)).periods;
    state.time=Math.min(state.time,state.periods.length-1);state.categories=(await api(`/api/categories?profile=${state.periods[0].hours<=1?'hourly':'daily'}`)).categories;
    if(qs.has('area')){try{state.geometry=JSON.parse(qs.get('area'));}catch{toast('Area pada URL tidak valid.');}}
    document.title=`Rainfall NC Compare · ${productName('A')} × ${productName('B')}`;
    $('product-subtitle').textContent=`${productName('A')} × ${productName('B')}`;
    ['A','B'].forEach(key=>{$('product').querySelector(`option[value="${key}"]`).textContent=productLabel(key);$(`metric-name-${key.toLowerCase()}`).textContent=productLabel(key);});
    updateSourceMeta();
    $('sources-detail').innerHTML=Object.values(state.metadata.products).map(p=>`<p><strong>${escapeHtml(p.id+' · '+p.name)}</strong><br>${escapeHtml(p.path)}<br>${escapeHtml(p.variable)} · ${escapeHtml(p.original_units)} → mm<br>Label: ${p.time_label==='start'?'awal':'akhir'} akumulasi<br>${p.negative_values_clipped} nilai negatif kecil dikoreksi ke 0.</p>`).join('');
    for(const [i,overlay] of (state.metadata.overlays||[]).entries()){
      const label=document.createElement('label');label.className='check';const input=document.createElement('input');input.type='checkbox';label.append(input,document.createTextNode(overlay.name));input.onchange=()=>{input.checked?state.activeOverlays.add(i):state.activeOverlays.delete(i);maps.forEach(m=>m.getLayer(`overlay-${i}`)&&m.setLayoutProperty(`overlay-${i}`,'visibility',input.checked?'visible':'none'));};$('overlay-controls').append(label);
    }
    if(!state.metadata.overlays?.length)$('overlay-controls').innerHTML='<p class="helper">Batas admin/DAS: tambahkan GeoJSON di config.yaml.</p>';
    if(innerWidth<=950)$('workspace').classList.add('summary-collapsed');if(innerWidth<=650)$('workspace').classList.add('panel-collapsed');
    initControls();
    const center=[number('lng',118),number('lat',-2.75)],zoom=number('zoom',3.5);
    maps=[0,1].map(i=>new maplibregl.Map({container:i?'map-b':'map-a',style:basemapStyle(),center,zoom,minZoom:2,maxZoom:12,
      canvasContextAttributes:{preserveDrawingBuffer:true},renderWorldCopies:false,attributionControl:{compact:true}}));
    maps.forEach((map,i)=>{
      map.addControl(new maplibregl.NavigationControl({showCompass:false}),'top-right');map.addControl(new maplibregl.ScaleControl({unit:'metric'}),'bottom-left');
      map.on('style.load',()=>{ensureMapLayers(map,i);updateVectors();});
      map.on('move',()=>sync(map,maps[1-i]));map.on('moveend',()=>{saveURL();updateVectors();});
      map.on('mousemove',e=>hover(e,map));map.getCanvas().addEventListener('mouseleave',()=>{clearTimeout(hoverTimer);hoverController?.abort();$('tooltip').hidden=true;});
      map.on('click',e=>state.drawing?drawClick([e.lngLat.lng,e.lngLat.lat]):inspect(e.lngLat.lat,e.lngLat.lng));
      map.on('error',e=>{if(e.error?.message?.includes('/api/'))fail(e.error);});
    });
    setMode(state.mode);$('toggle-left').setAttribute('aria-expanded',!$('workspace').classList.contains('panel-collapsed'));$('toggle-right').setAttribute('aria-expanded',!$('workspace').classList.contains('summary-collapsed'));
    await Promise.all(maps.map(m=>new Promise(resolve=>m.once('load',resolve))));
    if(!qs.has('lng'))fitDomain();await refresh(true);window.rainfall={state,maps,refresh,inspect,setArea,setMode};
  }catch(error){fail(error);}
}
init();
