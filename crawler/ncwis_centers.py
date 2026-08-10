"""托嬰中心主檔爬蟲（工程執行計劃 6.1 後續強化）：托育媒合平臺 ncwisweb。

目的不是裁罰（該站的「違反法令紀錄」欄位實務全空，見第二節），而是機構本身的
資料厚度：評鑑等級與年度、準公共化資格、核定與實際收托、面積、核備工作人員數、
**收費情形明細**、退費說明。kiang 的 `babycare.json` 只投影了其中 8 個欄位，
撐不起 2,045 家托嬰中心的機構頁。

站方行為（2026-08-09 實測）：

- 詳情頁 `/home/childcare-center/detail/{CW編號}` 可直接 GET，不需 session。
- 查詢是 POST `/home/childcare-center`（Spring，需 `_csrf` 與 session cookie），
  **搜尋條件存在 session 裡**，所以每次都要送出完整參數集，不能只送要改的那個。
- `cityId` 不會過濾（送了仍回全域結果），`townId` 會。且全域查詢有 **300 筆上限**，
  故以鄉鎮市區為分割單位列舉，每區筆數遠低於上限。
- 結果頁的地圖標記把該區全部 CW 編號都寫在 HTML 裡，不必逐頁翻。
- **座標也在結果頁的地圖標記裡**（`var x/var y`，TWD97 二度分帶 EPSG:3826），
  站方只是沒有直接寫成經緯度。反算後與 kiang 的既有座標中位差 0.0 公尺、
  90% 在 75 公尺內；差距大的那 6% 是他改用 NLSC 地理編碼的部分，站方自己的
  標記才是權威值。所以不需要 TGOS 也不需要 NLSC，本爬蟲自帶座標。

低頻批次（機構主檔月更即可），不進每日排程，因此不寫心跳檔、不納入 watchdog。
**此站同樣封鎖境外 IP，只能在 old-mini（台灣 IP）執行**：2026-08-10 自 GitHub
Actions 實測兩次（出口 IP 52.161.50.38、52.188.87.20），對 `/home/childcare-center`
的請求四次重試全部 `timed out`——是逾時而非 403，看起來是網路層丟包。
注意同屬社家署的 `crc.sfaa.gov.tw`（裁罰專區）從 Actions 一直跑得通，
兩站的封鎖策略不同，不能互相推論。

用法：python3 crawler/ncwis_centers.py [--data-dir PATH] [--delay N]
      [--city 臺北市] [--limit N] [--new-only] [--parse-only]
      --new-only   只抓尚無快照的中心（快速補新增）
      --parse-only 不連線，只用既有快照重新解析
"""

import json
import math
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import Fetcher, log, save  # noqa: E402

BASE = "https://ncwisweb.sfaa.gov.tw"
SEARCH = f"{BASE}/home/childcare-center"
DETAIL = f"{BASE}/home/childcare-center/detail/"

CSRF_RE = re.compile(r'<meta name="_csrf" content="([^"]+)"')
CITY_SELECT_RE = re.compile(r'<select[^>]*id="city".*?</select>', re.S)
TOWN_SELECT_RE = re.compile(r'<select[^>]*id="town".*?</select>', re.S)
OPTION_RE = re.compile(r'<option[^>]*value="([^"]*)"[^>]*>\s*([^<]*?)\s*</option>')
CW_RE = re.compile(r"/home/childcare-center/detail/(CW\d+)")
TOTAL_RE = re.compile(r"共\s*<span[^>]*>\s*(\d+)\s*</span>\s*筆")

NAME_RE = re.compile(r'<div class="cell">機構名稱<span>\s*(.*?)\s*</span>')
ROW_RE = re.compile(r'<span class="tag">\s*(.*?)\s*</span>(.*?)(?=<div class="data-row|</div>\s*</div>)', re.S)
# 不要求收尾的 </div>：區塊尾端的 </div> 可能已被 ROW_RE 的界線切掉
STAFF_RE = re.compile(r'<div class="d-inline-block[^"]*">\s*(.*?)\s*(\d+)\s*人')
FEE_MODE_RE = re.compile(r'<p class="h6">托育方式：\s*(.*?)\s*</p>')
FEE_CELL_RE = re.compile(r'<td[^>]*data-label="([^"｜]*)[^"]*">\s*(.*?)\s*</td>', re.S)
TAG_RE = re.compile(r"<[^>]+>")

# 站方每次連線的 csrf token 都不同，存檔前剝掉，否則每日快照都是假異動
CSRF_NOISE_RE = re.compile(r'(<meta name="_csrf" content=)"[^"]*"')
CSRF_INPUT_RE = re.compile(r'(name="_csrf"\s+value=)"[^"]*"')

# 單值欄位：站方標籤 → 我方欄位名
FIELDS = {
    "所在地": "address",
    "托嬰中心註記名稱": "note_name",
    "負責人姓名": "owner",
    "聯絡電話": "phone",
    "核定收托": "capacity_approved",
    "實際收托": "enrolled",
    "評鑑等級": "grade",
    "最近一次評鑑年度": "grade_year",
    "總面積": "area_total",
    "室內面積": "area_indoor",
    "室外面積": "area_outdoor",
    "公共安全說明": "safety_public",
    "消防安全說明": "safety_fire",
    "準公共化資格": "pre_public",
    "退費說明": "refund_note",
}
NUMERIC = {"capacity_approved", "enrolled"}


# 地圖標記：座標與該筆的詳情連結相鄰，限制間距避免跨筆錯配
MARKER_RE = re.compile(
    r"var x = '([\d.]+)';\s*var y = '([\d.]+)';.{0,600}?detail/(CW\d+)", re.S)
# 站方全台一律用 TWD97 二度分帶、中央經線 121（離島亦同，已實測確認）
TM2_LON0 = 121.0
TW_BBOX = (118.0, 21.5, 122.2, 26.5)


def text(html: str) -> str:
    return re.sub(r"\s+", " ", TAG_RE.sub(" ", html)).strip()


def tm2_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """TWD97 二度分帶（EPSG:3826）→ WGS84 經緯度。橫麥卡托反算，零依賴。"""
    a, f = 6378137.0, 1 / 298.257222101       # GRS80
    k0, false_easting = 0.9999, 250000.0
    e2 = 2 * f - f * f
    ep2 = e2 / (1 - e2)
    x -= false_easting
    mu = (y / k0) / (a * (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi = (mu + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
           + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
           + (151 * e1**3 / 96) * math.sin(6 * mu)
           + (1097 * e1**4 / 512) * math.sin(8 * mu))
    c1 = ep2 * math.cos(phi) ** 2
    t1 = math.tan(phi) ** 2
    n1 = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    r1 = a * (1 - e2) / (1 - e2 * math.sin(phi) ** 2) ** 1.5
    d = x / (n1 * k0)
    lat = phi - (n1 * math.tan(phi) / r1) * (
        d**2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * ep2) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * ep2 - 3 * c1**2) * d**6 / 720)
    lon = math.radians(TM2_LON0) + (
        d - (1 + 2 * t1 + c1) * d**3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * ep2 + 24 * t1**2) * d**5 / 120
    ) / math.cos(phi)
    return math.degrees(lat), math.degrees(lon)


def collect_coords(data_dir: Path) -> tuple[dict[str, tuple[float, float]], int]:
    """從列表快照抽出各中心的地圖標記座標。回傳（座標表, 站方為 0 或超出範圍的筆數）。"""
    coords, bad = {}, 0
    for path in sorted((data_dir / "raw" / "ncwis" / "list").glob("*.html")):
        html = path.read_text(encoding="utf-8")
        for sx, sy, cw in MARKER_RE.findall(html):
            x, y = float(sx), float(sy)
            if not x or not y:      # 站方自己就沒有座標
                bad += 1
                continue
            lat, lng = tm2_to_wgs84(x, y)
            x0, y0, x1, y1 = TW_BBOX
            if not (x0 <= lng <= x1 and y0 <= lat <= y1):
                bad += 1
                continue
            coords[cw] = (round(lat, 6), round(lng, 6))
    return coords, bad


def strip_csrf(html: str) -> str:
    return CSRF_INPUT_RE.sub(r'\1""', CSRF_NOISE_RE.sub(r'\1""', html))


def options(html: str, select_re: re.Pattern) -> list[tuple[str, str]]:
    m = select_re.search(html)
    return [(v, n) for v, n in OPTION_RE.findall(m.group(0)) if v] if m else []


def parse_detail(html: str, cw: str, failures: list) -> dict | None:
    name = NAME_RE.search(html)
    if not name:
        failures.append(f"ncwis:{cw} 找不到機構名稱")
        return None
    rec = {"id": cw, "name": text(name.group(1)), "staff": {}, "fees": [],
           "violations": []}
    for label, body in ROW_RE.findall(html):
        label = text(label)
        if label == "核備工作人員數":
            rec["staff"] = {text(role): int(n) for role, n in STAFF_RE.findall(body)}
        elif label == "收費情形":
            mode = FEE_MODE_RE.search(body)
            cells = [(text(k), text(v)) for k, v in FEE_CELL_RE.findall(body)]
            item: dict = {}
            for key, value in cells:
                field = {"費用名稱": "name", "費用金額": "amount",
                         "費用說明": "note"}.get(key)
                if field is None:
                    continue
                if field == "name" and item:
                    rec["fees"].append(item)
                    item = {}
                item[field] = value or None
            if item:
                rec["fees"].append(item)
            rec["fee_mode"] = text(mode.group(1)) if mode else None
        elif label == "違反法令紀錄":
            rec["violations"] = [text(li) for li in
                                 re.findall(r"<li[^>]*>(.*?)</li>", body, re.S)]
        elif (field := FIELDS.get(label)) is not None:
            value = text(body) or None
            if field in NUMERIC and value:
                digits = re.match(r"\d+", value)
                value = int(digits.group(0)) if digits else None
            rec[field] = value
    if not rec.get("address"):
        failures.append(f"ncwis:{cw} 無所在地")
    return rec


def collect_ids(fetcher: Fetcher, data_dir: Path, only_city: str | None,
                delay_note: str) -> list[str] | None:
    """逐縣市取鄉鎮清單、逐鄉鎮查詢，收集全部 CW 編號。"""
    page = fetcher.fetch(SEARCH)
    if page is None:
        log("無法取得查詢頁")
        return None
    csrf = CSRF_RE.search(page)
    if not csrf:
        log("查詢頁找不到 _csrf token（站方可能改版）")
        return None
    token = csrf.group(1)
    cities = options(page, CITY_SELECT_RE)
    log(f"縣市 {len(cities)} 個{delay_note}")

    def query(fields: dict) -> str | None:
        # 搜尋條件存在 session，每次都要送完整參數集才能清掉上一次的條件
        body = {"_csrf": token, "locateType": "1", "cityId": "", "townId": "",
                "conditions[keytext]": "", "organ2type": "", "publicFlag": "",
                "distance": "1.0", "page": "0", **fields}
        return fetcher.fetch(SEARCH, data=body,
                             headers={"Referer": SEARCH,
                                      "Content-Type": "application/x-www-form-urlencoded"})

    ids: list[str] = []
    seen: set[str] = set()
    for city_id, city_name in cities:
        if only_city and city_name != only_city:
            continue
        html = query({"cityId": city_id})
        if html is None:
            log(f"  {city_name}：鄉鎮清單取得失敗，略過")
            continue
        towns = options(html, TOWN_SELECT_RE)
        city_count = 0
        for town_id, town_name in towns:
            result = query({"cityId": city_id, "townId": town_id})
            if result is None:
                log(f"  {city_name}{town_name}：查詢失敗，略過")
                continue
            save(data_dir / "raw" / "ncwis" / "list" / f"{city_name}_{town_name}.html",
                 strip_csrf(result))
            total = TOTAL_RE.search(result)
            found = [cw for cw in dict.fromkeys(CW_RE.findall(result))]
            if total and int(total.group(1)) != len(found):
                log(f"  {city_name}{town_name}：站方稱 {total.group(1)} 筆，"
                    f"頁面只有 {len(found)} 個編號")
            for cw in found:
                if cw not in seen:
                    seen.add(cw)
                    ids.append(cw)
            city_count += len(found)
        log(f"  {city_name}：{len(towns)} 區、{city_count} 家")
    return ids


def main(argv: list[str]) -> int:
    def opt(name: str, default=None):
        return argv[argv.index(name) + 1] if name in argv else default

    known = {"--data-dir", "--delay", "--city", "--limit", "--new-only", "--parse-only"}
    for a in argv:
        if a.startswith("--") and a not in known:
            log(f"未知參數：{a}")
            return 2

    default_data = Path(__file__).resolve().parent.parent.parent / "preschool-resume-data"
    data_dir = Path(opt("--data-dir", default_data))
    delay = float(opt("--delay", 2.0))
    only_city = opt("--city")
    limit = int(opt("--limit")) if "--limit" in argv else None
    new_only = "--new-only" in argv
    parse_only = "--parse-only" in argv

    detail_dir = data_dir / "raw" / "ncwis" / "detail"

    if parse_only:
        ids = sorted(p.stem for p in detail_dir.glob("CW*.html"))
        log(f"--parse-only：使用既有快照 {len(ids):,} 份")
    else:
        fetcher = Fetcher(delay=delay, verify_tls=False)  # 同 sfaa 網域，CA 缺 SKI
        ids = collect_ids(fetcher, data_dir, only_city,
                          f"，請求間隔 {delay} 秒")
        if ids is None:
            return 1
        log(f"共 {len(ids):,} 家托嬰中心")
        targets = [cw for cw in ids
                   if not (new_only and (detail_dir / f"{cw}.html").exists())]
        if limit:
            targets = targets[:limit]
        log(f"待抓詳情 {len(targets):,} 家")
        for n, cw in enumerate(targets, 1):
            html = fetcher.fetch(DETAIL + cw)
            if html is None:
                continue
            save(detail_dir / f"{cw}.html", strip_csrf(html))
            if n % 100 == 0:
                log(f"  {n:,}/{len(targets):,}")
        if fetcher.failures:
            log(f"抓取失敗 {len(fetcher.failures)} 筆")

    coords, bad_coords = collect_coords(data_dir)
    records, failures = [], []
    for cw in sorted(ids):
        path = detail_dir / f"{cw}.html"
        if not path.exists():
            failures.append(f"ncwis:{cw} 詳情快照不存在")
            continue
        rec = parse_detail(path.read_text(encoding="utf-8"), cw, failures)
        if rec:
            rec["lat"], rec["lng"] = coords.get(cw, (None, None))
            records.append(rec)

    out = data_dir / "data" / "ncwis" / "centers.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"as_of": date.today().isoformat(), "records": records},
                              ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    graded = sum(1 for r in records if r.get("grade"))
    fees = sum(1 for r in records if r.get("fees"))
    located = sum(1 for r in records if r.get("lat") is not None)
    log(f"解析 {len(records):,} 家 → {out}"
        f"（有評鑑等級 {graded:,}、有收費明細 {fees:,}、有座標 {located:,}"
        + (f"，站方無座標 {bad_coords}" if bad_coords else "") + "）")
    if failures:
        for f in failures[:10]:
            log(f"  {f}")
        log(f"解析問題 {len(failures)} 筆")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
