# Rainfall NC Compare

Aplikasi web lokal sesuai PRD **Rainfall NC Compare**, untuk membandingkan deteksi hujan lebat dua produk NetCDF yang dikonfigurasi. Backend FastAPI, pengolahan xarray/NumPy/Dask, cache Zarr, peta MapLibre, grafik Chart.js. Antarmuka berbahasa Indonesia. Konfigurasi sekarang membandingkan ECMWF satu run dengan ECMWF harian run 00Z; nama UI mengikuti konfigurasi.

## Jalankan di Windows

Dependensi sudah dipasang di `D:\hujan_forecast\.venv`; aset frontend sudah tersedia lokal.

Klik dua kali **`run.bat`**, atau dari PowerShell:

```powershell
& "D:\hujan_forecast\rainfall-nc-compare\run.ps1"
```

Buka **http://127.0.0.1:8000**. Dokumentasi API interaktif: **http://127.0.0.1:8000/docs**. Tutup server dengan `Ctrl+C`.

Alternatif tanpa menjalankan skrip PowerShell:

```powershell
& "D:\hujan_forecast\.venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir "D:\hujan_forecast\rainfall-nc-compare" --host 127.0.0.1 --port 8000
```

Port lain: `run.ps1 -Port 8001`.

### Instalasi ulang

Jalankan dari direktori `rainfall-nc-compare`:

```powershell
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm --prefix frontend ci
..\.venv\Scripts\python.exe scripts\preprocess.py
```

Python 3.11+, Node.js/npm diperlukan untuk instalasi aset. Saat aplikasi berjalan, Node.js tidak diperlukan. JS/CSS disajikan lokal; basemap terang/gelap/satelit Esri dan pencarian nama lokasi membutuhkan internet. Mode **Tanpa basemap** tetap menampilkan raster hujan, inspeksi grid, metrik dan ekspor secara offline. Pencarian koordinat juga offline.

## Data default

Konfigurasi: `app/core/config.yaml`. Path relatif terhadap direktori proyek.

| Produk | File | Variabel |
| --- | --- | --- |
| A — ECMWF IFS | `../ecmwf_rain/rain24h_*.nc` (nama file terbaru) | `rain_24h` |
| B — ECMWF daily 00Z | `../ecmwf_out/ecmwf_rain_daily.nc` | `precip_daily_mm` |

A: run **30 September 2026 18:00 UTC**, label akhir akumulasi **1–5 Oktober 18:00 UTC**. B: label awal akumulasi **1–5 Oktober 00:00 UTC**, berasal dari run harian. Lima pasangan **label tanggal 1–5 Oktober**. Domain overlap: **95–141° BT, −11–6° lintang**, grid bersama **69 × 185**, resolusi **0.25°**.

### Label waktu dan pencocokan tanggal

```yaml
products:
  A:
    name: ECMWF IFS
    path: ../ecmwf_rain/rain24h_*.nc
    variable: rain_24h
    period_hours: 24
    time_label: end
  B:
    name: ECMWF daily 00Z
    path: ../ecmwf_out/ecmwf_rain_daily.nc
    variable: precip_daily_mm
    period_hours: 24
    time_label: start
grid: coarser
time_alignment: date
```

- **`time_label: start`**: timestamp file adalah awal jendela; akhir = timestamp + durasi.
- **`time_label: end`** (default): timestamp adalah akhir; awal = timestamp − durasi. Data `step/valid_time` menggunakan `end`.
- **`time_alignment: exact`** (default tanpa override): cocokkan waktu akhir dan awal akumulasi persis. Menolak kedua dataset sekarang karena jendela berbeda; error menampilkan rentang timestamp A/B.
- **`time_alignment: date`**: cocokkan **tanggal UTC label asli**. Hanya akumulasi 24 jam, satu interval per tanggal, tanggal bersama kontinu. Tidak menggeser nilai hujan atau menyatakan jendela fisik identik.

Contoh label **1 Oktober**: A **30 Sep 18:00 → 1 Okt 18:00 UTC**; B **1 Okt 00:00 → 2 Okt 00:00 UTC**. Label terakhir B **5 Oktober** berakhir **6 Oktober 00:00 UTC**. UI menampilkan dua jendela dan catatan perbedaan; label tanggal tetap UTC, WIB hanya mengubah tampilan jam jendela asli.

Mode date memakai interval kalender sintetis hanya sebagai indeks internal. API menandainya `index_window_only: true`; waktu hujan sebenarnya selalu di `period.source_periods.A/B`. Metadata menyediakan `comparison_dates`, `accumulation_starts`, `accumulation_ends`, `time_label` dan `time_alignment`. CSV memakai batas waktu asli per produk; GeoJSON dan PNG juga mencatatnya. Agregasi 3 harian/bulanan mengelompokkan label tanggal bersama; bulanan data saat ini adalah **1–5 Oktober parsial**.

Setelah mengubah YAML, **restart server** (Ctrl+C lalu jalankan lagi `run.bat`) dan muat ulang browser. Cache fingerprint otomatis berubah; tidak perlu menghapus cache lama atau file sumber. Untuk kembali membandingkan GFS, ubah B ke file GFS, `variable: precipitation`, `time_label: end`, dan gunakan `time_alignment: exact`.

Normalisasi latitude menaik dan longitude −180..180, satuan mm, lalu regrid bilinear jika perlu ke grid lebih kasar di overlap kedua produk. Tidak ada extrapolation. Nama variabel dapat dideteksi otomatis atau di-override. Satuan didukung: mm, m, kg m⁻², mm/jam, mm/hari, kg m⁻² s⁻¹. Konversi rate mengikuti durasi akumulasi sebenarnya. `cumulative: true` untuk sumber kumulatif sejak run.

Durasi perlu `period_hours` bila metadata tidak cukup. Pada mode exact, periode kedua produk harus sama dan kontinu. Mode date hanya memasangkan label tanggal 24 jam secara eksplisit. Grid 1-D reguler diperlukan; dimensi ensemble/level non-singleton harus dipilih sebelum registrasi.

Cache `app/cache/<fingerprint>.zarr` memakai chunk satu waktu dan 128 × 128 grid. Fingerprint mencakup path, ukuran, mtime, konfigurasi, dan versi preprocessing. Startup memakai ulang cache. `GET /api/products` mengecek perubahan sumber. Registrasi membangun pasangan baru di background; konfigurasi diterbitkan hanya setelah validasi selesai. File sumber tidak dimodifikasi.

### Registrasi produk

Contoh body `POST /api/products/register`:

```json
{
  "product": "A",
  "name": "ECMWF IFS",
  "path": "../ecmwf_rain/rain24h_*.nc",
  "variable": "rain_24h",
  "units": "mm",
  "period_hours": 24,
  "cumulative": false,
  "time_label": "end",
  "time_alignment": "exact",
  "grid": "coarser"
}
```

Respons 202 berisi `job_id` dan `status_url`. Poll `GET /api/jobs/{job_id}` sampai `complete` atau `failed`. Muat ulang UI sesudah registrasi sukses. `grid`: `coarser`, `A`, atau `B`.

## Fitur peta

- **Single**: pilih A atau B; **Side-by-side**: pan/zoom sinkron; **Swipe**: geser pembatas; **Selisih**: A − B, merah positif/biru negatif; **Kesepakatan**: keduanya, hanya A, hanya B, tidak ada.
- Ambang 10–200 mm, tombol cepat 20/50/100/150. Deteksi memakai **`nilai > ambang`**, bukan `>=`.
- Kategori configurable di `app/core/categories.yaml`. Nilai tepat 50 masuk kategori lebat, tetapi tidak melampaui ambang 50. Nilai 150 masih sangat lebat; ekstrem `>150`.
- Opasitas, redam/sembunyikan di bawah ambang, toggle kategori, palet alternatif. Toggle kategori hanya mengubah visual, bukan metrik.
- Timeline, play/pause 0.5×–4×, tombol puncak deteksi; panah kiri/kanan mengubah periode jika fokus bukan input.
- Hover: nilai A/B, kategori dan selisih. Klik: grafik seluruh forecast dan statistik grid. Koordinat pencarian: **`latitude, longitude`**, misalnya `-2, 105`.
- Area kotak (dua sudut), poligon (≥3 titik lalu **Selesai**), lingkaran (pusat dan radius), atau unggah GeoJSON Polygon/MultiPolygon. Esc membatalkan gambar. **Reset area** kembali ke domain penuh.
- State mode/produk/waktu/ambang/peta/area tersimpan di URL; tombol Bagikan menyalin tautan.
- Panel kontrol/metrik dapat dilipat; layar kecil memakai panel bawah. Legenda, angka, tooltip dan label melengkapi warna.
- Pada zoom ≥8, grid lebat dilapisi GeoJSON agar batas sel terlihat. Raster tersedia pada semua zoom. Batas respons 50.000 fitur; jika terpotong, metadata mencantumkan `truncated`.

Layer batas administrasi/DAS bisa disediakan melalui konfigurasi:

```yaml
overlays:
  - name: Batas provinsi
    path: data/provinsi.geojson
    color: '#586d78'
  - name: Batas DAS
    path: data/das.geojson
    color: '#146e70'
```

GeoJSON harus menggunakan longitude/latitude WGS84. Dataset batas tidak disertakan karena pengguna belum menyediakan sumbernya.

## Aturan ilmiah

1. **18:00–18:00 UTC adalah jendela asli**, bukan akumulasi tengah malam. WIB hanya mengubah label (01:00–01:00 WIB). Mengubah batas hari membutuhkan data subharian yang belum tersedia.
2. Native: interval asli. Harian: kelompok 24 jam. 3 harian: kelompok 72 jam; akhir 48 jam ditandai **parsial**. Pada mode exact, bulanan berdasarkan bulan **awal interval fisik**; pada mode date berdasarkan bulan **label UTC bersama**. Interval lintas bulan tidak dipecah secara artifisial. Maksimum periode: maksimum jumlah hujan pada **interval asli**, bukan total forecast.
3. Ambang diterapkan pada hasil agregasi. Batas kategori numerik pada multi-hari bukan klaim intensitas harian BMKG. Profil hourly terpisah untuk interval ≤1 jam.
4. Nilai negatif −0.1..0 mm dikoreksi ke 0 dan jumlah koreksi dicatat. Nilai negatif lebih besar ditolak. ECMWF saat ini: **114** nilai dikoreksi, tanpa mengubah jumlah grid lebat.
5. Metrik hanya pada **grid finite bersama**. NoData tidak dianggap 0; agregasi tidak mengisi interval hilang. Luas memakai quadrilateral bola bumi R=6371.0088 km dengan batas setengah sel, bukan luas konstan per grid.
6. Bbox/area memilih berdasarkan **pusat sel**; luas seluruh sel terpilih dihitung, bukan fraksi perpotongan batas area. Persebaran mencakup daratan **dan laut** di domain; tidak ada land mask tersembunyi.
7. Rasio A/B `null` jika B=0. Selisih rata-rata pada union grid lebat dalam mm; bias relatif terhadap mean B pada union dalam %. NoData, area kosong dan pembagi nol ditangani.
8. **Lebih sensitif bukan lebih akurat** tanpa data observasi. POD/FAR/CSI/referensi stasiun merupakan fitur opsional PRD, belum diaktifkan tanpa gauge.

Baseline regresi pasangan awal ECMWF/GFS (`time_alignment: exact`, sebelum konfigurasi B diganti), `>50 mm`:

| | D01 | D02 | D03 | D04 | D05 |
| --- | ---: | ---: | ---: | ---: | ---: |
| ECMWF | 0 | 0 | 1 | 1 | 0 |
| GFS | 0 | 11 | 9 | 1 | 2 |
| Keduanya | 0 | 0 | 0 | 0 | 0 |

## Ekspor & API

- **GeoJSON UI**: grid lebat pada viewport. Properties memuat A/B, selisih, kategori, agreement. Metadata mencatat parameter/period/cache dan pemotongan fitur.
- **CSV UI**: kedua produk untuk seluruh periode agregasi pada viewport, lengkap jumlah/luas, P95/P99, agreement, rasio, bias, parameter dan cache.
- **PNG peta UI**: viewport dan mode peta yang terlihat, legenda, periode, ambang, attribution. **`/api/export/png`**: raster grid tanpa basemap; sesuai bbox, periode dan ambang.
- Area gambar memengaruhi ringkasan dan grafik deteksi; ekspor UI memakai viewport, sebagaimana label tombol. Untuk area persis, gunakan `/api/compare/area` lalu ekspor responsnya.

Endpoint utama:

```text
GET  /api/products
POST /api/products/register
GET  /api/meta/{A|B}
GET  /api/categories?profile=daily|hourly
GET  /api/periods?agg=daily
GET  /api/tiles/{A|B|diff|agreement}/{z}/{x}/{y}.png
GET  /api/grid/{A|B|diff|agreement}
GET  /api/diff
GET  /api/agreement
GET  /api/compare/summary
POST /api/compare/area
GET  /api/compare/point?lat=-2&lon=105
GET  /api/compare/timeseries?lat=-2&lon=105
GET  /api/export/{geojson|csv|png}
GET  /api/jobs/{job_id}
```

Query umum: `time=0` (indeks periode), `agg=native|daily|3daily|monthly|max`, `threshold=50`, `bbox=west,south,east,north`. `start`/`end` pada summary adalah **indeks periode inklusif**, menghasilkan daftar ringkasan per periode, bukan jumlah unik grid lintas waktu. Tile memakai ETag, Cache-Control dan cache LRU; JSON memakai orjson dan gzip.

## Pengujian

Jalankan dari direktori proyek:

```powershell
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe scripts\benchmark.py
..\.venv\Scripts\python.exe -m playwright install chromium
..\.venv\Scripts\python.exe scripts\browser_check.py
```

Browser check menyalakan server uji lokal sementara lalu menutupnya. Screenshot tersimpan di `screenshots/`. Menguji lima mode, pan/zoom sinkron, threshold, agregasi parsial, grafik titik, area, state URL, ekspor PNG/CSV dan panel mobile/320px. Benchmark mengukur API lewat ASGI TestClient; tidak mencakup jaringan basemap.

Hasil awal pada data asli (5 × 71 × 193): **36 test lulus**. Warm p95: tile **1.14 ms**, grid **5.37 ms**, ringkasan **2.38 ms**, ringkasan lima periode **5.78 ms**. First tile **42.99 ms**; first ringkasan lima periode **104.67 ms**. Target lokal PRD tercapai pada dataset ini; dataset beberapa GB atau satu bulan penuh belum diuji.

Chromium: lima mode, hover/klik, gambar kotak/poligon/lingkaran, mobile 390px/320px, URL dan PNG/CSV lulus tanpa error JavaScript atau respons error API. Basemap Esri terang ditinjau melalui `screenshots/desktop-basemap.png`; tampilan offline desktop/mobile dan hasil ekspor juga disimpan. **Not verified:** pembaca layar, audit kontras/WCAG formal, 200% browser zoom, RTL, Firefox/Edge dan data skala GB.

Verifikasi setelah perubahan pencocokan tanggal: **46 test**, meliputi regresi ECMWF/GFS pada exact, kedua ECMWF pada date, normalisasi label start/end, pelestarian semua nilai A/B di domain overlap, penolakan exact untuk jendela berbeda, dan jendela asli pada API/CSV/GeoJSON/PNG. Browser check juga memakai konfigurasi sekarang: nama B dari YAML, label 1–5 Oktober, dua jendela berbeda dan ekspor berhasil. Screenshot terbaru memperlihatkan kedua produk ECMWF, bukan pasangan GFS lama.
