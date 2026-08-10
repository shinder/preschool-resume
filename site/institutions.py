"""機構主檔（工程執行計劃 6.1）：幼兒園自建主檔，托嬰中心以 kiang 為底。

地圖需要機構維度與座標，而我方 penalties.json 只到縣市／區層級。

- 幼兒園：`crawler/ece_schools.py` 自教保網抓的 `data/ece/schools.json`。
  原本用 kiang/ap.ece.moe.edu.tw 的 docs/preschools.json，已於 2026-08-10 換掉，
  理由見下方「幼兒園主鍵」。
- 托嬰中心：kiang/ncwisweb.sfaa.gov.tw 的 docs/babycare.json（2,045 家）為底，
  再由 `crawler/ncwis_centers.py` 的站方主檔覆蓋與補欄（見 NCWIS_OVERLAY）。

kiang 那份自 raw.githubusercontent.com 取得，與 2.4 回補同樣不觸及 kiang 自家
伺服器。原檔一併快照到 raw/kiang/ 並記錄 sha256：上游若停更或消失，主檔仍可
重放（沿用「先備份、後解析」原則）。

**幼兒園主鍵**（2026-08-10 全量 7,664 筆實測後定案）：
`digest(縣市 + reg_no + 園名)` 單層雜湊，不做「撞號才延伸」的兩層設計。

- kiang 的 UUID 是內容衍生的（改名就換一顆、舊紀錄不刪），本來就不能沿用。
- `reg_id`（重發證書字號）一年內變動 820 家（10.9%），含它的 id 會大規模崩掉。
- `reg_no`（最初設立許可文號）一年零變動，但**單獨不唯一**：撞號組涵蓋 1,527 筆
  （20%）——雲林 19 家鄉鎮立幼兒園共用一張文號，分班一律沿用母園文號。
- 三欄合起來實測 0 撞號。改採單層是為了避免跨紀錄耦合：兩層設計下，一家新園
  共用某張文號就會讓既有那家從基本雜湊翻成延伸雜湊，id 因別人的變動而改變。
  代價是改名就換 id，但實測改名率上限僅約 0.7%/年，遠小於 20%。
- 日後若真要保住改名前的網址，可另加「改名登錄表」以 (縣市, reg_no) 認出改名
  並沿用舊 id；那是純 override，不會動到沒改名的園。

**個資注意**：幼兒園紀錄含 `owner`（負責人姓名）。依第十節分層原則，此欄位
只供內部歸戶與「同負責人另營機構」的不具名關聯運算，**不得進入公開層**——
把關在 6.3 的白名單投影與其測試，不在本檔。

用法：python3 site/institutions.py [--data-dir PATH] [--as-of YYYY-MM-DD]
      [--no-fetch] [--dry-run]
      --no-fetch  只用 raw/kiang/ 既有快照，不連網
"""

import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "crawler"))

from common import Fetcher, log  # noqa: E402

RAW_BASE = "https://raw.githubusercontent.com/kiang"
SOURCES = (
    # kind, source（對應 penalty.source）, url, 快照檔名
    ("babycare", "sfaa", f"{RAW_BASE}/ncwisweb.sfaa.gov.tw/master/docs/babycare.json",
     "babycare.json"),
)

# 含澎湖、金門、馬祖的台灣本島與離島範圍；落在框外即視為座標錯誤
TW_BBOX = (118.0, 21.5, 122.2, 26.5)

# 托嬰中心地址格式："231新北市新店區中央里中央一街20號1樓"
ADDR_RE = re.compile(r"^(?P<zip>\d{3,6})?\s*(?P<city>..[市縣])(?P<town>.{1,4}?[鄉鎮市區])")

# 品質門檻：超過即 exit 1，讓排程失敗（比照 diff.py 的涵蓋率告警）
MAX_ISSUE_RATE = 0.01
MAX_SHRINK_RATE = 0.10


def clean(v) -> str | None:
    """空字串與純空白視為無值，統一成 None。"""
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def as_int(v) -> int | None:
    if isinstance(v, int):
        return v
    v = (v or "").strip().replace(",", "") if isinstance(v, str) else ""
    return int(v) if v.isdigit() else None


def check_coords(raw_lng, raw_lat, issues: list, iid: str,
                 name: str) -> tuple[float, float] | None:
    """驗證一組經緯度。上游有 4 筆以字串存，一併正規化為 float。"""
    if raw_lng is None or raw_lat is None:
        issues.append({"id": iid, "name": name, "issue": "無座標"})
        return None
    try:
        lng, lat = float(raw_lng), float(raw_lat)
    except (TypeError, ValueError):
        issues.append({"id": iid, "name": name,
                       "issue": f"座標無法解析：{raw_lng!r},{raw_lat!r}"})
        return None
    x0, y0, x1, y1 = TW_BBOX
    if not (x0 <= lng <= x1 and y0 <= lat <= y1):
        issues.append({"id": iid, "name": name, "issue": f"座標落在台灣範圍外：{lng},{lat}"})
        return None
    return lng, lat


def coords(feature: dict, issues: list, iid: str, name: str) -> tuple[float, float] | None:
    c = (feature.get("geometry") or {}).get("coordinates")
    if not c or len(c) < 2:
        issues.append({"id": iid, "name": name, "issue": "無座標"})
        return None
    return check_coords(c[0], c[1], issues, iid, name)


def base_record(kind: str, source: str, native_id: str, name: str,
                lnglat: tuple[float, float] | None, origin: str = "kiang") -> dict:
    return {
        "id": f"{kind}:{native_id}",
        "kind": kind,
        "source": source,      # 對應 penalty.source，供 6.2 對照
        "native_id": native_id,
        "name": name,
        "origin": origin,
        "lng": lnglat[0] if lnglat else None,
        "lat": lnglat[1] if lnglat else None,
    }


def norm_key(s: str | None) -> str:
    """雜湊前的正規化。站方的全形／半形括號與「台／臺」會隨時間飄動（實測
    「昌福非營利幼兒園(委託…)」與「（委託…）」兩種寫法一年內就變過），
    不吸收掉的話 id 會因為純排版差異而改變。"""
    s = unicodedata.normalize("NFKC", (s or "").strip()).replace("台", "臺")
    return re.sub(r"\s+", "", s)


def _digest(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def assign_preschool_ids(rows: list[dict], issues: list) -> dict[int, str]:
    """幼兒園主鍵：digest(縣市 + reg_no + 園名)，見模組 docstring。

    實測全量 0 撞號，但仍留確定性的保底：真撞上就依序追加未正規化園名、
    鄉鎮、序號。只在撞號組內生效，不像兩層設計那樣常態影響五分之一的紀錄。
    """
    base = {i: _digest(norm_key(r.get("city")), norm_key(r.get("reg_no")),
                       norm_key(r.get("name"))) for i, r in enumerate(rows)}
    groups = defaultdict(list)
    for i, d in base.items():
        groups[d].append(i)

    out = {}
    for digest, idxs in groups.items():
        if len(idxs) == 1:
            out[idxs[0]] = digest
            continue
        for seq, i in enumerate(sorted(idxs, key=lambda j: (rows[j].get("town") or "",
                                                            rows[j].get("name") or "", j))):
            r = rows[i]
            out[i] = _digest(digest, r.get("name") or "", r.get("town") or "", str(seq))
            issues.append({"id": f"preschool:{out[i]}", "name": r.get("name") or "",
                           "issue": f"主鍵撞號（縣市+reg_no+園名 相同），延伸為第 {seq} 筆",
                           "gating": False})
    return out


def build_preschool(schools: list[dict], issues: list) -> list[dict]:
    """吃 `crawler/ece_schools.py` 的 data/ece/schools.json。

    站方的基本資料查詢只列現存園所，故 is_active 一律 True；kiang 那份是
    累積不刪的（含 319 家已停辦），這也是換源後家數會少一些的原因。
    """
    rows = [r for r in schools if clean(r.get("name"))]
    for r in schools:
        if not clean(r.get("name")):
            issues.append({"id": "preschool:?", "name": "", "issue": "缺園名"})
    ids = assign_preschool_ids(rows, issues)

    out = []
    for i, p in enumerate(rows):
        native_id, name = ids[i], clean(p.get("name"))
        iid = f"preschool:{native_id}"
        city, town = clean(p.get("city")), clean(p.get("town"))
        if not clean(p.get("address")):
            issues.append({"id": iid, "name": name, "issue": "無地址"})
        r = base_record("preschool", "ece", native_id, name,
                        check_coords(p.get("lng"), p.get("lat"), issues, iid, name),
                        origin="ece")
        r.update({
            "city": city,
            "town": town,
            "postal_code": clean(p.get("postal_code")),
            "address": clean(p.get("address")),
            "phone": clean(p.get("phone")),
            "url": clean(p.get("url")),
            "org_type": clean(p.get("org_type")),        # 公立／私立／非營利
            "capacity_approved": as_int(p.get("capacity_approved")),
            "pre_public": clean(p.get("pre_public")),    # 準公共化資格年度
            # 五歲免學費與月費參考只在收費明細頁，基本資料查詢沒有；
            # 換源後暫缺，template 以 if 包住，不會留空列。
            "free5": None,
            "monthly_fee": None,
            "after_school": clean(p.get("after_school")),  # 課後留園
            "shuttle": clean(p.get("shuttle")),          # 幼童專用車
            "floor": clean(p.get("floor")),
            "area_total": clean(p.get("area_total")),
            "area_indoor": clean(p.get("area_indoor")),
            "area_outdoor": clean(p.get("area_outdoor")),
            "reg_no": clean(p.get("reg_no")),            # 最初設立許可文號（主鍵原料）
            "reg_date": clean(p.get("reg_date")),
            "reg_id": clean(p.get("reg_id")),            # 重發證書字號，一年變動一成
            "evaluations": p.get("evaluations") or [],   # 多年度評鑑歷史
            "is_active": True,
            # 個資，僅供內部歸戶；不得進公開層（見模組 docstring）
            "owner": clean(p.get("owner")),
        })
        out.append(r)
    return out


def load_ncwis(data_dir: Path) -> tuple[dict[str, dict], str | None]:
    """托嬰中心主檔（`crawler/ncwis_centers.py`）。沒有就退回只用 kiang 的投影。

    一併回傳主檔的資料日期：實際收托是站方回報值、非即時，缺額必須標時點。
    """
    path = data_dir / "data" / "ncwis" / "centers.json"
    if not path.exists():
        return {}, None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {r["id"]: r for r in payload["records"]}, payload.get("as_of")


# ncwis 直接覆蓋的欄位：站方是權威來源，kiang 只是它的 8 欄投影
NCWIS_OVERLAY = ("address", "phone", "capacity_approved", "enrolled", "owner")
# ncwis 才有的欄位：機構頁的內容厚度靠這些
NCWIS_EXTRA = ("grade", "grade_year", "area_total", "area_indoor", "area_outdoor",
               "safety_public", "safety_fire", "staff", "fees", "fee_mode",
               "refund_note", "note_name")


def apply_ncwis(rec: dict, ncwis: dict, issues: list) -> dict:
    """把 ncwis 的欄位疊上 kiang 的底稿。

    座標優先用站方地圖標記反算的值——kiang 有 6% 是自行地理編碼的結果，
    與站方標記差距逾 500 公尺，站方自己標的才是權威值。
    """
    rec["origin"] = "ncwis"
    rec["enrolled_as_of"] = NCWIS_AS_OF
    if ncwis.get("lat") is not None:
        rec["lat"], rec["lng"] = ncwis["lat"], ncwis["lng"]
        rec["geo_origin"] = "ncwis"
    else:
        rec["geo_origin"] = "kiang" if rec.get("lat") is not None else None
    for field in NCWIS_OVERLAY:
        if (value := ncwis.get(field)) not in (None, ""):
            rec[field] = value
    for field in NCWIS_EXTRA:
        rec[field] = ncwis.get(field)
    # 準公共在兩本名冊語意不同：幼兒園是年度區間、托嬰是是／否
    rec["pre_public"] = "是" if ncwis.get("pre_public") == "是" else None
    if ncwis.get("address"):
        m = ADDR_RE.match(ncwis["address"])
        if m:
            rec["postal_code"] = m.group("zip") or rec.get("postal_code")
            rec["city"], rec["town"] = m.group("city"), m.group("town")
            rec["address"] = ncwis["address"][len(m.group("zip") or ""):]
    return rec


def build_babycare(features: list[dict], issues: list) -> list[dict]:
    out = []
    for f in features:
        p = f["properties"]
        native_id, name = clean(p.get("id")), clean(p.get("name")) or ""
        iid = f"babycare:{native_id}"
        if not native_id or not name:
            issues.append({"id": iid, "name": name, "issue": "缺 CW 編號或機構名"})
            continue
        # 上游 city 是抓取批次的檔名，有時是「新北市新店區」有時只有縣市；
        # 地址含郵遞區號與完整縣市區，優先從地址解析，解不出才退回 city 欄位。
        addr = clean(p.get("address")) or ""
        m = ADDR_RE.match(addr)
        if m:
            postal, city, town = m.group("zip"), m.group("city"), m.group("town")
            street = addr[m.end():]
            addr = addr[len(postal):] if postal else addr  # 郵遞區號另存，地址不重複帶
        else:
            postal, street = None, addr
            raw_city = clean(p.get("city")) or ""
            c = re.match(r"^(..[市縣])(.*)$", raw_city)
            city, town = (c.group(1), c.group(2) or None) if c else (raw_city or None, None)
            issues.append({"id": iid, "name": name, "issue": f"地址無法解析縣市區：{addr!r}"})
        if not street:
            issues.append({"id": iid, "name": name, "issue": "無地址"})
        r = base_record("babycare", "sfaa", native_id, name,
                        coords(f, issues, iid, name))
        r.update({
            "city": city,
            "town": town,
            "postal_code": postal,
            "address": addr or None,
            "phone": clean(p.get("phone")),
            "capacity_approved": as_int(p.get("capacity")),  # 核定收托
            "enrolled": as_int(p.get("status")),             # 實際收托（上游快照時點）
            "is_active": bool(p.get("is_active")),
        })
        if (extra := NCWIS.get(native_id)):
            apply_ncwis(r, extra, issues)
        else:
            # 站方名冊已無（多為停辦），只剩 kiang 的紀錄
            r["geo_origin"] = "kiang" if r.get("lat") is not None else None
        out.append(r)

    # ncwis 有、kiang 沒有的中心（kiang 以地理編碼失敗為由濾掉約 300 家，另有新立案者）：
    # 一併收錄，只是沒有座標——列表與機構頁看得到，地圖上標不出來
    seen = {r["native_id"] for r in out}
    for cw, extra in sorted(NCWIS.items()):
        if cw in seen:
            continue
        r = base_record("babycare", "sfaa", cw, extra["name"], None)
        r.update({"city": None, "town": None, "postal_code": None,
                  "is_active": True})
        apply_ncwis(r, extra, issues)
        if r.get("lat") is None:
            # 已知的缺口而非資料異常，不計入品質門檻（gating=False）
            issues.append({"id": r["id"], "name": r["name"], "gating": False,
                           "issue": "無座標（站方地圖標記為空）"})
        out.append(r)
    return out


BUILDERS = {"preschool": build_preschool, "babycare": build_babycare}
NCWIS: dict[str, dict] = {}   # main() 載入後填入，供 build_babycare 使用
NCWIS_AS_OF: str | None = None


def fetch_sources(raw_dir: Path, no_fetch: bool) -> dict[str, dict] | None:
    """下載（或沿用快照）兩份 GeoJSON。回傳 kind → {path, sha256, bytes}。"""
    raw_dir.mkdir(parents=True, exist_ok=True)
    fetcher = None if no_fetch else Fetcher()
    got = {}
    for kind, _source, url, filename in SOURCES:
        path = raw_dir / filename
        if fetcher is not None:
            raw = fetcher.fetch_bytes(url)
            if raw is None:
                if not path.exists():
                    log(f"下載失敗且無既有快照：{url}")
                    return None
                log(f"下載失敗，沿用既有快照：{path.name}")
            elif not path.exists() or path.read_bytes() != raw:
                path.write_bytes(raw)
                log(f"更新快照：{path.name}（{len(raw):,} bytes）")
        if not path.exists():
            log(f"缺少快照（--no-fetch 需要既有檔案）：{path}")
            return None
        data = path.read_bytes()
        got[kind] = {"url": url, "path": path, "bytes": len(data),
                     "sha256": hashlib.sha256(data).hexdigest()}
    return got


def load_schools(data_dir: Path) -> tuple[list[dict], str | None]:
    """幼兒園主檔（`crawler/ece_schools.py`）。缺檔即中止：不像托嬰中心有
    kiang 那份可退，這是幼兒園唯一的主檔來源。"""
    path = data_dir / "data" / "ece" / "schools.json"
    if not path.exists():
        return [], None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("records", []), payload.get("as_of")


def previous_counts(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    counts: dict[str, int] = {}
    for r in json.loads(path.read_text(encoding="utf-8")).get("records", []):
        counts[r.get("kind")] = counts.get(r.get("kind"), 0) + 1
    return counts


def main(argv: list[str]) -> int:
    def opt(name: str, default=None):
        return argv[argv.index(name) + 1] if name in argv else default

    known = {"--data-dir", "--as-of", "--no-fetch", "--dry-run"}
    for a in argv:
        if a.startswith("--") and a not in known:
            log(f"未知參數：{a}")
            return 2

    default_data = Path(__file__).resolve().parent.parent.parent / "preschool-resume-data"
    data_dir = Path(opt("--data-dir", default_data))
    as_of = opt("--as-of", date.today().isoformat())
    dry_run = "--dry-run" in argv
    out_path = data_dir / "data" / "institutions.json"

    if not (data_dir / "data").is_dir():
        log(f"資料 repo 不存在或結構不符：{data_dir}")
        return 2

    got = fetch_sources(data_dir / "raw" / "kiang", "--no-fetch" in argv)
    if got is None:
        return 1

    schools, schools_as_of = load_schools(data_dir)
    if not schools:
        log("缺少幼兒園主檔 data/ece/schools.json，先跑 crawler/ece_schools.py")
        return 1

    global NCWIS, NCWIS_AS_OF
    NCWIS, NCWIS_AS_OF = load_ncwis(data_dir)
    if NCWIS:
        log(f"托嬰中心主檔 {len(NCWIS):,} 家（crawler/ncwis_centers.py）")

    records, issues, counts = [], [], {}
    built = build_preschool(schools, issues)
    counts["preschool"] = len(built)
    records.extend(built)
    log(f"preschool：主檔 {len(schools):,} 筆 → 入庫 {len(built):,} 筆"
        f"（as_of {schools_as_of}）")
    for kind, _source, _url, _filename in SOURCES:
        features = json.loads(got[kind]["path"].read_text(encoding="utf-8"))["features"]
        built = BUILDERS[kind](features, issues)
        counts[kind] = len(built)
        records.extend(built)
        log(f"{kind}：上游 {len(features):,} 筆 → 入庫 {len(built):,} 筆")

    # 品質門檻一：id 重複代表上游主鍵語意改變，後續對照會錯接，直接中止
    dups = [i for i, n in Counter(r["id"] for r in records).items() if n > 1]
    if dups:
        log(f"中止：機構 id 重複 {len(dups)} 筆，例：{dups[:5]}")
        return 1

    # 品質門檻二：座標／地址缺漏率（gating=False 的是已知缺口，不計入）
    gating = [i for i in issues if i.get("gating", True)]
    rate = len(gating) / len(records) if records else 1.0
    known = len(issues) - len(gating)
    log(f"合計 {len(records):,} 家；資料問題 {len(gating)} 筆（{rate:.2%}）"
        + (f"，另有已知缺口 {known} 筆" if known else ""))
    for it in issues[:20]:
        log(f"  {it['id']} {it['name']}：{it['issue']}")
    if len(issues) > 20:
        log(f"  …另有 {len(issues) - 20} 筆，完整清單見 institutions_issues.json")

    # 品質門檻三：任一類別筆數大幅縮水，多半是上游改版而非真的關了那麼多家
    prev = previous_counts(out_path)
    shrunk = [f"{k}：{prev[k]:,} → {counts.get(k, 0):,}" for k in prev
              if counts.get(k, 0) < prev[k] * (1 - MAX_SHRINK_RATE)]

    ok = True
    if rate > MAX_ISSUE_RATE:
        log(f"中止：資料問題率 {rate:.2%} 超過門檻 {MAX_ISSUE_RATE:.0%}")
        ok = False
    if shrunk:
        log(f"中止：機構數大幅縮水（門檻 {MAX_SHRINK_RATE:.0%}）：{'；'.join(shrunk)}")
        ok = False
    if not ok:
        return 1

    if dry_run:
        log("--dry-run：不寫檔")
        return 0

    out = {
        "as_of": as_of,
        "sources": [{"kind": "preschool", "url": "https://ap.ece.moe.edu.tw/webecems/",
                     "via": "crawler/ece_schools.py", "as_of": schools_as_of,
                     "license": "政府資料開放授權條款—第 1 版"}]
        + [{"kind": k, "url": got[k]["url"], "sha256": got[k]["sha256"],
            "bytes": got[k]["bytes"], "license": "MIT (kiang)"}
           for k, *_ in SOURCES],
        "records": sorted(records, key=lambda r: r["id"]),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    log(f"寫入 {out_path}")

    issues_path = out_path.parent / "institutions_issues.json"
    if issues:
        issues_path.write_text(
            json.dumps({"as_of": as_of, "issues": issues}, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8")
        log(f"寫入 {issues_path}（{len(issues)} 筆）")
    elif issues_path.exists():
        issues_path.unlink()
        log(f"問題清單已清空，移除 {issues_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
