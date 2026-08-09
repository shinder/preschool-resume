"""公開層投影（工程執行計劃 6.3）：把私有資料投影成可公開發佈的建置輸入。

資料 repo 是 private、本站是公開的，兩者之間必須有一道明確的閘門。本模組就是
那道閘門，設計原則有三：

1. **白名單，不是黑名單**。輸出是逐欄位重建的新物件，不是複製後刪欄位——
   上游日後新增任何欄位，預設不會外流。
2. **自由文字要遮罩**。白名單擋得住 `person` 欄位，擋不住「事實摘要」裡寫著
   「陳○○於托嬰中心不當對待兒童」。實測 sfaa 有 74 筆如此，故對每筆裁罰的
   關係人姓名（person／targets／機構負責人）在自由文字中逐一遮罩為「陳○○」。
3. **產物自我稽核**。輸出序列化後，用私有資料算出的完整姓名集合回頭掃一遍，
   掃到任何一個就 exit 1。這是 6.3 的完成定義，也是 SSG 相對 SSR 的關鍵好處：
   產物是可 grep 的。

依第十節與 MVP 決策，自然人裁罰整筆不進公開層，負責人姓名一律不輸出。

用法：python3 site/public_data.py [--data-dir PATH] [--out PATH]
      [--as-of YYYY-MM-DD] [--dry-run]
"""

import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "crawler"))

from common import log  # noqa: E402

# 公開層欄位白名單。要新增欄位就得動這裡，且必須想清楚該欄位是法人資料還是個資。
INSTITUTION_FIELDS = (
    "id", "kind", "source", "native_id", "name", "city", "town", "postal_code",
    "address", "lng", "lat", "phone", "url", "org_type", "capacity_approved",
    "enrolled", "pre_public", "free5", "after_school", "shuttle", "monthly_fee",
    "floor", "area_indoor", "area_outdoor", "reg_no", "reg_date", "is_active",
    # 托嬰中心主檔（ncwis）才有的欄位。負責人姓名（owner）刻意不在此列。
    "grade", "grade_year", "area_total", "safety_public", "safety_fire",
    "staff", "fees", "fee_mode", "refund_note", "note_name",
)
PENALTY_FIELDS = (
    "id", "source", "institution_id", "city", "area", "doc_no", "law",
    "violation", "fact", "sanction", "penalty_date", "removed_at", "origin",
    "name_at_penalty",
)
ALIAS_FIELDS = (
    "institution_id", "current_name", "former_name", "type", "penalty_count",
    "first_penalty_date", "last_penalty_date",
)
# 姓名集合的雜訊過濾：這些是職稱、狀態註記或機構詞，不是人名
ROLE_WORDS = {"負責人", "行為人", "業者", "從業人員", "托育人員", "機構", "無",
              "未立案", "代表人", "園長", "教保員", "主管人員", "其他",
              "已更名", "更名", "停業", "已歇業", "歇業", "原名"}
INST_WORDS = ("幼兒園", "托嬰中心", "教保", "課後照顧", "托育中心", "幼稚園", "中心")
NAME_TOKEN_RE = re.compile(r"[一-鿿]{2,4}$")
# 括號與英文句點也要當分隔符——實測有「…托嬰中心(鍾○○.鍾○○.鍾○○)」這種寫法
SPLIT_RE = re.compile(r"[；;、,／/\s：:（）()．.·・]+")


def name_tokens(s: str | None) -> list[str]:
    """從「負責人：王大明；李小美」這類欄位切出可能的自然人姓名。"""
    out = []
    for tok in SPLIT_RE.split(s or ""):
        tok = tok.strip()
        if (NAME_TOKEN_RE.fullmatch(tok) and tok not in ROLE_WORDS
                and not any(w in tok for w in INST_WORDS)):
            out.append(tok)
    return out


def related_names(penalty: dict) -> set[str]:
    """一筆裁罰的關係人姓名（遮罩範圍就是這些，不是全域姓名集）。"""
    names = set(name_tokens(penalty.get("person")))
    for t in (penalty.get("targets") or []):
        names.update(name_tokens(t.get("name")))
    names.update(name_tokens(penalty.get("institution")))
    return names


def mask(name: str) -> str:
    return name[0] + "○" * (len(name) - 1)


def mask_text(text: str | None, names: set[str]) -> str | None:
    if not text:
        return text
    for n in sorted(names, key=len, reverse=True):
        text = text.replace(n, mask(n))
    return text


def pick(record: dict, fields: tuple, names: set[str] | None = None) -> dict:
    """逐欄位重建（白名單生效處，不要改成 copy + del），並對所有字串套遮罩。

    遮罩不限於自由文字：實測機構名帶著「(負責人姓名)」、甚至有一筆 ece 的文號
    被解析成「高市教幼字第負責人○○○號」。既然遮罩只替換本筆的關係人姓名，
    對正常欄位是無操作，全欄位套用純屬白拿的縱深防禦。
    """
    out = {f: record.get(f) for f in fields}
    if names:
        for f, v in out.items():
            if isinstance(v, str):
                out[f] = mask_text(v, names)
            elif isinstance(v, list):   # 如收費明細，逐項的字串也要遮
                out[f] = [{k: mask_text(x, names) if isinstance(x, str) else x
                           for k, x in item.items()} if isinstance(item, dict)
                          else mask_text(item, names) if isinstance(item, str) else item
                          for item in v]
    return out


def build_name_scanner(penalties: list[dict], institutions: list[dict]) -> set[str]:
    """稽核用的完整姓名集合。排除同時是機構名子字串的姓名，避免誤報。"""
    names: set[str] = set()
    for p in penalties:
        names |= related_names(p)
    for r in institutions:
        names.update(name_tokens(r.get("owner")))
    inst_blob = " ".join(r["name"] for r in institutions)
    return {n for n in names if n not in inst_blob}


def project_institutions(institutions: list[dict], summary: dict) -> list[dict]:
    out = []
    for r in institutions:
        pub = pick(r, INSTITUTION_FIELDS, set(name_tokens(r.get("owner"))))
        s = summary.get(r["id"], {})
        pub["penalty_count"] = s.get("count", 0)
        pub["last_penalty_date"] = s.get("last_date")
        # 兩種「官方已無、本站仍有」要分開講：
        # removed  ＝ 我方觀測到的下架（有確切日期），護城河的計量單位
        # archived ＝ 2.4 回補的歷史紀錄（removed_at="unknown"，下架日不明）
        pub["removed_penalty_count"] = s.get("removed", 0)
        pub["archived_penalty_count"] = s.get("archived", 0)
        out.append(pub)
    return out


def project_penalties(penalties: list[dict], links: dict,
                      inst_names: dict) -> tuple[list[dict], dict]:
    out, summary = [], defaultdict(
        lambda: {"count": 0, "last_date": None, "removed": 0, "archived": 0})
    for p in penalties:
        link = links.get(p["id"])
        if not link:
            continue                      # 未對上機構者不進公開層（含全部自然人裁罰）
        iid = link["institution_id"]
        names = related_names(p)
        pub = pick(p, PENALTY_FIELDS, names)
        pub["institution_id"] = iid
        # 處分時名稱與現名不同時保留（機構名屬法人資料，可公開，也是換牌線索）
        former = p.get("institution")
        pub["name_at_penalty"] = (mask_text(former, names)
                                  if former and former != inst_names.get(iid) else None)
        out.append(pub)
        s = summary[iid]
        s["count"] += 1
        d = p.get("penalty_date")
        if d:
            s["last_date"] = max(s["last_date"] or d, d)
        if p.get("removed_at") == "unknown":
            s["archived"] += 1
        elif p.get("removed_at"):
            s["removed"] += 1
    return out, summary


def scan_leaks(payloads: dict[str, object], names: set[str]) -> list[tuple[str, str]]:
    """把輸出序列化後回頭掃姓名。找到即代表白名單或遮罩有漏。"""
    found = []
    for filename, payload in payloads.items():
        blob = json.dumps(payload, ensure_ascii=False)
        for n in names:
            if n in blob:
                found.append((filename, n))
    return found


def main(argv: list[str]) -> int:
    def opt(name: str, default=None):
        return argv[argv.index(name) + 1] if name in argv else default

    known = {"--data-dir", "--out", "--as-of", "--dry-run"}
    for a in argv:
        if a.startswith("--") and a not in known:
            log(f"未知參數：{a}")
            return 2

    repo = Path(__file__).resolve().parent.parent
    data_dir = Path(opt("--data-dir", repo.parent / "preschool-resume-data"))
    out_dir = Path(opt("--out", repo / "build" / "public"))
    as_of = opt("--as-of", date.today().isoformat())
    dry_run = "--dry-run" in argv

    def load(rel: str, key: str):
        path = data_dir / "data" / rel
        if not path.exists():
            log(f"缺少 {path}")
            return None
        return json.loads(path.read_text(encoding="utf-8"))[key]

    institutions = load("institutions.json", "records")
    links = load("penalty_institution.json", "links")
    aliases = load("aliases.json", "aliases")
    if institutions is None or links is None or aliases is None:
        log("請先執行 site/institutions.py 與 site/match.py")
        return 2
    penalties = []
    for source in ("ece", "sfaa"):
        rs = load(f"{source}/penalties.json", "records")
        if rs is None:
            return 2
        penalties.extend(rs)

    inst_names = {r["id"]: r["name"] for r in institutions}
    pub_penalties, summary = project_penalties(penalties, links, inst_names)
    pub_institutions = project_institutions(institutions, summary)
    # renamed_unlinked 的舊名裡混著人名且未經確認，MVP 不公開
    pub_aliases = [pick(a, ALIAS_FIELDS,
                        set(name_tokens(a.get("former_name")))
                        | set(name_tokens(a.get("current_name"))))
                   for a in aliases if a["type"] != "renamed_unlinked"]

    with_penalty = sum(1 for r in pub_institutions if r["penalty_count"])
    removed = sum(r["removed_penalty_count"] for r in pub_institutions)
    archived = sum(r["archived_penalty_count"] for r in pub_institutions)
    log(f"公開層：機構 {len(pub_institutions):,} 家（有裁罰 {with_penalty:,}）、"
        f"裁罰 {len(pub_penalties):,} 筆（我方觀測到下架 {removed:,}、"
        f"歷史存檔 {archived:,}）、別名 {len(pub_aliases)} 組")
    log(f"未進公開層的裁罰 {len(penalties) - len(pub_penalties):,} 筆"
        f"（自然人、未立案、名冊查無等，見 match_unresolved.json）")

    payloads = {
        "institutions.json": {"as_of": as_of, "records": pub_institutions},
        "penalties.json": {"as_of": as_of, "records": pub_penalties},
        "aliases.json": {"as_of": as_of, "aliases": pub_aliases},
        "meta.json": {
            "as_of": as_of,
            "counts": {"institutions": len(pub_institutions),
                       "institutions_with_penalty": with_penalty,
                       "penalties": len(pub_penalties),
                       "removed_penalties": removed,
                       "archived_penalties": archived,
                       "aliases": len(pub_aliases)},
            "sources": [
                {"name": "幼兒園裁罰：全國教保資訊網（教育部）", "url":
                 "https://ap.ece.moe.edu.tw/webecems/punishSearch.aspx"},
                {"name": "托嬰中心裁罰：違反兒少法裁罰專區（衛福部社家署）", "url":
                 "https://crc.sfaa.gov.tw/ChildYoungLaw/Sanction"},
                {"name": "托嬰中心基本資料、評鑑與收費：托育媒合平臺（衛福部社家署）",
                 "url": "https://ncwisweb.sfaa.gov.tw/home/childcare-center"},
                {"name": "幼兒園基本資料與地圖座標：kiang 開放資料（MIT）", "url":
                 "https://github.com/kiang"},
            ],
            "notice": "自然人裁罰不予公開；公文所載姓名一律遮罩。",
        },
    }

    # 完成定義：產物掃不到任何自然人姓名
    scanner = build_name_scanner(penalties, institutions)
    leaks = scan_leaks(payloads, scanner)
    log(f"姓名稽核：比對 {len(scanner):,} 個已知姓名 → 命中 {len(leaks)}")
    if leaks:
        for filename, n in leaks[:20]:
            log(f"  外洩：{filename} 含「{n}」")
        log("中止：公開層產物含自然人姓名")
        return 1

    # 白名單自我檢查：輸出不得出現白名單以外的欄位
    for rows, allowed, label in ((pub_institutions, INSTITUTION_FIELDS + (
            "penalty_count", "last_penalty_date", "removed_penalty_count",
            "archived_penalty_count"), "機構"),
            (pub_penalties, PENALTY_FIELDS, "裁罰"),
            (pub_aliases, ALIAS_FIELDS, "別名")):
        extra = {k for r in rows for k in r} - set(allowed)
        if extra:
            log(f"中止：{label}輸出含白名單外欄位 {sorted(extra)}")
            return 1

    if dry_run:
        log("--dry-run：不寫檔")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    for filename, payload in payloads.items():
        (out_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log(f"寫入 {out_dir}（{len(payloads)} 檔）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
