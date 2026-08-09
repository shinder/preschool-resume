"""裁罰↔機構對照（工程執行計劃 6.2）：把 penalty 接到 institutions.json 的機構。

輸入：data/institutions.json（6.1）、data/{ece,sfaa}/penalties.json（2.x）
輸出（皆在資料 repo 的 data/ 下）：
- penalty_institution.json  penalty_id → institution_id，附命中方法
- aliases.json              改名／換牌線索（處分時名稱 → 現名），第四階段歸戶素材
- match_unresolved.json     未對上者，全部帶分類標籤供人工覆核

**未對上不等於出錯**。相當比例的裁罰在名冊裡本來就沒有對應機構：

- person      裁罰對象是自然人（sfaa 逾九成如此），依第十節不進公開層
- unlicensed  未立案／稽查時無招牌——未立案正是被罰的原因，名冊當然沒有
- out_of_scope  課後照顧服務班及中心、非托嬰之兒少福利機構，不在本站兩類名冊
- misclassified 站方歸類為機構、實際是人名（無任何機構關鍵字）
- renamed_unlinked  註明「已更名／變更負責人」但接不到現機構——仍收進 aliases 當換牌線索
- closed      名稱像機構但不在名冊，多為已停辦——這才是真正的漏，計入對照率分母

用法：python3 site/match.py [--data-dir PATH] [--as-of YYYY-MM-DD] [--dry-run]
"""

import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "crawler"))

from common import log  # noqa: E402

# 機構關鍵字：判斷一個名稱是「機構」還是「人名」
INST_WORDS = ("幼兒園", "托嬰中心", "教保服務中心", "課後照顧", "托育中心", "幼稚園")
UNLICENSED_RE = re.compile(r"未立案|尚無招牌|無招牌")
# 括號註記裡常見的雜訊前後綴，剝掉才看得出裡面是不是機構名
PAREN_NOISE_RE = re.compile(
    r"^(?:前?委託|稽查時招牌為|稽查時|原名|前身為|後有變更負責人|現已?辦理?變更|已?歇業|停業|負責人)+|"
    r"(?:辦理|後有變更負責人|現已變更負責人及中心名稱|已辦理變更負責人及中心名稱|已歇業|停業)+$")
PAREN_RE = re.compile(r"[（(]([^)）]*)[)）]")
# 括號內若出現這些字樣，代表該筆本身就是換牌／易主線索
CHANGE_WORDS = ("變更負責人", "變更名稱", "中心名稱", "稽查時招牌為", "原名", "前身")

ECE_MIN_MATCH_RATE = 0.95
MAX_SHRINK_RATE = 0.10


def norm(s: str | None) -> str:
    """比對用正規化：臺／台統一、全形轉半形、去空白與常見標點（含括號字元）。"""
    s = unicodedata.normalize("NFKC", (s or "")).replace("台", "臺")
    return re.sub(r"[\s　·・、,，。．.\-—－_/／()]+", "", s)


def strip_paren(s: str | None) -> str:
    """移除整段括號註記後再正規化——非營利園換受託單位時靠這個仍能對上同一園。"""
    return norm(PAREN_RE.sub("", s or ""))


def paren_parts(s: str | None) -> list[str]:
    """取出括號內容並剝除註記雜訊；某些 sfaa 紀錄把機構名寫在括號裡、人名在外面。"""
    out = []
    for m in PAREN_RE.finditer(s or ""):
        inner = PAREN_NOISE_RE.sub("", m.group(1).strip(" ，,。.、"))
        if inner:
            out.append(inner)
    return out


def is_institution_name(s: str | None) -> bool:
    return any(w in (s or "") for w in INST_WORDS)


class Index:
    """機構名索引。exact 走（縣市, 名稱），另備全國唯一名與去括號名兩張表。"""

    def __init__(self, records: list[dict]):
        self.by_city_name: dict[tuple, dict] = {}
        self.by_name: dict[tuple, list] = defaultdict(list)
        self.by_city_stripped: dict[tuple, list] = defaultdict(list)
        for r in records:
            kind, city = r["kind"], norm(r["city"])
            self.by_city_name[(kind, city, norm(r["name"]))] = r
            self.by_name[(kind, norm(r["name"]))].append(r)
            self.by_city_stripped[(kind, city, strip_paren(r["name"]))].append(r)

    def find(self, kind: str, city: str, name: str) -> tuple[dict | None, str]:
        """回傳（機構, 命中方法）。方法名會寫進對照檔，方便日後回頭稽核。"""
        c, n = norm(city), norm(name)
        if not n:
            return None, ""
        if (hit := self.by_city_name.get((kind, c, n))):
            return hit, "exact"
        # 名稱漏了縣市前綴（如「私立華興托嬰中心」）
        if (hit := self.by_city_name.get((kind, c, norm(city) + n))):
            return hit, "city_prefix"
        # 非營利園換受託單位：去掉「（委託○○辦理）」後仍是同一園
        cand = self.by_city_stripped.get((kind, c, strip_paren(name)))
        if cand and len(cand) == 1:
            return cand[0], "paren_strip"
        # 全國同名唯一時允許跨縣市命中（縣市欄位本身也可能有誤）
        cand = self.by_name.get((kind, n))
        if cand and len(cand) == 1:
            return cand[0], "name_only"
        return None, ""


def candidates(penalty: dict, kind: str) -> list[str]:
    """一筆裁罰可用來比對的名稱，依可信度排序。"""
    if kind == "preschool":
        # school＝列表頁現園名（較接近名冊現況），institution＝處分時園名
        names = [penalty.get("school"), penalty.get("institution")]
    else:
        names = [penalty.get("institution")]
        # sfaa 有一批把機構名塞在括號、人名放外面
        names += [p for p in paren_parts(penalty.get("institution"))
                  if is_institution_name(p)]
    seen, out = set(), []
    for n in names:
        if n and norm(n) not in seen:
            seen.add(norm(n))
            out.append(n)
    return out


def classify_unresolved(penalty: dict, names: list[str]) -> str:
    raw = " ".join(names)
    if UNLICENSED_RE.search(raw):
        return "unlicensed"
    cats = {t.get("category") for t in (penalty.get("targets") or [])}
    if "課後照顧服務班及中心" in cats:
        return "out_of_scope"
    if not is_institution_name(raw):
        return "misclassified"
    if any(w in raw for w in CHANGE_WORDS + ("已更名",)):
        return "renamed_unlinked"
    return "closed"


def kinds_for(name: str, default: str) -> list[str]:
    """名稱關鍵字決定查哪本名冊——兒少法也罰幼兒園，sfaa 裡混著幼兒園紀錄。"""
    if "托嬰中心" in name:
        return ["babycare"]
    if any(w in name for w in ("幼兒園", "幼稚園", "教保服務中心")):
        return ["preschool"]
    return [default, "babycare" if default == "preschool" else "preschool"]


def match_source(penalties: list[dict], kind: str, index: Index) -> dict:
    links, aliases, unresolved = {}, [], []
    stats = Counter()
    for p in penalties:
        names = candidates(p, kind)
        if not names:
            stats["person"] += 1          # 純自然人裁罰，本就無機構可對
            unresolved.append({"penalty_id": p["id"], "source": p["source"],
                               "city": p.get("city"), "name": None,
                               "class": "person", "reason": "無機構名（自然人裁罰）"})
            continue
        hit = method = matched_name = None
        for name in names:
            for k in kinds_for(name, kind):
                hit, method = index.find(k, p.get("city") or "", name)
                if hit:
                    matched_name = name
                    break
            if hit:
                break
        if not hit:
            cls = classify_unresolved(p, names)
            stats[cls] += 1
            unresolved.append({"penalty_id": p["id"], "source": p["source"],
                               "city": p.get("city"), "name": names[0],
                               "class": cls, "reason": "名冊查無此機構"})
            if cls == "renamed_unlinked":
                # 接不到現機構，但舊名本身就是換牌線索，仍收進 aliases
                aliases.append({"institution_id": None, "current_name": None,
                                "former_name": next((n for n in names
                                                     if is_institution_name(n)), names[0]),
                                "type": "renamed_unlinked", "penalty_id": p["id"],
                                "penalty_date": p.get("penalty_date"), "via": "annotation"})
            continue
        stats["matched"] += 1
        stats[f"method_{method}"] += 1
        links[p["id"]] = {"institution_id": hit["id"], "method": method}
        # 處分時名稱與命中的現名不同 → 改名／換牌／換受託單位線索
        for name in names:
            # 只差縣市前綴不算改名（如「私立兒晴托嬰中心」→「臺中市私立兒晴托嬰中心」）
            if norm(hit["name"]) in (norm(name), norm((p.get("city") or "") + name)):
                continue
            if strip_paren(name) == strip_paren(hit["name"]):
                # 主體同名、只差括號內容：唯有雙方都是「委託○○辦理」才是換受託單位，
                # 其餘（負責人、已歇業之類的註記）純屬雜訊，不進別名表
                if not all("委託" in s for s in (name, hit["name"])):
                    continue
                kindtag = "operator_change"
            else:
                kindtag = "renamed"
            aliases.append({"institution_id": hit["id"], "current_name": hit["name"],
                            "former_name": name, "type": kindtag,
                            "penalty_id": p["id"], "penalty_date": p.get("penalty_date"),
                            "via": method if name == matched_name else "sibling_name"})
    return {"links": links, "aliases": aliases, "unresolved": unresolved, "stats": stats}


def merge_aliases(rows: list[dict]) -> list[dict]:
    """同一組（機構, 舊名）合併成一筆，保留最早與最晚的裁罰日期與筆數。"""
    groups: dict[tuple, dict] = {}
    for r in rows:
        key = (r["institution_id"], norm(r["former_name"]))
        g = groups.setdefault(key, {
            "institution_id": r["institution_id"], "current_name": r["current_name"],
            "former_name": r["former_name"], "type": r["type"],
            "penalty_count": 0, "first_penalty_date": None, "last_penalty_date": None,
            "penalty_ids": []})
        g["penalty_count"] += 1
        g["penalty_ids"].append(r["penalty_id"])
        d = r.get("penalty_date")
        if d:
            g["first_penalty_date"] = min(g["first_penalty_date"] or d, d)
            g["last_penalty_date"] = max(g["last_penalty_date"] or d, d)
    return sorted(groups.values(),
                  key=lambda g: (g["institution_id"] or "", g["former_name"]))


def report(source: str, stats: Counter) -> float:
    """回傳對照率：分母＝名冊裡本來就該有的那些（命中＋已停辦＋換牌接不到），
    自然人、未立案、非本站範圍、站方分類錯置本就無機構可對，不計入。"""
    denom = stats["matched"] + stats["closed"] + stats["renamed_unlinked"]
    rate = stats["matched"] / denom if denom else 1.0
    methods = "／".join(f"{k[7:]} {v}" for k, v in sorted(stats.items())
                        if k.startswith("method_"))
    log(f"{source}：命中 {stats['matched']}（{methods}）；"
        f"可對照母體 {denom} → 對照率 {rate:.1%}")
    log(f"  未對上分類：已停辦或查無 {stats['closed']}、換牌接不到 "
        f"{stats['renamed_unlinked']}、自然人 {stats['person']}、"
        f"未立案 {stats['unlicensed']}、非本站範圍 {stats['out_of_scope']}、"
        f"站方分類錯置 {stats['misclassified']}")
    return rate


def main(argv: list[str]) -> int:
    def opt(name: str, default=None):
        return argv[argv.index(name) + 1] if name in argv else default

    known = {"--data-dir", "--as-of", "--dry-run"}
    for a in argv:
        if a.startswith("--") and a not in known:
            log(f"未知參數：{a}")
            return 2

    default_data = Path(__file__).resolve().parent.parent.parent / "preschool-resume-data"
    data_dir = Path(opt("--data-dir", default_data))
    as_of = opt("--as-of", date.today().isoformat())
    dry_run = "--dry-run" in argv

    inst_path = data_dir / "data" / "institutions.json"
    if not inst_path.exists():
        log(f"缺少機構主檔，請先執行 site/institutions.py：{inst_path}")
        return 2
    records = json.loads(inst_path.read_text(encoding="utf-8"))["records"]
    index = Index(records)
    log(f"機構主檔 {len(records):,} 家")

    all_links, all_aliases, all_unresolved = {}, [], []
    rates = {}
    for source, kind in (("ece", "preschool"), ("sfaa", "babycare")):
        path = data_dir / "data" / source / "penalties.json"
        if not path.exists():
            log(f"缺少 {path}")
            return 2
        penalties = json.loads(path.read_text(encoding="utf-8"))["records"]
        res = match_source(penalties, kind, index)
        log(f"{source}：裁罰 {len(penalties):,} 筆")
        rates[source] = report(source, res["stats"])
        all_links.update(res["links"])
        all_aliases.extend(res["aliases"])
        all_unresolved.extend(res["unresolved"])

    aliases = merge_aliases(all_aliases)
    renamed = [a for a in aliases if a["type"] == "renamed"]
    log(f"別名 {len(aliases)} 組：改名／換牌 {len(renamed)}、"
        f"換受託單位 {len(aliases) - len(renamed)}")

    # 門檻一：每筆未命中都必須有分類，殘留代表分類器漏接
    if (blank := [u for u in all_unresolved if not u["class"]]):
        log(f"中止：{len(blank)} 筆未對上者沒有分類")
        return 1
    # 門檻二：幼兒園母體夠大，對照率設絕對門檻
    if rates["ece"] < ECE_MIN_MATCH_RATE:
        log(f"中止：ece 對照率 {rates['ece']:.1%} 低於門檻 {ECE_MIN_MATCH_RATE:.0%}")
        return 1
    # 門檻三：連結數大幅縮水，多半是主檔或解析改版
    out_path = data_dir / "data" / "penalty_institution.json"
    if out_path.exists():
        prev = len(json.loads(out_path.read_text(encoding="utf-8"))["links"])
        if len(all_links) < prev * (1 - MAX_SHRINK_RATE):
            log(f"中止：對照數大幅縮水 {prev:,} → {len(all_links):,}")
            return 1

    if dry_run:
        log("--dry-run：不寫檔")
        return 0

    def write(name: str, payload: dict):
        p = data_dir / "data" / name
        p.write_text(json.dumps({"as_of": as_of, **payload}, ensure_ascii=False,
                                indent=1) + "\n", encoding="utf-8")
        log(f"寫入 {p}")

    write("penalty_institution.json", {"links": dict(sorted(all_links.items()))})
    write("aliases.json", {"aliases": aliases})
    write("match_unresolved.json",
          {"unresolved": sorted(all_unresolved, key=lambda u: u["penalty_id"])})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
