"""歷史回補（工程執行計劃 2.4）：自 kiang 的 MIT 開放資料回補歷史裁罰。

資料源（皆從 GitHub clone，不觸及 kiang 自家伺服器）：
- crc：kiang/crc.sfaa.gov.tw 的 data/case/{官方id}.json——與我方 sfaa 主鍵同源，
  官方 id 直接去重。
- ece：kiang/ap.ece.moe.edu.tw 的 docs/data/punish/{縣市}/{園名}.json——
  列格式 [日期, 文號, 處分依據, 違反之規定, 對象, 處分內容]；
  以（文號＋日期＋處分內容）多重集合對現有紀錄去重。

回補紀錄的語意：
- origin: "kiang"（自抓快照為 origin 缺省＝official）
- removed_at: "unknown"——官方已不存在、確切下架日不明。
  diff 引擎的下架判定只針對 removed_at is None 的紀錄，回補紀錄天然不受影響；
  若日後官方重新上架同 id，diff 會發 reappeared 事件並轉為官方紀錄。
- 不產生事件：events.jsonl 只記「我方觀測到的異動」，回補是一次性資料匯入。

用法：python3 crawler/backfill_kiang.py --data-dir PATH --kiang-crc PATH --kiang-ece PATH
      [--as-of YYYY-MM-DD] [--dry-run]
"""

import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from common import log
from parse import SFAA_PERSON_CATEGORIES, _digest, norm_date


def norm_key(s: str | None) -> str:
    """去重比對用正規化：移除所有空白與全形空白。"""
    return re.sub(r"[\s　]+", "", s or "")


# ---------- crc（社家署） ----------

def backfill_crc(kiang_dir: Path, state: dict[str, dict]) -> tuple[list[dict], dict]:
    added, stats = [], Counter()
    for f in sorted((kiang_dir / "data" / "case").glob("*.json"),
                    key=lambda p: int(p.stem)):
        case = json.loads(f.read_text(encoding="utf-8"))
        if "裁罰日期" not in case:
            stats["empty_shell"] += 1  # kiang 探測不存在 id 的空殼檔，非真實案件
            continue
        stats["total"] += 1
        rid = f"sfaa:{f.stem}"
        if rid in state:
            stats["dup"] += 1
            continue
        pdate = norm_date(str(case.get("裁罰日期", "")))
        if pdate is None:
            stats["skip_bad"] += 1
            log(f"  跳過 {f.name}：日期無法解析：{case.get('裁罰日期')!r}")
            continue
        targets_raw = case.get("裁罰對象")
        # 絕大多數為 dict；少數空值（官方原頁即無對象），保留為無對象紀錄
        targets = ([{"category": c, "name": n} for c, n in targets_raw.items()]
                   if isinstance(targets_raw, dict) else [])
        if not targets:
            stats["no_target"] += 1
        institutions = [t["name"] for t in targets if t["category"] not in SFAA_PERSON_CATEGORIES]
        persons = [t["name"] for t in targets if t["category"] in SFAA_PERSON_CATEGORIES]
        if not case.get("公告檔案"):
            stats["no_file"] += 1
        added.append({
            "id": rid,
            "source": "sfaa",
            "origin": "kiang",
            "city": case.get("縣市名稱") or None,
            "area": None,
            "institution": "；".join(filter(None, institutions)) or None,
            "person": "；".join(filter(None, persons)) or None,
            "targets": targets,
            "basis": case.get("裁罰依據") or None,
            "law": case.get("違法條文") or None,
            "fact": case.get("事實摘要") or None,
            "sanction": None,
            "penalty_date": pdate,
            "files": [case["公告檔案"]] if case.get("公告檔案") else [],
            "first_seen": None,
            "last_seen": None,
            "removed_at": "unknown",
        })
    return added, stats


# ---------- ece（教保網） ----------

def backfill_ece(kiang_dir: Path, state: dict[str, dict]) -> tuple[list[dict], dict]:
    # 現有紀錄的去重多重集合：(文號, 日期, 處分內容) 正規化後計數
    existing = Counter(
        (norm_key(r.get("doc_no")), r.get("penalty_date"), norm_key(r.get("sanction")))
        for r in state.values())
    # 次級去重：同文號＋日期已有現存紀錄者，kiang 列視為同案變體（多列處分的
    # 列組合差異、處分內容文字改版），不入庫——避免偽歷史紀錄
    existing_case = {(norm_key(r.get("doc_no")), r.get("penalty_date"))
                     for r in state.values()}

    rows_all, stats = [], Counter()
    for city_dir in sorted((kiang_dir / "docs" / "data" / "punish").iterdir()):
        if not city_dir.is_dir():
            continue
        for f in sorted(city_dir.glob("*.json")):
            school = f.stem
            for row in json.loads(f.read_text(encoding="utf-8")):
                stats["total"] += 1
                if len(row) not in (6, 7):
                    stats["skip_bad"] += 1
                    log(f"  跳過 {city_dir.name}/{f.name} 非 6/7 欄列：{row!r:.80}")
                    continue
                # 新版列多第 7 欄＝處分時園名（同官方表格語意）；舊版以檔名（現園名）代
                rdate, doc_no, basis, detail, person, sanction = (s.strip() for s in row[:6])
                inst = row[6].strip() if len(row) == 7 and row[6].strip() else school
                pdate = norm_date(rdate)
                if pdate is None:
                    stats["skip_bad"] += 1
                    log(f"  跳過 {city_dir.name}/{f.name} 日期無法解析：{rdate!r}")
                    continue
                if not doc_no:
                    stats["no_doc_no"] += 1
                key = (norm_key(doc_no), pdate, norm_key(sanction))
                if existing[key] > 0:
                    existing[key] -= 1
                    stats["dup"] += 1
                    continue
                if doc_no and (norm_key(doc_no), pdate) in existing_case:
                    stats["dup_variant"] += 1
                    continue
                rows_all.append({
                    "_key": (inst, pdate, basis, detail),
                    "id": None,
                    "source": "ece",
                    "origin": "kiang",
                    "city": city_dir.name,
                    "area": None,
                    "institution": inst,
                    "school": school,
                    "school_status": None,
                    "person": person or None,
                    "doc_no": doc_no or None,
                    "law": basis or None,
                    "violation": detail or None,
                    "sanction": sanction or None,
                    "penalty_date": pdate,
                    "first_seen": None,
                    "last_seen": None,
                    "removed_at": "unknown",
                })

    # 主鍵指派：沿用 parse.py 的階層式雜湊，並避開已存在的 id
    groups: dict[str, list[dict]] = {}
    for r in rows_all:
        groups.setdefault(_digest(*r.pop("_key")), []).append(r)
    added = []
    for base, group in groups.items():
        if len(group) == 1 and f"ece:{base}" not in state:
            group[0]["id"] = f"ece:{base}"
        else:
            counts: dict[str, int] = {}
            for r in group:
                ext = _digest(base, r.get("doc_no") or "", r.get("sanction") or "")
                counts[ext] = counts.get(ext, 0) + 1
                rid = f"ece:{ext}" if counts[ext] == 1 else f"ece:{ext}-{counts[ext]}"
                while rid in state:
                    rid += "x"  # 與現有官方 id 罕見碰撞時的保底
                r["id"] = rid
        added.extend(group)
    return added, stats


# ---------- 主流程 ----------

def load_state(path: Path) -> dict[str, dict]:
    return {r["id"]: r for r in
            json.loads(path.read_text(encoding="utf-8"))["records"]}


def main(argv: list[str]) -> int:
    def arg(name: str, default=None):
        return Path(argv[argv.index(name) + 1]) if name in argv else default

    data_dir = arg("--data-dir")
    kiang_crc = arg("--kiang-crc")
    kiang_ece = arg("--kiang-ece")
    if not all((data_dir, kiang_crc, kiang_ece)):
        log("需要 --data-dir、--kiang-crc、--kiang-ece")
        return 2
    as_of = argv[argv.index("--as-of") + 1] if "--as-of" in argv else date.today().isoformat()
    dry_run = "--dry-run" in argv

    for source, backfill, kdir in (("sfaa", backfill_crc, kiang_crc),
                                   ("ece", backfill_ece, kiang_ece)):
        state_path = data_dir / "data" / source / "penalties.json"
        state = load_state(state_path)
        added, stats = backfill(kdir, state)
        n = stats["total"]
        log(f"{source}：kiang {n} 筆 → 與現有重複 {stats['dup']}"
            f"（{stats['dup'] / n:.1%}）＋同案變體 {stats.get('dup_variant', 0)}、"
            f"回補 {len(added)}、跳過 {stats['skip_bad']}、"
            f"空殼 {stats.get('empty_shell', 0)}；欄位缺漏：無文號 {stats.get('no_doc_no', 0)}、"
            f"無公告檔 {stats.get('no_file', 0)}、無對象 {stats.get('no_target', 0)}")
        if dry_run:
            continue
        merged = sorted([*state.values(), *added], key=lambda r: r["id"])
        state_path.write_text(
            json.dumps({"as_of": as_of, "backfill_kiang": as_of, "records": merged},
                       ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8")
    if dry_run:
        log("--dry-run：不寫檔")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
