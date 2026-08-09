"""diff 引擎（第二階段 2.2）：解析現況 vs 上次狀態 → 事件。

流程：parse.py 解析 raw/ 快照 → 與 data/{source}/penalties.json 比對 →
產出 new／changed／removed 事件 append 到 events/events.jsonl，
並回寫更新後的 penalties.json（含 first_seen／last_seen／removed_at 生命週期欄位）。

原則：
- 解析有任何失敗即中止（exit 1），不寫任何檔案——部分解析會誤發大量 removed。
- removed 紀錄永久保留在 penalties.json（官方下架、我方仍有，即護城河）。
- 單次 removed 數量異常（疑似站方改版）時中止，需 --force 才放行。

用法：python3 crawler/diff.py --data-dir PATH [--as-of YYYY-MM-DD] [--dry-run] [--force]
"""

import json
import sys
from datetime import date
from pathlib import Path

from common import log
from parse import parse_ece, parse_sfaa

LIFECYCLE = ("first_seen", "last_seen", "removed_at")

# 單次 removed 超過 max(絕對值, 既有筆數比例) 即視為異常（防站方改版造成的靜默失敗）
REMOVED_GUARD_ABS = 20
REMOVED_GUARD_RATIO = 0.05


def load_state(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return {r["id"]: r for r in json.loads(path.read_text(encoding="utf-8"))["records"]}


def content_fields(record: dict) -> dict:
    return {k: v for k, v in record.items() if k not in LIFECYCLE}


def diff_source(old: dict[str, dict], current: list[dict], as_of: str
                ) -> tuple[list[dict], list[dict]]:
    """回傳 (更新後 records, 事件 list)。old 就地不動。"""
    events = []
    merged: dict[str, dict] = {rid: dict(r) for rid, r in old.items()}

    for rec in current:
        rid = rec["id"]
        prev = merged.get(rid)
        if prev is None:
            merged[rid] = {**rec, "first_seen": as_of, "last_seen": as_of, "removed_at": None}
            events.append({"type": "new", "penalty_id": rid, "date": as_of})
            continue
        changes = {
            k: [prev.get(k), v] for k, v in content_fields(rec).items()
            if prev.get(k) != v
        }
        reappeared = prev.get("removed_at") is not None
        # 回補紀錄（origin=kiang）first_seen 為 None，重新上架時以當日補上
        merged[rid] = {**rec, "first_seen": prev.get("first_seen") or as_of,
                       "last_seen": as_of, "removed_at": None}
        if reappeared:
            events.append({"type": "new", "penalty_id": rid, "date": as_of,
                           "note": "reappeared"})
        elif changes:
            events.append({"type": "changed", "penalty_id": rid, "date": as_of,
                           "diff": changes})

    current_ids = {r["id"] for r in current}
    for rid, prev in merged.items():
        if rid not in current_ids and prev.get("removed_at") is None:
            prev["removed_at"] = as_of
            events.append({"type": "removed", "penalty_id": rid, "date": as_of})

    return sorted(merged.values(), key=lambda r: r["id"]), events


def main(argv: list[str]) -> int:
    data_dir = Path(argv[argv.index("--data-dir") + 1]) if "--data-dir" in argv \
        else Path(__file__).resolve().parent.parent.parent / "preschool-resume-data"
    as_of = argv[argv.index("--as-of") + 1] if "--as-of" in argv \
        else date.today().isoformat()
    dry_run = "--dry-run" in argv
    force = "--force" in argv

    failures: list[str] = []
    parsed = {"sfaa": parse_sfaa(data_dir, failures),
              "ece": parse_ece(data_dir, failures)}
    if failures:
        log(f"解析失敗 {len(failures)} 筆，中止（不產生事件）：")
        for msg in failures:
            log(f"  - {msg}")
        return 1

    all_events = []
    outputs = {}
    for source, current in parsed.items():
        state_path = data_dir / "data" / source / "penalties.json"
        old = load_state(state_path)
        records, events = diff_source(old, current, as_of)

        removed = sum(1 for e in events if e["type"] == "removed")
        guard = max(REMOVED_GUARD_ABS, int(len(old) * REMOVED_GUARD_RATIO))
        if old and removed > guard and not force:
            log(f"{source}：單次 removed {removed} 筆超過警戒值 {guard}，"
                f"疑似站方改版或抓取異常，中止。確認無誤後加 --force 放行。")
            return 1

        outputs[state_path] = records
        for e in events:
            e["source"] = source
        all_events.extend(events)
        counts = {t: sum(1 for e in events if e["type"] == t)
                  for t in ("new", "changed", "removed")}
        log(f"{source}：現況 {len(current)} 筆 → new {counts['new']}、"
            f"changed {counts['changed']}、removed {counts['removed']}")

    if dry_run:
        log("--dry-run：不寫檔")
        return 0

    for state_path, records in outputs.items():
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps({"as_of": as_of, "records": records},
                       ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8")
    if all_events:
        events_path = data_dir / "events" / "events.jsonl"
        events_path.parent.mkdir(parents=True, exist_ok=True)
        with events_path.open("a", encoding="utf-8") as fh:
            for e in all_events:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    log(f"完成：事件共 {len(all_events)} 筆")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
