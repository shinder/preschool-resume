"""HTML 快照 → penalty 紀錄解析器（第二階段 2.1）。

輸入：data repo 的 raw/ 快照（list 頁為「目前在線」錨點，detail 頁為內容來源）。
輸出：penalty 紀錄 list[dict]（不含 first_seen 等生命週期欄位，那是 diff 引擎的職責）。

主鍵：
- sfaa 用站方 Detail id → "sfaa:{id}"
- ece 無公開 id，用內容雜湊（處分時園名＋處分日期＋處分依據＋違反之規定）
  → "ece:{sha1 前 16 碼}"；雜湊刻意排除裁處文號與處分內容，讓這兩欄的
  更正走 changed 事件而不是 removed＋new。
  同批撞號時（同園同日同條文的多筆處分，實測 74 組）改用延伸雜湊
  （再納入文號＋處分內容）；仍相同者依列序加序號。代價：原本唯一的
  紀錄若日後出現同鍵新處分，舊紀錄 id 會改變（removed＋new），屬可接受
  的罕見情況。

涵蓋率要求 100%：解析失敗的頁面記入 failures 回報，不靜默丟棄。
"""

import hashlib
import html as htmllib
import json
import re
import sys
from pathlib import Path

from common import log, safe_filename

# ---------- 共用 ----------

DATE_RE = re.compile(r"(\d{4})[./](\d{1,2})[./](\d{1,2})")


def norm_date(s: str) -> str | None:
    """2016.01.28 / 2025/10/08 → ISO 2016-01-28。解析不到回傳 None。"""
    m = DATE_RE.search(s)
    if not m:
        return None
    y, mo, d = m.groups()
    return f"{y}-{int(mo):02d}-{int(d):02d}"


def strip_tags(s: str) -> str:
    """去標籤：<br> 視為換行，其餘標籤移除，實體還原，行內空白收斂。"""
    s = re.sub(r"<\s*/?br\s*/?\s*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = htmllib.unescape(s).replace("\xa0", " ")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in s.splitlines()]
    return "\n".join(ln for ln in lines if ln)


# ---------- 社家署（crc.sfaa.gov.tw） ----------

SFAA_LIST_ID_RE = re.compile(r'href="/ChildYoungLaw/Detail/(\d+)"')
SFAA_ROW_RE = re.compile(
    r'<div role="columnheader">\s*(.*?)\s*</div>\s*<div role="cell">(.*?)</div>\s*</div>',
    re.S,
)
SFAA_TITLE_RE = re.compile(r'<span class="detailTitle">\s*(.*?)\s*</span>', re.S)
SFAA_FILE_RE = re.compile(r'href="(/UploadFile/Sanction/[^"]+)"')

# 裁罰對象分類 → institution／person 欄位歸屬。
# 實測分類：行為人、負責人（皆為人）；兒童及少年福利機構、課後照顧服務班及中心（皆為機構）。
SFAA_PERSON_CATEGORIES = {"行為人", "負責人"}


def parse_sfaa_targets(cell: str) -> list[dict]:
    """裁罰對象 cell → [{"category": 分類, "name": 名稱}]。

    結構：<span class="col..."><span class="detailTitle">分類</span><br />名稱</span>，
    外層 class 有 col／col-12 等變體，可能多個（含空的佔位）。
    """
    targets = []
    for chunk in re.split(r'<span class="col[^"]*">', cell)[1:]:
        m = SFAA_TITLE_RE.search(chunk)
        if not m:
            continue  # 空佔位
        category = strip_tags(m.group(1))
        name = strip_tags(chunk[m.end():])
        if category or name:
            targets.append({"category": category, "name": name})
    return targets


def parse_sfaa(data_dir: Path, failures: list[str]) -> list[dict]:
    raw = data_dir / "raw" / "sfaa"
    online: list[str] = []
    for page in sorted((raw / "list").glob("p*.html")):
        for pid in SFAA_LIST_ID_RE.findall(page.read_text(encoding="utf-8")):
            if pid not in online:
                online.append(pid)

    records = []
    for pid in online:
        path = raw / "detail" / f"{pid}.html"
        if not path.exists():
            failures.append(f"sfaa:{pid} 詳情快照不存在（列表有、detail 缺）")
            continue
        html = path.read_text(encoding="utf-8")
        fields = {strip_tags(h): c for h, c in SFAA_ROW_RE.findall(html)}
        date = norm_date(strip_tags(fields.get("裁罰日期", "")))
        targets = parse_sfaa_targets(fields.get("裁罰對象", ""))
        if date is None or not targets:
            failures.append(f"sfaa:{pid} 缺裁罰日期或裁罰對象（欄位：{sorted(fields)}）")
            continue
        institutions = [t["name"] for t in targets if t["category"] not in SFAA_PERSON_CATEGORIES]
        persons = [t["name"] for t in targets if t["category"] in SFAA_PERSON_CATEGORIES]
        records.append({
            "id": f"sfaa:{pid}",
            "source": "sfaa",
            "city": strip_tags(fields.get("縣市名稱", "")) or None,
            "area": None,
            "institution": "；".join(filter(None, institutions)) or None,
            "person": "；".join(filter(None, persons)) or None,
            "targets": targets,
            "basis": strip_tags(fields.get("裁罰依據", "")) or None,
            "law": strip_tags(fields.get("違法條文", "")) or None,
            "fact": strip_tags(fields.get("事實摘要", "")) or None,
            "sanction": None,  # 站上無此欄，裁罰結果在公告 PDF 內
            "penalty_date": date,
            "files": SFAA_FILE_RE.findall(html),
        })
    return records


# ---------- 全國教保資訊網（ap.ece.moe.edu.tw） ----------

ECE_SPAN_RE = re.compile(
    r'id="GridView1_(lblCity|lblArea|lblSchName|lblPub|lblBStatus)_(\d+)"[^>]*>(.*?)</span>',
    re.S,
)
ECE_DETAIL_SPAN_RE = re.compile(
    r'id="GridView1_(lblPDate|lblPShName|lblPGNum|lblPName|lblPDetail|lblPObject|lblPContent)_(\d+)"[^>]*>(.*?)</span>',
    re.S,
)


def ece_list_rows(html: str) -> list[dict]:
    rows: dict[str, dict] = {}
    for key, idx, val in ECE_SPAN_RE.findall(html):
        rows.setdefault(idx, {})[key] = strip_tags(val)
    return [rows[i] for i in sorted(rows, key=int)]


def parse_ece(data_dir: Path, failures: list[str]) -> list[dict]:
    raw = data_dir / "raw" / "ece"

    # 重演爬蟲的檔名決定邏輯（seen 去重＋同名加序號），把列表列對回 detail 檔
    seen: set[tuple[str, str]] = set()
    used_names: set[str] = set()
    records = []
    for page in sorted((raw / "list").glob("p*.html")):
        for row in ece_list_rows(page.read_text(encoding="utf-8")):
            city, name = row.get("lblCity", ""), row.get("lblSchName", "")
            if not name or (city, name) in seen:
                continue
            seen.add((city, name))
            fname = safe_filename(f"{city}_{name}")
            n = 2
            while fname in used_names:
                fname = safe_filename(f"{city}_{name}_{n}")
                n += 1
            used_names.add(fname)
            path = raw / "detail" / f"{fname}.html"
            if not path.exists():
                failures.append(f"ece:{city}_{name} 詳情快照不存在")
                continue
            records.extend(parse_ece_detail(path, row, city, name, failures))

    # 文號品質告警：站方偶有把別欄內容打進文號欄的紀錄（實測「高市教幼字第
    # 負責人黃俊賢號」——高雄市把負責人姓名填進去了）。原始 HTML 就長這樣，
    # 不是解析瑕疵，所以照實保留（doc_no 也參與撞號時的延伸雜湊，改動會換 id）；
    # 外洩由 public_data 的姓名遮罩擋住。這裡只計數，讓新案例不會默默溜過去。
    # 刻意不進 failures：這是上游資料品質，不該讓每日 diff 停擺。
    odd = [r["doc_no"] for r in records
           if r["doc_no"] and not any(c.isdigit() for c in r["doc_no"])]
    if odd:
        log(f"ece：文號格式異常 {len(odd)} 筆（無任何數字，上游填錯欄）：{sorted(set(odd))}")

    assign_ece_ids(records)
    return records


def _digest(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def assign_ece_ids(records: list[dict]) -> None:
    """階層式主鍵：基本雜湊唯一 → 直接用；撞號組 → 延伸雜湊（＋文號＋處分內容）；
    仍相同 → 依列序加序號。"""
    groups: dict[str, list[dict]] = {}
    for r in records:
        groups.setdefault(_digest(*r.pop("_key")), []).append(r)
    for base, group in groups.items():
        if len(group) == 1:
            group[0]["id"] = f"ece:{base}"
            continue
        ext_counts: dict[str, int] = {}
        for r in group:
            ext = _digest(base, r.get("doc_no") or "", r.get("sanction") or "")
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
            r["id"] = f"ece:{ext}" if ext_counts[ext] == 1 else f"ece:{ext}-{ext_counts[ext]}"


def parse_ece_detail(path: Path, listrow: dict, city: str, name: str,
                     failures: list[str]) -> list[dict]:
    html = path.read_text(encoding="utf-8")
    rows: dict[str, dict] = {}
    for key, idx, val in ECE_DETAIL_SPAN_RE.findall(html):
        rows.setdefault(idx, {})[key] = strip_tags(val)
    if not rows:
        failures.append(f"ece:{city}_{name} 詳情頁無任何處分列（GridView 空）")
        return []

    out = []
    for idx in sorted(rows, key=int):
        r = rows[idx]
        date = norm_date(r.get("lblPDate", ""))
        inst = r.get("lblPShName") or name
        if date is None:
            failures.append(f"ece:{city}_{name} 第 {idx} 列處分日期無法解析：{r.get('lblPDate')!r}")
            continue
        # 內容雜湊主鍵：排除裁處文號與處分內容（其更正應為 changed 事件）；
        # id 由 assign_ece_ids 統一指派，這裡先留雜湊原料
        basis = r.get("lblPName", "")
        detail = r.get("lblPDetail", "")
        out.append({
            "_key": (inst, date, basis, detail),
            "id": None,
            "source": "ece",
            "city": city or None,
            "area": listrow.get("lblArea") or None,
            "institution": inst,
            "school": name,  # 目前園名（處分時園名可能不同）
            "school_status": listrow.get("lblBStatus") or None,
            "person": r.get("lblPObject") or None,
            "doc_no": r.get("lblPGNum") or None,
            "law": basis or None,           # 處分依據
            "violation": detail or None,    # 違反之規定（含條文與事由）
            "sanction": r.get("lblPContent") or None,
            "penalty_date": date,
        })
    return out


# ---------- CLI：涵蓋率檢查 ----------

def main(argv: list[str]) -> int:
    data_dir = Path(argv[argv.index("--data-dir") + 1]) if "--data-dir" in argv \
        else Path(__file__).resolve().parent.parent.parent / "preschool-resume-data"
    dump = Path(argv[argv.index("--dump") + 1]) if "--dump" in argv else None

    failures: list[str] = []
    sfaa = parse_sfaa(data_dir, failures)
    ece = parse_ece(data_dir, failures)

    cats = sorted({t["category"] for r in sfaa for t in r["targets"]})
    log(f"sfaa：{len(sfaa)} 筆（裁罰對象分類：{cats}）")
    log(f"ece：{len(ece)} 筆（{len({r['school'] for r in ece})} 園）")
    log(f"解析失敗：{len(failures)} 筆")
    for msg in failures:
        log(f"  - {msg}")

    if dump:
        dump.write_text(json.dumps(sfaa + ece, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        log(f"已寫出 {dump}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
