"""內部監看告警（docs/07 方向二之 1）：觀察名單負責人另營機構。

觀察名單自動納入「遭廢止設立許可」或「終身」處分機構的負責人。門檻從嚴是
刻意的：這兩類是明確的官方終局處分，母體小、誤報低；金額門檻刻意不用，
名單一大人工查證就跟不上。可在資料 repo 的 data/watchlist.json 人工增刪：

    {"add": [{"name": "王大明", "note": "納入理由"}], "remove": ["李小美"]}

主檔更新後比對：現存（is_active）機構的負責人命中觀察名單、且不是觀察來源
機構本身，即告警。「同負責人」是姓名推導，同名同姓會誤關聯——所以這只是
內部訊號，人工查證後再決定處理，一切不進公開層（公開層由 public_data.py
白名單把關，owner 本來就不輸出）。負責人換人頭開新園抓不到，這是姓名歸戶
方法的天花板（docs/07）。

告警通道：GitHub issue（gh CLI）。內容含姓名，只能開在 private repo——
程式 repo 已於 2026-08-22 轉公開，故預設開在維持私有的資料 repo；
CI 需要 fine-grained PAT（WATCHLIST_ISSUE_TOKEN），github.token 對外部
repo 無權。
已告警的（機構 id × 姓名）記在資料 repo 的 data/watchlist_state.json，
成功開 issue 才記錄——重跑冪等、不重複開單；開單失敗下次重試。

用法：python3 site/watchlist.py [--data-dir PATH] [--repo owner/name] [--dry-run]
      --dry-run  只列出命中，不開 issue、不寫 state
"""

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "crawler"))

from common import log  # noqa: E402
from public_data import REVOKED_RE, name_tokens  # noqa: E402

TERMINAL_KEYWORD = "終身"   # 目前 0 筆（官方查詢只列罰鍰與廢照），上了系統即生效


def is_terminal(sanction: str | None) -> bool:
    s = (sanction or "").replace("\n", "")
    return bool(REVOKED_RE.search(s)) or TERMINAL_KEYWORD in s


def build_watch(institutions: list[dict], penalties: list[dict],
                links: dict, manual: dict) -> tuple[dict, set]:
    """回傳（姓名 → 納入理由）與觀察來源機構 id 集合。"""
    by_id = {r["id"]: r for r in institutions}
    watch: dict[str, str] = {}
    watched_ids: set[str] = set()
    for p in penalties:
        if not is_terminal(p.get("sanction")):
            continue
        link = links.get(p["id"])
        inst = by_id.get(link["institution_id"]) if link else None
        if not inst:
            continue
        watched_ids.add(inst["id"])
        for name in name_tokens(inst.get("owner")):
            watch.setdefault(name, f"{inst['name']}（{p.get('sanction', '').strip()}）")
    for item in manual.get("add", []):
        name = item["name"] if isinstance(item, dict) else item
        note = item.get("note", "人工納入") if isinstance(item, dict) else "人工納入"
        watch.setdefault(name, note)
    for name in manual.get("remove", []):
        watch.pop(name, None)
    return watch, watched_ids


def find_hits(institutions: list[dict], watch: dict, watched_ids: set) -> list[dict]:
    hits = []
    for inst in institutions:
        if not inst.get("is_active", True) or inst["id"] in watched_ids:
            continue
        for name in name_tokens(inst.get("owner")):
            if name in watch:
                hits.append({"institution_id": inst["id"], "name": name,
                             "institution": inst["name"], "city": inst.get("city"),
                             "kind": inst.get("kind"), "reason": watch[name]})
    return hits


def create_issue(repo: str, hit: dict) -> bool:
    title = (f"觀察名單命中：{hit['name']} 名下現存機構"
             f"「{hit['institution']}」")
    body = (f"- 觀察名單姓名：{hit['name']}\n"
            f"- 納入理由：{hit['reason']}\n"
            f"- 命中機構：{hit['institution']}（{hit['city']}，{hit['kind']}）\n"
            f"- 機構 id：`{hit['institution_id']}`\n\n"
            "同名同姓會誤關聯，請人工查證後再決定處理；查證結論請記錄於本 issue。\n"
            "此為內部監看訊號（docs/07 方向二），不進公開層。")
    r = subprocess.run(["gh", "issue", "create", "-R", repo,
                        "--title", title, "--body", body],
                       capture_output=True, text=True)
    if r.returncode != 0:
        log(f"開 issue 失敗：{title}——{r.stderr.strip()}")
        return False
    log(f"已開 issue：{r.stdout.strip()}")
    return True


def main(argv: list[str]) -> int:
    def opt(name: str, default=None):
        return argv[argv.index(name) + 1] if name in argv else default

    known = {"--data-dir", "--repo", "--dry-run"}
    for a in argv:
        if a.startswith("--") and a not in known:
            log(f"未知參數：{a}")
            return 2

    repo_dir = Path(__file__).resolve().parent.parent
    data_dir = Path(opt("--data-dir", repo_dir.parent / "preschool-resume-data"))
    repo = opt("--repo", "shinder/preschool-resume-data")
    dry_run = "--dry-run" in argv

    def load(rel: str, key: str, default=None):
        path = data_dir / "data" / rel
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8")).get(key, default)

    institutions = load("institutions.json", "records")
    links = load("penalty_institution.json", "links")
    if institutions is None or links is None:
        log("缺少機構主檔或對照結果，請先執行 site/institutions.py 與 site/match.py")
        return 2
    penalties = []
    for source in ("ece", "sfaa"):
        penalties.extend(load(f"{source}/penalties.json", "records", []))
    manual_path = data_dir / "data" / "watchlist.json"
    manual = (json.loads(manual_path.read_text(encoding="utf-8"))
              if manual_path.exists() else {})

    watch, watched_ids = build_watch(institutions, penalties, links, manual)
    hits = find_hits(institutions, watch, watched_ids)
    log(f"觀察名單 {len(watch)} 人（來源機構 {len(watched_ids)} 家）、"
        f"命中現存機構 {len(hits)} 筆")

    state_path = data_dir / "data" / "watchlist_state.json"
    state = (json.loads(state_path.read_text(encoding="utf-8"))
             if state_path.exists() else {"alerted": {}})
    new_hits = [h for h in hits
                if f"{h['institution_id']}|{h['name']}" not in state["alerted"]]
    for h in hits:
        marker = "（已告警）" if h not in new_hits else ""
        log(f"  {h['name']} → {h['institution']}{marker}")
    if not new_hits:
        log("無新命中")
        return 0
    if dry_run:
        log(f"--dry-run：{len(new_hits)} 筆新命中，不開 issue、不寫 state")
        return 0

    failed = 0
    today = date.today().isoformat()
    for h in new_hits:
        if create_issue(repo, h):
            state["alerted"][f"{h['institution_id']}|{h['name']}"] = today
        else:
            failed += 1
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if failed:
        log(f"{failed} 筆開 issue 失敗，state 未記錄、下次重試")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
