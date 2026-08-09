"""公開層投影驗收測試（工程執行計劃 6.3 完成定義）：
產物掃不到任何自然人姓名，且白名單外的欄位不會外流。

零依賴，直接執行：python3 tests/test_public_data.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site"))

import public_data  # noqa: E402

INSTITUTIONS = [
    {"id": "preschool:a", "kind": "preschool", "source": "ece", "native_id": "a",
     "name": "臺北市私立甲幼兒園", "city": "臺北市", "town": "中正區",
     "address": "臺北市中正區測試路1號", "lng": 121.5, "lat": 25.0,
     "capacity_approved": 120, "is_active": True, "owner": "王大明"},
    {"id": "babycare:CW1", "kind": "babycare", "source": "sfaa", "native_id": "CW1",
     "name": "新北市私立乙托嬰中心", "city": "新北市", "town": "板橋區",
     "address": "新北市板橋區測試路2號", "lng": 121.4, "lat": 25.0,
     "capacity_approved": 30, "enrolled": 25, "is_active": True, "owner": "李小美",
     "grade": "甲等", "grade_year": "114", "staff": {"托育人員": 6},
     "fees": [{"name": "托育費", "amount": "18000", "note": "洽負責人李小美"}]},
]

PENALTIES = {
    "ece": [
        # 一般案件：負責人姓名在 person 欄位，公開層不得出現
        {"id": "ece:1", "source": "ece", "city": "臺北市", "institution": "臺北市私立甲幼兒園",
         "school": "臺北市私立甲幼兒園", "person": "負責人：王大明",
         "doc_no": "北市教幼字第1號", "law": "幼兒教育及照顧法第52條",
         "violation": "第26條第2項", "sanction": "罰鍰：6,000元",
         "penalty_date": "2024-01-01", "removed_at": None, "origin": None},
        # 文號欄位被上游／解析夾帶姓名（實測有此情形），全欄位遮罩要蓋掉
        {"id": "ece:2", "source": "ece", "city": "臺北市", "institution": "臺北市私立舊名幼兒園",
         "school": "臺北市私立甲幼兒園", "person": "負責人：陳美麗",
         "doc_no": "北市教幼字第負責人陳美麗號", "law": "幼兒教育及照顧法第52條",
         "violation": "第8條", "sanction": "罰鍰：6,000元",
         "penalty_date": "2023-05-05", "removed_at": "2026-08-01", "origin": None},
        # 2.4 回補的歷史紀錄：removed_at="unknown"，要與實際觀測到的下架分開計數
        {"id": "ece:3", "source": "ece", "city": "臺北市", "institution": "臺北市私立甲幼兒園",
         "school": "臺北市私立甲幼兒園", "person": "負責人：王大明",
         "doc_no": "北市教幼字第3號", "law": "幼兒教育及照顧法第52條",
         "violation": "第10條", "sanction": "罰鍰：6,000元",
         "penalty_date": "2019-03-03", "removed_at": "unknown", "origin": "kiang"},
    ],
    "sfaa": [
        # 機構名帶括號負責人＋事實摘要直接寫姓名（兩者實測都存在）
        {"id": "sfaa:1", "source": "sfaa", "city": "新北市",
         "institution": "新北市私立乙托嬰中心(張三豐)", "person": None,
         "targets": [{"category": "兒童及少年福利機構",
                      "name": "新北市私立乙托嬰中心(張三豐)"}],
         "fact": "張三豐於托嬰中心不當對待兒童", "law": "兒少法第49條",
         "sanction": "罰鍰：60,000元", "files": ["/UploadFile/x.pdf"],
         "penalty_date": "2024-06-06", "removed_at": None, "origin": None},
        # 自然人裁罰：沒有機構可對，整筆不得進公開層
        {"id": "sfaa:2", "source": "sfaa", "city": "新北市", "institution": None,
         "person": "行為人：趙錢孫",
         "targets": [{"category": "行為人", "name": "趙錢孫"}],
         "fact": "趙錢孫對兒少為不當行為", "law": "兒少法第49條",
         "sanction": "罰鍰：60,000元", "files": [],
         "penalty_date": "2024-07-07", "removed_at": None, "origin": None},
    ],
}

LINKS = {
    "ece:1": {"institution_id": "preschool:a", "method": "exact"},
    "ece:2": {"institution_id": "preschool:a", "method": "exact"},
    "ece:3": {"institution_id": "preschool:a", "method": "exact"},
    "sfaa:1": {"institution_id": "babycare:CW1", "method": "paren_strip"},
}

ALIASES = [
    {"institution_id": "preschool:a", "current_name": "臺北市私立甲幼兒園",
     "former_name": "臺北市私立舊名幼兒園", "type": "renamed", "penalty_count": 1,
     "first_penalty_date": "2023-05-05", "last_penalty_date": "2023-05-05",
     "penalty_ids": ["ece:2"]},
    {"institution_id": None, "current_name": None,
     "former_name": "王美玲(新北市私立丙托嬰中心，現已變更負責人及中心名稱)",
     "type": "renamed_unlinked", "penalty_count": 1,
     "first_penalty_date": "2014-02-18", "last_penalty_date": "2014-02-18",
     "penalty_ids": ["sfaa:9"]},
]

SECRETS = ("王大明", "李小美", "陳美麗", "張三豐", "趙錢孫", "王美玲")


def setup(d: Path):
    (d / "data/ece").mkdir(parents=True)
    (d / "data/sfaa").mkdir(parents=True)
    (d / "data/institutions.json").write_text(
        json.dumps({"as_of": "2026-08-09", "records": INSTITUTIONS}, ensure_ascii=False))
    (d / "data/penalty_institution.json").write_text(
        json.dumps({"as_of": "2026-08-09", "links": LINKS}, ensure_ascii=False))
    (d / "data/aliases.json").write_text(
        json.dumps({"as_of": "2026-08-09", "aliases": ALIASES}, ensure_ascii=False))
    for src, rs in PENALTIES.items():
        (d / f"data/{src}/penalties.json").write_text(
            json.dumps({"as_of": "2026-08-09", "records": rs}, ensure_ascii=False))


def run(d: Path, out: Path) -> int:
    return public_data.main(["--data-dir", str(d), "--out", str(out),
                             "--as-of", "2026-08-09"])


def main():
    with tempfile.TemporaryDirectory() as tmp:
        d, out = Path(tmp) / "data-repo", Path(tmp) / "out"
        d.mkdir()
        setup(d)
        assert run(d, out) == 0

        blob = "".join((out / f).read_text() for f in
                       ("institutions.json", "penalties.json", "aliases.json", "meta.json"))
        # 完成定義：產物掃不到任何自然人姓名
        for name in SECRETS:
            assert name not in blob, f"公開層外洩姓名：{name}"

        insts = {r["id"]: r for r in json.loads(
            (out / "institutions.json").read_text())["records"]}
        pens = {r["id"]: r for r in json.loads(
            (out / "penalties.json").read_text())["records"]}

        # 白名單：私有欄位不得出現
        assert "owner" not in insts["preschool:a"]
        # ncwis 帶來的欄位要進公開層，但巢狀結構裡的姓名同樣要遮
        baby = insts["babycare:CW1"]
        assert baby["grade"] == "甲等" and baby["staff"]["托育人員"] == 6
        assert baby["fees"][0]["note"] == "洽負責人李○○", baby["fees"]
        # 缺額＝核定收托−實際收托，只有托嬰中心兩個數字都有
        assert baby["vacancy"] == 5, baby
        assert insts["preschool:a"]["vacancy"] is None
        assert not ({"person", "targets", "files", "school"} & set(pens["ece:1"])), pens["ece:1"]

        # 自然人裁罰整筆不進公開層
        assert "sfaa:2" not in pens and len(pens) == 4, sorted(pens)

        # 自由文字與夾帶姓名的欄位都要遮罩
        assert pens["sfaa:1"]["fact"] == "張○○於托嬰中心不當對待兒童", pens["sfaa:1"]
        assert pens["ece:2"]["doc_no"] == "北市教幼字第負責人陳○○號", pens["ece:2"]
        assert pens["sfaa:1"]["name_at_penalty"] == "新北市私立乙托嬰中心(張○○)"

        # 處分時名稱：與現名相同就不輸出，不同才留（機構名屬法人資料）
        assert pens["ece:1"]["name_at_penalty"] is None
        assert pens["ece:2"]["name_at_penalty"] == "臺北市私立舊名幼兒園"

        # 我方觀測到的下架與 2.4 回補的歷史存檔要分開計數
        a = insts["preschool:a"]
        assert (a["penalty_count"], a["removed_penalty_count"],
                a["archived_penalty_count"]) == (3, 1, 1), a
        # 揭露分層：as_of 2026-08-09 → 近五年自 2021-08-09 起，ece:3（2019）不算
        assert a["recent_penalty_count"] == 2, a
        meta = json.loads((out / "meta.json").read_text())
        assert meta["recent_since"] == "2021-08-09" and meta["recent_years"] == 5
        assert a["last_penalty_date"] == "2024-01-01"
        assert insts["babycare:CW1"]["penalty_count"] == 1

        # 未經確認的換牌線索（舊名裡混著人名）不公開
        aliases = json.loads((out / "aliases.json").read_text())["aliases"]
        assert [x["type"] for x in aliases] == ["renamed"], aliases
        assert "penalty_ids" not in aliases[0]          # 白名單外欄位

    # 白名單被放寬而放進「非本筆關係人」的姓名時（遮罩涵蓋不到），閘門要擋下來。
    # 本筆自己的關係人姓名會先被全欄位遮罩蓋掉，所以這裡用別筆的人名模擬真實漏法。
    with tempfile.TemporaryDirectory() as tmp:
        d, out = Path(tmp) / "data-repo", Path(tmp) / "out"
        d.mkdir()
        setup(d)
        leaky = json.loads((d / "data/institutions.json").read_text())
        leaky["records"][0]["internal_note"] = "承辦人趙錢孫"
        (d / "data/institutions.json").write_text(
            json.dumps(leaky, ensure_ascii=False))
        original = public_data.INSTITUTION_FIELDS
        public_data.INSTITUTION_FIELDS = original + ("internal_note",)
        try:
            assert run(d, out) == 1
            assert not out.exists(), "閘門攔下時不該寫出任何檔案"
        finally:
            public_data.INSTITUTION_FIELDS = original

    print("test_public_data：全部通過")


if __name__ == "__main__":
    main()
