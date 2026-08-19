"""內部監看告警驗收測試（docs/07 方向二之 1）：
廢照機構負責人進觀察名單、名下現存機構要告警；來源機構本身與已停辦者不算；
已告警者不重複開單；人工增刪生效；開單失敗不寫 state（下次重試）。

零依賴，直接執行：python3 tests/test_watchlist.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site"))

import watchlist  # noqa: E402

INSTITUTIONS = [
    # 廢照園（觀察來源）：負責人王大明
    {"id": "preschool:a", "kind": "preschool", "name": "臺北市私立甲幼兒園",
     "city": "臺北市", "is_active": False, "owner": "王大明"},
    # 同負責人另營的現存機構 → 要命中
    {"id": "babycare:CW1", "kind": "babycare", "name": "新北市私立乙托嬰中心",
     "city": "新北市", "is_active": True, "owner": "王大明"},
    # 同負責人但已停辦 → 不告警
    {"id": "preschool:c", "kind": "preschool", "name": "臺北市私立丙幼兒園",
     "city": "臺北市", "is_active": False, "owner": "王大明"},
    # 無關機構
    {"id": "preschool:d", "kind": "preschool", "name": "臺北市私立丁幼兒園",
     "city": "臺北市", "is_active": True, "owner": "李小美"},
]

PENALTIES = [
    {"id": "ece:1", "source": "ece", "sanction": "廢止設立許可：2026/08/01",
     "penalty_date": "2026-07-01"},
    {"id": "ece:2", "source": "ece", "sanction": "罰鍰：600,000 元",  # 高額不進名單
     "penalty_date": "2026-07-20"},
]

LINKS = {"ece:1": {"institution_id": "preschool:a"},
         "ece:2": {"institution_id": "preschool:d"}}


def setup(d: Path, manual=None):
    (d / "data/ece").mkdir(parents=True)
    (d / "data/sfaa").mkdir(parents=True)
    (d / "data/institutions.json").write_text(
        json.dumps({"records": INSTITUTIONS}, ensure_ascii=False))
    (d / "data/penalty_institution.json").write_text(
        json.dumps({"links": LINKS}, ensure_ascii=False))
    (d / "data/ece/penalties.json").write_text(
        json.dumps({"records": PENALTIES}, ensure_ascii=False))
    (d / "data/sfaa/penalties.json").write_text(json.dumps({"records": []}))
    if manual is not None:
        (d / "data/watchlist.json").write_text(
            json.dumps(manual, ensure_ascii=False))


def main():
    created = []

    def fake_issue(repo, hit):
        created.append(hit)
        return True

    original = watchlist.create_issue
    watchlist.create_issue = fake_issue
    try:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            setup(d)
            # 首輪：廢照園負責人名下的現存機構命中並開單；來源機構本身、
            # 已停辦者、僅高額罰鍰（不進名單）者都不告警
            assert watchlist.main(["--data-dir", str(d)]) == 0
            assert [h["institution_id"] for h in created] == ["babycare:CW1"]
            assert created[0]["name"] == "王大明"
            state = json.loads((d / "data/watchlist_state.json").read_text())
            assert "babycare:CW1|王大明" in state["alerted"]
            # 重跑冪等：不重複開單
            created.clear()
            assert watchlist.main(["--data-dir", str(d)]) == 0
            assert created == []
            # --dry-run 不寫 state 也不開單
            (d / "data/watchlist_state.json").unlink()
            assert watchlist.main(["--data-dir", str(d), "--dry-run"]) == 0
            assert created == [] and not (d / "data/watchlist_state.json").exists()

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # 人工增刪：加李小美 → 丁幼兒園命中；移除王大明 → 乙托嬰不再告警
            setup(d, manual={"add": [{"name": "李小美", "note": "測試納入"}],
                             "remove": ["王大明"]})
            created.clear()
            assert watchlist.main(["--data-dir", str(d)]) == 0
            assert [h["institution_id"] for h in created] == ["preschool:d"]
            assert created[0]["reason"] == "測試納入"

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            setup(d)
            # 開單失敗：exit 1 且不寫入 state（下次重試）
            watchlist.create_issue = lambda repo, hit: False
            assert watchlist.main(["--data-dir", str(d)]) == 1
            state = json.loads((d / "data/watchlist_state.json").read_text())
            assert state["alerted"] == {}
    finally:
        watchlist.create_issue = original

    print("test_watchlist：全部通過")


if __name__ == "__main__":
    main()
