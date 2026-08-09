"""歷史回補的同案變體去重測試（工程執行計劃 2.4 修補）。

零依賴，直接執行：python3 tests/test_backfill.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "crawler"))

import backfill_kiang as bk  # noqa: E402


def rec(rid, doc_no, date, violation, sanction, origin="kiang"):
    return {"id": rid, "doc_no": doc_no, "penalty_date": date,
            "violation": violation, "sanction": sanction, "origin": origin}


def main():
    records = [
        # 同一份公文、同日、同額：舊版只有條項，新版接了說明 → 舊版是變體
        rec("a", "新北府教幼字第1121097605號", "2023-06-12", "第16條第4項", "罰鍰：60,000元"),
        rec("b", "新北府教幼字第1121097605號", "2023-06-12",
            "第16條第4項幼兒園班級師生比違反規定。", "罰鍰：60,000元"),
        # 同一份公文的另一項處分：條項不同、金額不同，是真的另一件事
        rec("c", "新北府教幼字第1121097605號", "2023-06-12", "第16條第3項", "罰鍰：6,000元"),
        # 條項相同但金額不同 → 不視為變體（處分內容有進去比對鍵）
        rec("d", "北市教字第2號", "2024-01-01", "第8條", "罰鍰：6,000元"),
        rec("e", "北市教字第2號", "2024-01-01", "第8條招收人數限制", "罰鍰：15,000元"),
        # 不同公文，前綴關係也不該誤殺
        rec("f", "北市教字第3號", "2024-02-02", "第8條", "罰鍰：6,000元"),
    ]
    drop = bk.variant_duplicates(records)
    assert drop == {"a"}, drop

    # 三方變體：只留最完整的一筆
    triple = [
        rec("x1", "府教字第9號", "2022-01-01", "第10條", "罰鍰：3,000元"),
        rec("x2", "府教字第9號", "2022-01-01", "第10條-環境", "罰鍰：3,000元"),
        rec("x3", "府教字第9號", "2022-01-01", "第10條-環境衛生不符規定。", "罰鍰：3,000元"),
    ]
    assert bk.variant_duplicates(triple) == {"x1", "x2"}, bk.variant_duplicates(triple)

    # 缺文號或缺日期者不參與比對，避免把不同案件併在一起
    loose = [
        rec("n1", None, "2022-01-01", "第10條", "罰鍰：3,000元"),
        rec("n2", None, "2022-01-01", "第10條說明", "罰鍰：3,000元"),
    ]
    assert bk.variant_duplicates(loose) == set()

    print("test_backfill：全部通過")


if __name__ == "__main__":
    main()
