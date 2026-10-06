# PRD — Rainfall NC Compare

**Aplikasi peta interaktif untuk visualisasi & perbandingan sensitivitas hujan lebat dari dua produk NetCDF**

|  |  |
| --- | --- |
| Versi | 1.0 (Draft) |
| Tanggal | 1 Oktober 2026 |
| Stack utama | FastAPI, xarray, NumPy, Zarr (cache), Leaflet/MapLibre |
| Bahasa | Indonesia |

---

## 1. Latar Belakang

Dua produk curah hujan (mis. satelit/reanalisis/grid observasi) sudah diunduh dalam format NetCDF (`.nc`). Kedua produk punya resolusi, satuan, dan karakter yang berbeda, sehingga sulit dibandingkan secara manual. Kebutuhan utama: **melihat grid mana yang melampaui ambang 50 mm** dan **menilai produk mana yang lebih sensitif menangkap hujan lebat**.

## 2. Tujuan

1. Menampilkan data hujan `.nc` dari **Produk A** dan **Produk B** pada peta interaktif.
2. Menyorot grid dengan curah hujan **> 50 mm** (ambang dapat diubah).
3. Mewarnai setiap grid berdasarkan **kategori hujan**.
4. Membandingkan A vs B: sebaran grid lebat, selisih, dan metrik sensitivitas.
5. Respons cepat (target \< 1 detik per time-step pada area studi) memakai FastAPI.

### Di Luar Cakupan (v1)

- Download otomatis produk dari penyedia data.
- Bias correction / downscaling.
- Autentikasi multi-user.

## 3. Pengguna

| Persona | Kebutuhan |
| --- | --- |
| Analis hidrologi / BPBD | Melihat area hujan lebat dan memilih produk yang andal |
| Peneliti / modeler | Evaluasi produk hujan sebelum dipakai sebagai forcing model |
| Developer GIS | Mengambil hasil via API/GeoJSON |

## 4. Kebutuhan Fungsional

### 4.1 Manajemen Data

- **FR-01** Mendaftarkan file `.nc` (path lokal) sebagai Produk A dan B melalui file konfigurasi atau endpoint.
- **FR-02** Auto-deteksi variabel hujan (`precipitation`, `precip`, `tp`, `rain`, dst.), dimensi `time/lat/lon`, dan satuan (`units` attribute). Pengguna dapat meng-override manual.
- **FR-03** Normalisasi satuan ke **mm** (mm/jam, mm/hari, m, kg m⁻² s⁻¹ dikonversi sesuai periode akumulasi).
- **FR-04** Normalisasi koordinat (lon 0–360 → −180–180, lat menurun → menaik).
- **FR-05** Subset area studi (bounding box atau shapefile/GeoJSON, mis. Indonesia atau DAS tertentu).

### 4.2 Akumulasi Waktu

- **FR-06** Pilihan agregasi: per time-step asli, **harian**, **3 harian**, **bulanan**, atau **maksimum periode**.
- **FR-07** Ambang 50 mm dievaluasi pada akumulasi terpilih (default: 50 mm/hari).

### 4.3 Visualisasi Grid

- **FR-08** Tampilan peta: Produk A, Produk B, side-by-side, dan peta selisih (A − B).
- **FR-09** Grid > ambang ditampilkan menonjol (opasitas penuh, grid di bawah ambang transparan atau diredam).
- **FR-10** Pewarnaan per kategori hujan (lihat 4.4), dengan legenda.
- **FR-11** Tooltip per grid: koordinat, nilai A, nilai B, selisih, kategori.
- **FR-12** Slider/dropdown waktu dan tombol play untuk animasi.

### 4.4 Kategori Hujan (default, dapat dikonfigurasi)

Mengacu pada klasifikasi intensitas harian BMKG:

| Kategori | Curah hujan (mm/hari) | Warna usulan |
| --- | --- | --- |
| Tidak hujan / sangat ringan | \< 5 | Transparan / abu muda |
| Ringan | 5 – 20 | `#8ecae6` biru muda |
| Sedang | 20 – 50 | `#2a9d8f` hijau tosca |
| **Lebat** | 50 – 100 | `#f4a261` oranye |
| **Sangat lebat** | 100 – 150 | `#e63946` merah |
| **Ekstrem** | > 150 | `#6a0572` ungu |

> Untuk data per jam, gunakan profil kategori terpisah (mis. lebat 10–20 mm/jam, sangat lebat > 20 mm/jam). Profil disimpan di `categories.yaml`.

### 4.5 Perbandingan Sensitivitas

Kedua produk di-regrid ke **grid bersama** (default: grid produk beresolusi lebih kasar, atau pilihan pengguna) agar adil.

**Metrik tanpa referensi (selalu tersedia):**

| Metrik | Penjelasan |
| --- | --- |
| Jumlah grid > ambang | Per produk dan per periode |
| Luas area > ambang (km²) / persentase area | Normalisasi terhadap area studi |
| Maksimum, P95, P99 | Karakter ekor distribusi |
| Peta kesepakatan | Kedua > ambang, hanya A, hanya B, tidak ada |
| Rasio sensitivitas | `grid_A>ambang / grid_B>ambang` |
| Bias relatif pada grid lebat | Rata-rata (A−B) di grid yang lebat menurut salah satu produk |

**Metrik dengan referensi (opsional, bila ada data stasiun/gauge):**

| Metrik | Penjelasan |
| --- | --- |
| POD (Probability of Detection) | Kejadian lebat yang berhasil tertangkap |
| FAR (False Alarm Ratio) | Alarm lebat yang salah |
| CSI (Critical Success Index) | Gabungan POD dan FAR |
| Frequency Bias | Over/under-estimasi frekuensi kejadian lebat |

- **FR-13** Panel ringkasan menampilkan tabel metrik dan **kesimpulan otomatis**, misalnya "Produk A mendeteksi 32% lebih banyak grid > 50 mm dibanding Produk B".
- **FR-14** Perlu diingat di UI: tanpa data referensi, "lebih sensitif" berarti *lebih banyak mendeteksi*, bukan *lebih akurat*. Tampilkan catatan ini di panel.

### 4.6 Ekspor

- **FR-15** Unduh GeoJSON grid > ambang, CSV ringkasan metrik, dan PNG peta.

## 5. Arsitektur & Strategi Performa

```
.nc (A, B) ──► Preprocess (xarray) ──► Cache Zarr/NetCDF terkompresi
                                          │
Client (Leaflet/MapLibre) ◄── FastAPI ◄───┘
        ▲            │  ├─ /tiles (PNG raster, server-side render)
        └────────────┘  └─ /grid  (GeoJSON, hanya grid > ambang)
```

**Prinsip kecepatan:**

1. **Buka lazy**: `xr.open_dataset(..., chunks=...)` dengan dask; tidak memuat seluruh file.
2. **Preprocess sekali**: konversi ke Zarr ber-chunk waktu dan ruang, sekaligus regrid ke grid bersama dan hitung agregasi harian. Request hanya membaca irisan kecil.
3. **Dua mode render**:
   - **Raster tile PNG** (NumPy → colormap → PNG) untuk tampilan penuh. Cepat dan ringan di browser.
   - **GeoJSON vektor** hanya untuk grid > ambang dalam viewport (dibatasi jumlah fitur), supaya tooltip dan klik tetap tersedia.
4. **Vektorisasi NumPy**: klasifikasi kategori dengan `np.digitize`, bukan loop Python.
5. **Respons efisien**: `ORJSONResponse`, `GZipMiddleware`, header `ETag`/`Cache-Control`, cache LRU (`functools.lru_cache` atau Redis opsional) untuk hasil per `(produk, waktu, agregasi, ambang)`.
6. **Endpoint `async`** dan komputasi berat di threadpool (`run_in_threadpool`); job panjang (preprocess) lewat `BackgroundTasks`.
7. **Query berbasis bbox**: hanya irisan data di viewport yang dihitung.

## 6. Rancangan API

| Method | Endpoint | Fungsi |
| --- | --- | --- |
| GET | `/api/products` | Daftar produk, variabel, periode, resolusi, satuan |
| POST | `/api/products/register` | Mendaftarkan file `.nc` sebagai A/B |
| GET | `/api/meta/{product}` | Metadata: time range, bbox, resolusi |
| GET | `/api/categories` | Profil kategori hujan + warna |
| GET | `/api/tiles/{product}/{z}/{x}/{y}.png?time=&agg=&threshold=` | Raster tile berwarna kategori |
| GET | `/api/grid/{product}?time=&agg=&threshold=50&bbox=` | GeoJSON grid > ambang |
| GET | `/api/diff?time=&agg=&bbox=` | Peta selisih A − B |
| GET | `/api/agreement?time=&agg=&threshold=50` | Peta kesepakatan A/B |
| GET | `/api/compare/summary?start=&end=&agg=&threshold=50&bbox=` | Metrik sensitivitas (JSON) |
| GET | `/api/compare/timeseries?lat=&lon=` | Deret waktu A vs B pada satu titik |
| GET | `/api/export/{geojson\|csv\|png}` | Ekspor hasil |
| POST | `/api/reference/upload` | (Opsional) data stasiun untuk POD/FAR/CSI |

Contoh respons `/api/compare/summary`:

```json
{
  "threshold_mm": 50,
  "agg": "daily",
  "period": ["2026-01-01", "2026-01-31"],
  "products": {
    "A": {"grids_exceed": 1840, "area_pct": 4.2, "max_mm": 212.4, "p99_mm": 88.1},
    "B": {"grids_exceed": 1395, "area_pct": 3.1, "max_mm": 164.9, "p99_mm": 71.6}
  },
  "agreement": {"both": 960, "only_A": 880, "only_B": 435},
  "sensitivity_ratio_A_over_B": 1.32,
  "conclusion": "Produk A lebih sensitif terhadap hujan lebat (+32% grid > 50 mm)."
}
```

## 7. Antarmuka Pengguna — Peta Interaktif

UI utama adalah **peta interaktif layar penuh**. Semua fungsi (pilih produk, ambang, waktu, perbandingan) beroperasi langsung di atas peta, bukan di halaman terpisah.

### 7.1 Tata Letak

```
┌──────────────────────────────────────────────────────────────┐
│ [Logo]  Rainfall NC Compare     Mode: ◉Single ○Side ○Swipe ○Diff ○Agree │
├────────────┬─────────────────────────────────────┬───────────┤
│ Kontrol    │                                     │ Ringkasan │
│ (collaps.) │        PETA INTERAKTIF              │ Metrik    │
│ Produk A/B │   grid berwarna per kategori        │ Grafik    │
│ Ambang     │   grid > 50 mm menonjol             │ Kesimpulan│
│ Agregasi   │                      [Legenda]      │ (collaps.)│
│ Area       │  [Zoom +/-] [Layer] [Gambar area]   │           │
├────────────┴─────────────────────────────────────┴───────────┤
│ ◀ ▶ ⏵  |——●————————————————|  2026-01-15   Kecepatan: 1x    │
└──────────────────────────────────────────────────────────────┘
```

Panel kiri dan kanan dapat dilipat, sehingga peta bisa mengisi seluruh layar.

### 7.2 Mode Tampilan Peta

| Mode | Perilaku |
| --- | --- |
| **Single** | Satu produk (A atau B) dengan grid berwarna kategori |
| **Side-by-side** | Dua peta berdampingan dengan **zoom dan pan tersinkron** |
| **Swipe** | Satu peta; garis geser (slider vertikal) membelah tampilan A dan B |
| **Diff** | Peta selisih A − B (skala divergen: merah = A lebih tinggi, biru = B lebih tinggi) |
| **Agreement** | 4 warna: keduanya > ambang, hanya A, hanya B, tidak ada |

### 7.3 Interaksi Peta

1. **Hover grid**: grid disorot dengan outline, tooltip menampilkan koordinat, nilai A, nilai B, selisih, dan kategori.
2. **Klik grid**: popup berisi deret waktu A vs B pada grid tersebut (grafik mini), nilai maksimum, dan jumlah hari > ambang.
3. **Gambar area** (polygon/kotak/lingkaran): statistik ringkas untuk area itu, yaitu jumlah grid > ambang, rata-rata, maksimum, dan rasio sensitivitas A/B.
4. **Slider ambang**: geser ambang (10–200 mm), peta langsung diperbarui. Tombol cepat: 20, 50, 100, 150.
5. **Legenda interaktif**: klik kategori untuk menampilkan atau menyembunyikan; grid di bawah ambang bisa diredam atau disembunyikan.
6. **Timeline**: slider waktu, tombol play/pause, kecepatan 0.5x–4x, loncat ke hari dengan jumlah grid lebat tertinggi.
7. **Layer control**: basemap (OSM, satelit, gelap), batas administratif (provinsi/kabupaten), batas DAS, opasitas layer hujan.
8. **Pencarian lokasi**: ketik nama wilayah atau koordinat, peta terbang ke lokasi.
9. **Koordinat kursor** dan skala peta di pojok bawah.
10. **Bagikan tampilan**: seluruh state (produk, waktu, ambang, mode, posisi peta) tersimpan di URL.
11. **Ekspor tampilan**: tombol unduh PNG peta saat ini dan GeoJSON grid > ambang pada viewport.

### 7.4 Perilaku Rendering

- Zoom jauh: tampilkan **raster tile PNG** berwarna kategori (cepat).
- Zoom dekat: tampilkan **grid vektor** (GeoJSON, hanya grid > ambang di viewport) agar hover/klik presisi.
- Peralihan raster ke vektor otomatis pada zoom tertentu (default z ≥ 8).
- Saat menggeser slider waktu, tile bulan/hari sekitar di-*prefetch* agar animasi mulus.
- Indikator *loading* tipis di atas peta; tampilan lama dipertahankan sampai tile baru siap (tanpa berkedip).

### 7.5 Responsif & Aksesibilitas

- Desktop: tiga panel; tablet/ponsel: panel menjadi *bottom sheet*, kontrol utama tetap di atas peta.
- Palet warna kategori diuji untuk buta warna (pilihan palet alternatif), dengan label teks di legenda.
- Operasi dasar dapat diakses dengan keyboard (panah untuk waktu, `+/-` untuk zoom).

## 8. Kebutuhan Non-Fungsional

| Aspek | Target |
| --- | --- |
| Latensi tile/grid (area studi, 1 time-step, setelah cache) | \< 500 ms (p95) |
| Latensi tanpa cache | \< 2 detik |
| Ringkasan metrik 1 bulan data harian | \< 5 detik |
| Ukuran file sampai | ± beberapa GB per produk (via lazy/Zarr) |
| Jumlah fitur GeoJSON per respons | Maks. 50.000 (otomatis disederhanakan bila lebih) |
| Kompatibilitas | Python 3.11+, Windows/Linux, Chrome/Edge/Firefox |
| Reproducibility | Semua parameter perhitungan dicatat di respons dan hasil ekspor |

## 9. Tech Stack

- **Backend**: FastAPI, Uvicorn, xarray, dask, netCDF4/h5netcdf, zarr, NumPy, pyproj, `xesmf` atau `scipy` (regrid), `rioxarray`/`geopandas` (masking), Pillow/matplotlib colormap (render PNG), orjson.
- **Frontend (peta interaktif)**: MapLibre GL JS (rekomendasi, WebGL, mulus untuk grid dan tile besar; plugin swipe/compare tersedia), alternatif Leaflet. Chart.js/Plotly untuk grafik.
- **Opsional**: Redis (cache), Docker.

## 10. Struktur Proyek (usulan)

```
rainfall-nc-compare/
├── app/
│   ├── main.py            # FastAPI app, middleware
│   ├── api/               # routers: products, tiles, grid, compare, export
│   ├── core/              # config, categories.yaml
│   ├── services/
│   │   ├── loader.py      # buka .nc, deteksi variabel, normalisasi
│   │   ├── regrid.py      # grid bersama
│   │   ├── classify.py    # kategori & ambang (np.digitize)
│   │   ├── metrics.py     # sensitivitas, POD/FAR/CSI
│   │   └── render.py      # PNG tile, GeoJSON
│   └── cache/             # Zarr hasil preprocess
├── frontend/              # index.html, map.js, charts.js
├── scripts/preprocess.py  # CLI: .nc → Zarr (regrid + agregasi)
├── data/                  # file .nc A dan B
└── tests/
```

## 11. Kriteria Penerimaan

- [ ] Dua file `.nc` terbaca dan satuan otomatis menjadi mm.
- [ ] Peta menampilkan grid > 50 mm dengan warna sesuai kategori dan legenda.
- [ ] Ambang dapat diubah dan peta langsung ter-update.
- [ ] Peta interaktif layar penuh dengan mode Single, Side-by-side (tersinkron), Swipe, Diff, dan Agreement berfungsi.
- [ ] Hover grid menampilkan tooltip; klik grid menampilkan deret waktu A vs B; gambar area menampilkan statistik.
- [ ] State tampilan (produk, waktu, ambang, mode, posisi peta) tersimpan di URL.
- [ ] Panel metrik menampilkan jumlah grid, luas area, P95/P99, rasio sensitivitas, dan kesimpulan otomatis.
- [ ] Target latensi tercapai pada dataset uji.
- [ ] Ekspor GeoJSON/CSV/PNG berfungsi.
- [ ] Unit test untuk konversi satuan, klasifikasi kategori, regrid, dan metrik.

## 12. Rencana Pengerjaan

| Fase | Isi | Estimasi |
| --- | --- | --- |
| 1 | Loader `.nc`, normalisasi satuan/koordinat, preprocess ke Zarr | 3–4 hari |
| 2 | Klasifikasi kategori, endpoint grid dan tile, peta dasar | 4–5 hari |
| 3 | Regrid bersama, diff, agreement, metrik sensitivitas | 4–5 hari |
| 4 | UI lengkap: side-by-side, panel metrik, timeline, ekspor | 4–5 hari |
| 5 | Optimasi performa, cache, pengujian, dokumentasi | 3 hari |

## 13. Risiko & Mitigasi

| Risiko | Mitigasi |
| --- | --- |
| Resolusi dan grid kedua produk berbeda, sehingga perbandingan bias | Regrid ke grid bersama; tampilkan juga hasil di grid asli |
| Satuan/akumulasi ambigu (mm/jam vs mm/hari) | Deteksi dari atribut + konfirmasi manual di UI |
| File besar memperlambat respons | Zarr ber-chunk, lazy loading, cache hasil |
| "Lebih sensitif" disalahartikan sebagai "lebih akurat" | Catatan eksplisit di UI; dukung data referensi untuk POD/FAR/CSI |
| Terlalu banyak fitur GeoJSON di browser | Batasi per viewport; gunakan raster tile untuk tampilan penuh |
| Perbedaan zona waktu (UTC vs WIB) mengubah agregasi harian | Parameter zona waktu eksplisit (default UTC, opsi WIB) |

## 14. Pertanyaan Terbuka

1. Produk apa saja yang dipakai (nama, resolusi, interval waktu)?
2. Area studi: seluruh Indonesia, provinsi, atau DAS tertentu (shapefile tersedia)?
3. Apakah ada data stasiun/gauge sebagai referensi?
4. Ambang 50 mm dievaluasi per hari, per jam, atau per event multi-hari?
5. Apakah frontend perlu dikemas sebagai desktop app atau cukup web?