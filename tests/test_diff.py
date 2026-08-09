"""diff 引擎驗收測試（工程執行計劃 2.2 完成定義）：
人工改一筆測試資料，能觸發 new／changed／removed 三類事件。

零依賴，直接執行：python3 tests/test_diff.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "crawler"))

import diff  # noqa: E402
from common import safe_filename  # noqa: E402


def sfaa_detail(date: str, person: str, fact: str) -> str:
    return f"""<html><body>
    <div class="tr" role="row">
        <div role="columnheader">裁罰日期</div>
        <div role="cell">{date}</div>
    </div>
    <div class="tr" role="row">
        <div role="columnheader">縣市名稱</div>
        <div role="cell">臺北市</div>
    </div>
    <div class="tr" role="row">
        <div role="columnheader">裁罰對象</div>
        <div role="cell">
            <span class="col"><span class="detailTitle">行為人</span><br />{person}</span>
        </div>
    </div>
    <div class="tr" role="row">
        <div role="columnheader">事實摘要</div>
        <div role="cell"><span class="textarea">{fact}</span></div>
    </div>
    </body></html>"""


def sfaa_list(ids: list[int]) -> str:
    links = "".join(f'<a href="/ChildYoungLaw/Detail/{i}">詳情</a>' for i in ids)
    return f"<html><body>{links}</body></html>"


def ece_detail(rows: list[dict]) -> str:
    cells = ""
    for n, r in enumerate(rows):
        cells += "".join(
            f'<span id="GridView1_{k}_{n}">{v}</span>'
            for k, v in r.items())
    return f"<html><body><table>{cells}</table></body></html>"


def ece_list(schools: list[tuple[str, str, str]]) -> str:
    spans = ""
    for n, (city, area, name) in enumerate(schools):
        spans += (f'<span id="GridView1_lblCity_{n}">{city}</span>'
                  f'<span id="GridView1_lblArea_{n}">{area}</span>'
                  f'<span id="GridView1_lblSchName_{n}">{name}</span>'
                  f'<span id="GridView1_lblBStatus_{n}">正常</span>')
    return f"<html><body>{spans}</body></html>"


ECE_ROW = {"lblPDate": "2024/01/08", "lblPShName": "測試幼兒園",
           "lblPGNum": "測府字第1號", "lblPName": "幼兒教育及照顧法",
           "lblPDetail": "第10條", "lblPObject": "負責人：王大明",
           "lblPContent": "罰鍰：6,000 元"}


def write_snapshots(d: Path, sfaa_ids: list[int], sfaa_facts: dict[int, str],
                    ece_schools: list[tuple[str, str, str]], ece_rows: list[dict]):
    (d / "raw/sfaa/list").mkdir(parents=True, exist_ok=True)
    (d / "raw/sfaa/detail").mkdir(parents=True, exist_ok=True)
    (d / "raw/ece/list").mkdir(parents=True, exist_ok=True)
    (d / "raw/ece/detail").mkdir(parents=True, exist_ok=True)
    (d / "raw/sfaa/list/p0001.html").write_text(sfaa_list(sfaa_ids), encoding="utf-8")
    for i in sfaa_ids:
        (d / f"raw/sfaa/detail/{i}.html").write_text(
            sfaa_detail("2020.01.01", f"測試人{i}", sfaa_facts.get(i, "事實")),
            encoding="utf-8")
    (d / "raw/ece/list/p0001.html").write_text(ece_list(ece_schools), encoding="utf-8")
    for city, _area, name in ece_schools:
        (d / f"raw/ece/detail/{safe_filename(f'{city}_{name}')}.html").write_text(
            ece_detail(ece_rows), encoding="utf-8")


def run(d: Path, as_of: str) -> int:
    return diff.main(["--data-dir", str(d), "--as-of", as_of])


def main():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        school = ("臺北市", "中正區", "測試幼兒園")

        # 第一天：sfaa 兩筆＋ece 一園一列，全部應為 new
        write_snapshots(d, [1, 2], {}, [school], [ECE_ROW])
        assert run(d, "2026-08-09") == 0
        events = [json.loads(ln) for ln in
                  (d / "events/events.jsonl").read_text().splitlines()]
        assert [e["type"] for e in events] == ["new"] * 3, events

        # 第二天人工改資料：
        # - sfaa:1 改事實摘要 → changed
        # - sfaa:2 從列表消失 → removed
        # - sfaa:3 新增 → new
        write_snapshots(d, [1, 3], {1: "事實已更正"}, [school], [ECE_ROW])
        assert run(d, "2026-08-10") == 0
        events = [json.loads(ln) for ln in
                  (d / "events/events.jsonl").read_text().splitlines()][3:]
        by_type = {e["type"]: e for e in events}
        assert set(by_type) == {"new", "changed", "removed"}, events
        assert by_type["new"]["penalty_id"] == "sfaa:3"
        assert by_type["changed"]["penalty_id"] == "sfaa:1"
        assert by_type["changed"]["diff"]["fact"] == ["事實", "事實已更正"]
        assert by_type["removed"]["penalty_id"] == "sfaa:2"

        # 狀態檔：removed 紀錄仍保留（護城河），生命週期欄位正確
        state = {r["id"]: r for r in json.loads(
            (d / "data/sfaa/penalties.json").read_text())["records"]}
        assert state["sfaa:2"]["removed_at"] == "2026-08-10"
        assert state["sfaa:2"]["last_seen"] == "2026-08-09"
        assert state["sfaa:1"]["removed_at"] is None
        assert state["sfaa:1"]["first_seen"] == "2026-08-09"
        assert state["sfaa:1"]["last_seen"] == "2026-08-10"

        # 第三天：sfaa:2 重新上架 → new（note: reappeared）
        write_snapshots(d, [1, 2, 3], {1: "事實已更正"}, [school], [ECE_ROW])
        assert run(d, "2026-08-11") == 0
        events = [json.loads(ln) for ln in
                  (d / "events/events.jsonl").read_text().splitlines()][6:]
        assert len(events) == 1 and events[0]["type"] == "new" \
            and events[0]["penalty_id"] == "sfaa:2" \
            and events[0].get("note") == "reappeared", events

        # 無異動日：不新增事件
        assert run(d, "2026-08-12") == 0
        n_events = len((d / "events/events.jsonl").read_text().splitlines())
        assert n_events == 7, n_events

    print("test_diff：全部通過")


if __name__ == "__main__":
    main()
