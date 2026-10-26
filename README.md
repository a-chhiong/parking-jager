# Parking Jäger (都會區優質公有停車場圖資獵人)

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-Open%20Data-green.svg)](https://data.gov.tw/)
[![OpenStreetMap](https://img.shields.io/badge/OpenStreetMap-Compatible-7EBC6F.svg)](https://www.openstreetmap.org/)
[![CoMaps Ready](https://img.shields.io/badge/CoMaps-1--Tap%20KMZ-orange.svg)](https://github.com/comaps/comaps)
[![Google My Maps](https://img.shields.io/badge/Google-My%20Maps-red.svg)](https://mymaps.google.com/)

**Parking Jäger** 專為都會區通勤族、自駕車主與電動車主打造。我們直接串流各地直轄市政府官方開放資料 Single Source of Truth (SSOT)，全自動過濾並篩除暴利民營、日曬雨淋、升降機械塔台與半夜閉館等雷區，產出可一鍵匯入 **[CoMaps](https://github.com/comaps/comaps) (OpenStreetMap)**、**Organic Maps** 與 **Google 我的地圖** 之圖層與對照手冊。

---

## 🎯 Jäger 優質停車場選址標準 (The Jäger Benchmark)

所有收錄於本專案之場站均須通過以下 6 大嚴選標準（以「[格上-民有市場地下站](https://maps.app.goo.gl/WsLPEg952dXUfFgv5)」為旗艦基準）：

1. **🏛️ 公有體系管轄**：政府自營或公有委外，杜絕民間私人業者隨意哄抬與暴利定價。
2. **🕒 24 小時全天候營運**：任何時段皆可自由進出場，絕無半夜拉鐵捲門鎖車之風險。
3. **🏢 室內地下全平面**：坡道平面、風雨無阻，嚴格剔除易刮傷車身或無法停放休旅車/純電車之機械塔台。
4. **💰 公有平價費率**：日間臨停主流 30~40 元/時，住宅區更享夜間離峰 10 元/時超值優惠。
5. **⚡ 電動車友善設備**：優先篩選配備專屬 AC / DC 充電樁與友善專屬車位之場站。
6. **📍 車道入口精準定位**：嚴格採集「車道進場坡道閘門」之經緯度座標，避免導航至公園中心或大地地號。

---

## 🗺️ 城市圖資目錄與索引 (City Directory Index)

| 城市 (City) | 詳細說明文件 | 篩選合格場站 | EV 充電專屬站 | 官方單一真相來源 (SSOT) | 一鍵下載圖資檔案 |
| :--- | :--- | :---: | :---: | :--- | :--- |
| **臺北市 (Taipei)** | [📖 **Taipei/README.md**](./Taipei/README.md) | **230 處** | **188 處** | 臺北市停管處 Azure Blob API | [📲 **KMZ (CoMaps)**](./Taipei/taipei_parking_lots.kmz) · [🌐 **GeoJSON (OSM)**](./Taipei/taipei_parking_lots.geojson) · [📍 KML](./Taipei/taipei_parking_lots.kml) · [📊 CSV](./Taipei/taipei_parking_lots.csv) · [📋 手冊](./Taipei/taipei_parking_lots.md) |
| *新北市 (New Taipei)* | *規劃中 (Planned)* | - | - | 新北市政府開放資料平台 | *敬請期待* |
| *臺中市 (Taichung)* | *規劃中 (Planned)* | - | - | 臺中市政府交通局開放資料 | *敬請期待* |
| *高雄市 (Kaohsiung)* | *規劃中 (Planned)* | - | - | 高雄市政府交通局開放資料 | *敬請期待* |

---

## 🚀 雙生態系圖資應用指引

### 生態系 A：開源離線導航（CoMaps / Organic Maps / OpenStreetMap）
* **手機端 1 鍵匯入**：
  1. 下載僅 32 KB 的 [**`taipei_parking_lots.kmz`**](./Taipei/taipei_parking_lots.kmz)。
  2. 在手機上以 **[CoMaps](https://github.com/comaps/comaps)**（或 Organic Maps）開啟。
  3. 即刻享有 230 處自訂圖釘、完整費率備註，並具備 100% 無網路離線路線導航！
* **回饋至 OpenStreetMap (OSM)**：
  * 開發者與圖資維護者可載入 [**`taipei_parking_lots.geojson`**](./Taipei/taipei_parking_lots.geojson)，內含標準 OSM 標籤（`amenity=parking`, `parking=underground`, `capacity:charging` 等），可直接用於 JOSM 進行社群回饋。

### 生態系 B：Google 雲端地圖（Google My Maps）
1. 前往 [Google 我的地圖 (My Maps)](https://mymaps.google.com/) 並點擊「**+ 建立新地圖**」。
2. 點擊「無標題圖層」底下的「**匯入** (Import)」，上傳 [`taipei_parking_lots.kml`](./Taipei/taipei_parking_lots.kml) 或 [`taipei_parking_lots.csv`](./Taipei/taipei_parking_lots.csv)。
3. 開啟手機 **Google 地圖 App** $\to$「**已儲存**」$\to$ 最下方「**地圖**」即可永久啟用。

---

## 📂 專案架構 (Repository Structure)

```text
parking-jager/
├── README.md                   # [根目錄] 本索引目錄、Jäger 標準與各城市快速入口
├── .gitignore                  # [設定] Git 忽略名單 (忽略快取與暫存檔)
└── Taipei/                     # [城市模組] 臺北市專區
    ├── README.md               # 臺北市專屬架構、篩選管線、CoMaps / OSM 指引與 SSOT 紀錄
    ├── generate_parking_maps.py # 停管處 SSOT 串流生成主程式 (無第三方相依，純標準庫)
    ├── taipei_parking_lots.kmz # CoMaps / Organic Maps 專用 1 鍵匯入離線書籤檔 (32 KB)
    ├── taipei_parking_lots.geojson # OpenStreetMap 規範圖資 (支援 uMap、JOSM 與 GitHub 原生地圖)
    ├── taipei_parking_lots.kml # Google My Maps 專用 KML (含專屬停車圖示與導航氣泡卡)
    ├── taipei_parking_lots.csv # 官方經緯度資料庫 CSV (UTF-8 with BOM，Excel 完美相容)
    └── taipei_parking_lots.md  # 臺北市 12 行政區統一 7 欄位完整對照手冊
```

---

## 🤝 參與貢獻與新增城市 (Contributing)

歡迎提交 Pull Request 或建立 Issue 來擴充新城市：
* 必須以直轄市/縣市政府交通局或停管處之**官方開放資料 API (SSOT)** 為唯一圖資依據。
* 產出程式須維持**零第三方套件相依性（純 Python 標準庫）**原則。
* 輸出格式必須同時支援 CoMaps / OSM 生態系（`.kmz`, `.geojson`）與 Google 地圖生態系（`.kml`, `.csv`, `.md`）。
