"""幼兒園主檔爬蟲（脫離 kiang 的最後一塊）：全國教保資訊網「基本資料查詢」。

與 `ece_moe.py`（裁罰）同一個站、同一套 WebForms 流程，差別只在入口與欄位：

- 入口 `https://ap.ece.moe.edu.tw/webecems/`（表單 action 為 `./`），
  查詢條件與裁罰頁同名（`ddlKey`／`txtKeyNameS`／`ddlCityS`／`ddlAreaS`／`btnSearch`），
  翻頁一樣是 `PageControl1$lbNextPage` postback，每頁 10 筆。
- 結果頁是卡片式、欄位全部內嵌，不需要再進詳情頁；欄位由 `GridView1_lblXxx_{列}`
  這組穩定 id 取出，含多年度評鑑歷史表格。
- **座標**在地址連結指向的 `map/showmap.aspx?new=1&sch=<token>`，其 `hInfo` hidden
  欄位格式為「緯度,經度,鄉鎮,門牌,園名」，直接是 WGS84。token 綁 session
  （同 `punish_view`），必須在同一 session 內取用。

站方封鎖境外 IP，只能從台灣的 IP 執行（實務上是 old-mini）。TLS 憑證鏈不完整，
關閉驗證。以縣市為單位逐一查詢，避免單一 session 撐太久。

快照落地 `raw/ece_schools/list/{縣市}_pNNNN.html`（剝除 VIEWSTATE 與 sch token）。
showmap 頁本身九成是樣板，不整頁存檔，只把 `hInfo` 原文記進
`raw/ece_schools/coords/{縣市}.tsv`（可 diff、可重放）。

用法：python3 crawler/ece_schools.py [--data-dir PATH] [--delay N]
      [--city 臺北市] [--limit N] [--new-only] [--parse-only]
      --new-only   已有座標的園所不重抓 showmap（座標幾乎不變，增量跑很省）
      --parse-only 不連線，只用既有快照重新解析
"""

import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import Fetcher, log, save, strip_session_noise  # noqa: E402

BASE = "https://ap.ece.moe.edu.tw/webecems/"
NEXT_PAGE = "PageControl1$lbNextPage"

HIDDEN_RE = re.compile(r'<input type="hidden" name="(__[^"]+)"[^>]*value="([^"]*)"')
CITY_OPTS_RE = re.compile(r'<select[^>]*name="ddlCityS".*?</select>', re.S)
OPTION_RE = re.compile(r'<option[^>]*value="([^"]*)"[^>]*>\s*([^<]*?)\s*</option>')
TOTAL_RE = re.compile(r'id="PageControl1_lblTotalCount">(\d+)<')
MAP_TOKEN_RE = re.compile(
    r'id="GridView1_hlAddr_(\d+)"[^>]*onclick="[^"]*?map/showmap\.aspx\?new=1&amp;sch=([A-Za-z0-9+/=%]+)')
ADDR_RE = re.compile(r'id="GridView1_hlAddr_(\d+)"[^>]*>(.*?)</a>', re.S)
URL_RE = re.compile(r'id="GridView1_hlUrl_(\d+)"[^>]*>(.*?)</a>', re.S)
EVAL_TABLE_RE = re.compile(r'id="GridView1_GridView11_(\d+)"[^>]*>(.*?)</table>', re.S)
EVAL_ROW_RE = re.compile(r"<tr>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>", re.S)
HINFO_RE = re.compile(r'name="hInfo"[^>]*value="([^"]*)"')
TAG_RE = re.compile(r"<[^>]+>")
BRACKET_ZIP_RE = re.compile(r"^\[(\d{3,6})\]\s*")

# GridView1_lbl{名稱}_{列} → 我方欄位
FIELDS = {
    "lblSchName": "name", "lblCity": "city", "lblArea": "town",
    "lblPub": "org_type", "lblTel": "phone", "lblRegNum": "reg_no",
    "lblRegDate": "reg_date", "lblRegId": "reg_id", "lblCharge": "owner",
    "lblGenStd": "capacity_approved", "lblStdPub": "pre_public",
    "lblChildSvc": "after_school", "lblNoPunish": "penalty_note",
    "lblTSpace": "area_total", "lblInSpace": "area_indoor",
    "lblOASpace": "area_outdoor", "lblFloor": "floor", "lblCar": "shuttle",
}
NUMERIC = {"capacity_approved"}


def text(html: str) -> str:
    return re.sub(r"\s+", " ", TAG_RE.sub("", html).replace("&nbsp;", " ")).strip()


def clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def form_fields(html: str) -> dict:
    fields = dict(HIDDEN_RE.findall(html))
    fields.update({"ddlKey": "", "txtKeyNameS": "", "ddlAreaS": ""})
    return fields


def cities(html: str) -> list[tuple[str, str]]:
    m = CITY_OPTS_RE.search(html)
    return [(v, n) for v, n in OPTION_RE.findall(m.group(0)) if v] if m else []


def page_rows(html: str) -> dict[str, dict]:
    """以列索引為鍵回傳當頁各園的欄位（含 showmap token）。"""
    rows: dict[str, dict] = {}
    for key, field in FIELDS.items():
        for idx, raw in re.findall(
                r'id="GridView1_%s_(\d+)"[^>]*>(.*?)</span>' % key, html, re.S):
            rows.setdefault(idx, {})[field] = clean(text(raw))
    for idx, raw in ADDR_RE.findall(html):
        rows.setdefault(idx, {})["address_raw"] = clean(text(raw))
    for idx, raw in URL_RE.findall(html):
        # 站方的 href 會寫成 http://https://…，改用連結文字
        rows.setdefault(idx, {})["url"] = clean(text(raw))
    for idx, token in MAP_TOKEN_RE.findall(html):
        rows.setdefault(idx, {})["map_token"] = token
    for idx, table in EVAL_TABLE_RE.findall(html):
        rows.setdefault(idx, {})["evaluations"] = [
            {"year": text(y), "date": text(d), "result": text(r)}
            for y, d, r in EVAL_ROW_RE.findall(table) if text(y).isdigit()]
    return rows


def normalise(row: dict) -> dict:
    """卡片欄位 → 主檔紀錄。地址拆郵遞區號、數值轉 int、無值統一 None。"""
    rec = {f: row.get(f) for f in FIELDS.values()}
    rec["url"] = row.get("url")
    rec["evaluations"] = row.get("evaluations") or []
    street = row.get("address_raw") or ""
    m = BRACKET_ZIP_RE.match(street)
    rec["postal_code"], street = (m.group(1), street[m.end():]) if m else (None, street)
    rec["address"] = "".join(filter(None, (rec["city"], rec["town"], street))) or None
    for field in NUMERIC:
        digits = re.match(r"[\d,]+", rec.get(field) or "")
        rec[field] = int(digits.group(0).replace(",", "")) if digits else None
    for field in ("pre_public", "after_school", "shuttle", "penalty_note"):
        if rec.get(field) == "無":
            rec[field] = None
    return rec


def load_coords(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, _, info = line.partition("\t")
        if info:
            out[name] = info
    return out


def parse_hinfo(info: str) -> tuple[float, float] | None:
    """hInfo＝「緯度,經度,鄉鎮,門牌,園名」。"""
    parts = info.split(",")
    try:
        lat, lng = float(parts[0]), float(parts[1])
    except (IndexError, ValueError):
        return None
    return (lat, lng) if 21.5 <= lat <= 26.5 and 118.0 <= lng <= 122.2 else None


def crawl_city(f: Fetcher, data_dir: Path, code: str, name: str,
               new_only: bool, limit: int | None) -> int:
    raw = data_dir / "raw" / "ece_schools"
    coords_path = raw / "coords" / f"{name}.tsv"
    coords = load_coords(coords_path)

    html = f.fetch(BASE)
    if html is None:
        return 0
    html = f.fetch(BASE, data={**form_fields(html), "ddlCityS": code, "btnSearch": "搜尋"})
    if html is None:
        log(f"  {name}：查詢送出失敗")
        return 0

    total_expected = TOTAL_RE.search(html)
    page, count = 1, 0
    while True:
        rows = page_rows(html)
        save(raw / "list" / f"{name}_p{page:04d}.html", strip_session_noise(html))
        for idx in sorted(rows, key=int):
            row = rows[idx]
            school = row.get("name")
            if not school:
                continue
            count += 1
            if row.get("map_token") and not (new_only and school in coords):
                page_html = f.fetch(BASE + f"map/showmap.aspx?new=1&sch={row['map_token']}",
                                    headers={"Referer": BASE})
                info = HINFO_RE.search(page_html or "")
                if info:
                    coords[school] = info.group(1)
            if limit and count >= limit:
                break
        if limit and count >= limit:
            break
        if NEXT_PAGE not in html:
            break
        html = f.fetch(BASE, data={**form_fields(html), "ddlCityS": code,
                                   "__EVENTTARGET": NEXT_PAGE, "__EVENTARGUMENT": ""})
        if html is None:
            break
        page += 1

    save(coords_path, "".join(f"{k}\t{v}\n" for k, v in sorted(coords.items())))
    expected = int(total_expected.group(1)) if total_expected else None
    note = "" if expected in (None, count) else f"（站方稱 {expected} 筆）"
    log(f"  {name}：{page} 頁、{count} 園、座標 {len(coords)}{note}")
    return count


def parse_all(data_dir: Path) -> tuple[list[dict], list[str]]:
    raw = data_dir / "raw" / "ece_schools"
    records, failures = [], []
    for path in sorted((raw / "list").glob("*_p*.html")):
        city = path.stem.rsplit("_p", 1)[0]
        coords = load_coords(raw / "coords" / f"{city}.tsv")
        for idx, row in sorted(page_rows(path.read_text(encoding="utf-8")).items(),
                               key=lambda kv: int(kv[0])):
            if not row.get("name"):
                continue
            rec = normalise(row)
            info = coords.get(rec["name"])
            latlng = parse_hinfo(info) if info else None
            rec["lat"], rec["lng"] = latlng if latlng else (None, None)
            if latlng is None:
                failures.append(f"ece:{rec['name']} 無座標")
            records.append(rec)
    return records, failures


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

    if "--parse-only" not in argv:
        f = Fetcher(delay=delay, verify_tls=False)
        home = f.fetch(BASE)
        if home is None:
            log("無法連上教保網（站方封鎖境外 IP，需從台灣的網路執行）")
            return 1
        targets = [(c, n) for c, n in cities(home) if not only_city or n == only_city]
        log(f"縣市 {len(targets)} 個，請求間隔 {delay} 秒")
        total = 0
        for code, name in targets:
            total += crawl_city(f, data_dir, code, name, "--new-only" in argv, limit)
        log(f"抓取完成，共 {total:,} 園" +
            (f"；失敗 {len(f.failures)} 筆" if f.failures else ""))

    records, failures = parse_all(data_dir)
    out = data_dir / "data" / "ece" / "schools.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"as_of": date.today().isoformat(), "records": records},
                              ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    located = sum(1 for r in records if r["lat"] is not None)
    evaluated = sum(1 for r in records if r["evaluations"])
    log(f"解析 {len(records):,} 園 → {out}"
        f"（有座標 {located:,}、有評鑑紀錄 {evaluated:,}）")
    if failures:
        for line in failures[:5]:
            log(f"  {line}")
        log(f"缺座標 {len(failures)} 園")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
