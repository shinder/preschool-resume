"""機構主檔驗收測試（工程執行計劃 6.1 完成定義）：
欄位正規化正確，且座標／地址缺漏、id 重複、機構數縮水都會讓排程失敗。

零依賴，直接執行：python3 tests/test_institutions.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site"))

import institutions  # noqa: E402


def preschool(n: int, *, coords=(121.5, 25.0), address="[100]測試路1號",
              owner="王大明") -> dict:
    return {
        "type": "Feature",
        "properties": {
            "id": f"uuid-{n}", "title": f"臺北市私立測試{n}幼兒園", "owner": owner,
            "city": "臺北市", "town": "中正區", "type": "私立", "address": address,
            "floor": "1樓", "size": "300平方公尺", "size_in": "200平方公尺",
            "size_out": "100平方公尺", "tel": "(02)12345678", "url": "",
            "count_approved": "120", "shuttle": "", "pre_public": "113-115",
            "is_free5": "", "is_after": "有，人數：10", "penalty": "無",
            "has_slip": "no", "monthly": 3000, "is_active": 1,
            "reg_no": "北市教字第1號", "reg_docno": "第2號", "reg_date": "1996/08/17",
        },
        "geometry": {"type": "Point", "coordinates": list(coords)},
    }


def babycare(n: int, *, address="231新北市新店區中央里中央一街20號1樓") -> dict:
    return {
        "type": "Feature",
        "properties": {
            "id": f"CW{n:08d}", "name": f"新北市私立測試{n}托嬰中心",
            "city": "新北市新店區", "address": address, "phone": "02-82187139",
            "capacity": 30, "status": 25, "is_active": True,
        },
        "geometry": {"type": "Point", "coordinates": [121.528323, 24.973066]},
    }


def ncwis(cw: str, name: str, **extra) -> dict:
    return {"id": cw, "name": name, "address": "231新北市新店區中央一街20號1樓",
            "phone": "02-11112222", "capacity_approved": 40, "enrolled": 33,
            "owner": "陳美麗", "grade": "甲等", "grade_year": "114",
            "pre_public": "是", "area_total": "300 平方公尺",
            "area_indoor": "200 平方公尺", "area_outdoor": "100 平方公尺",
            "safety_public": "核備文號:112-K5000480-01", "safety_fire": None,
            "staff": {"主管人員": 1, "托育人員": 6}, "fee_mode": "日間托育",
            "fees": [{"name": "托育費", "amount": "18000", "note": "月費"}],
            "refund_note": "依定型化契約辦理。", "note_name": None,
            "violations": [], **extra}


def write_ncwis(d: Path, records: list[dict]):
    p = d / "data" / "ncwis"
    p.mkdir(parents=True, exist_ok=True)
    (p / "centers.json").write_text(
        json.dumps({"as_of": "2026-08-09", "records": records}, ensure_ascii=False),
        encoding="utf-8")


def write_sources(d: Path, pres: list[dict], babies: list[dict]):
    raw = d / "raw" / "kiang"
    raw.mkdir(parents=True, exist_ok=True)
    (d / "data").mkdir(parents=True, exist_ok=True)
    for name, feats in (("preschools.json", pres), ("babycare.json", babies)):
        (raw / name).write_text(
            json.dumps({"type": "FeatureCollection", "features": feats},
                       ensure_ascii=False), encoding="utf-8")


def run(d: Path, as_of: str = "2026-08-09") -> int:
    return institutions.main(["--data-dir", str(d), "--no-fetch", "--as-of", as_of])


def load(d: Path) -> dict[str, dict]:
    return {r["id"]: r for r in
            json.loads((d / "data/institutions.json").read_text())["records"]}


def main():
    # 一、正常路徑：欄位正規化
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        # 上游有 4 筆把經緯度存成字串，須正規化為 float
        p = preschool(1)
        p["geometry"]["coordinates"] = ["121.526304", "25.031977"]
        write_sources(d, [p], [babycare(1)])
        assert run(d) == 0
        recs = load(d)

        s = recs["preschool:uuid-1"]
        assert (s["lng"], s["lat"]) == (121.526304, 25.031977), s
        assert s["source"] == "ece" and s["native_id"] == "uuid-1"
        assert s["postal_code"] == "100"                      # [100] 剝離另存
        assert s["address"] == "臺北市中正區測試路1號"          # 縣市＋區＋街道組合
        assert s["capacity_approved"] == 120                  # 字串轉 int
        assert s["url"] is None and s["shuttle"] is None      # 空字串轉 None
        assert s["owner"] == "王大明"                          # 個資留在私有主檔供歸戶
        assert s["is_active"] is True

        b = recs["babycare:CW00000001"]
        assert b["source"] == "sfaa" and b["native_id"] == "CW00000001"
        assert (b["city"], b["town"], b["postal_code"]) == ("新北市", "新店區", "231")
        assert b["address"].startswith("新北市新店區"), b["address"]  # 郵遞區號不重複帶
        assert (b["capacity_approved"], b["enrolled"]) == (30, 25)  # 核定／實際收托
        assert not (d / "data/institutions_issues.json").exists()

    # 二、地址解不出縣市區時退回 city 欄位，並記為問題
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        write_sources(d, [preschool(i) for i in range(200)],
                      [babycare(1, address="中央一街20號1樓")])
        assert run(d) == 0                                    # 0.5% 未超過門檻
        b = load(d)["babycare:CW00000001"]
        assert (b["city"], b["town"]) == ("新北市", "新店區"), b
        issues = json.loads((d / "data/institutions_issues.json").read_text())["issues"]
        assert len(issues) == 1 and "地址無法解析" in issues[0]["issue"], issues

    # 二之二、ncwis 主檔覆蓋 kiang 底稿，並補進 kiang 名冊沒有的中心
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        write_sources(d, [preschool(1)], [babycare(1)])
        write_ncwis(d, [ncwis("CW00000001", "新北市私立測試1托嬰中心"),
                        ncwis("CW00000009", "新北市私立只在站方的托嬰中心")])
        assert run(d) == 0
        recs = load(d)

        merged = recs["babycare:CW00000001"]
        assert merged["origin"] == "ncwis" and merged["geo_origin"] == "kiang"
        assert (merged["capacity_approved"], merged["enrolled"]) == (40, 33)  # 站方覆蓋
        assert merged["lng"] is not None                    # 座標仍來自 kiang
        assert merged["grade"] == "甲等" and merged["pre_public"] == "是"
        assert merged["fees"][0]["amount"] == "18000"
        assert merged["staff"]["托育人員"] == 6
        assert merged["owner"] == "陳美麗"                   # 私有主檔留著供歸戶

        # 站方有、kiang 沒有的中心照收，只是沒有座標
        extra = recs["babycare:CW00000009"]
        assert extra["lng"] is None and extra["city"] == "新北市"
        # 這類缺口不計入品質門檻，否則約 300 家會直接讓建置失敗
        issues = json.loads((d / "data/institutions_issues.json").read_text())["issues"]
        assert [i["gating"] for i in issues] == [False], issues

    # 三、問題率超過門檻即中止（座標落在台灣範圍外）
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        write_sources(d, [preschool(1), preschool(2, coords=(0.0, 0.0))], [babycare(1)])
        assert run(d) == 1
        assert not (d / "data/institutions.json").exists()    # 不寫出半殘主檔

    # 四、id 重複即中止（上游主鍵語意改變，對照會錯接）
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        write_sources(d, [preschool(1), preschool(1)], [babycare(1)])
        assert run(d) == 1
        assert not (d / "data/institutions.json").exists()

    # 五、機構數大幅縮水即中止，且不覆寫既有主檔
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        write_sources(d, [preschool(i) for i in range(100)], [babycare(1)])
        assert run(d, "2026-08-09") == 0
        write_sources(d, [preschool(i) for i in range(80)], [babycare(1)])
        assert run(d, "2026-08-10") == 1
        kept = json.loads((d / "data/institutions.json").read_text())
        assert kept["as_of"] == "2026-08-09" and len(kept["records"]) == 101

    # 六、--no-fetch 但無快照：明確失敗，不產生空主檔
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "data").mkdir(parents=True)
        assert run(d) == 1
        assert not (d / "data/institutions.json").exists()

    print("test_institutions：全部通過")


if __name__ == "__main__":
    main()
