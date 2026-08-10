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


def preschool(n: int, *, coords=(121.5, 25.0), address="臺北市中正區測試路1號",
              owner="王大明", name=None, reg_no=None, town="中正區") -> dict:
    """`crawler/ece_schools.py` 的 schools.json 紀錄格式。"""
    return {
        "name": name or f"臺北市私立測試{n}幼兒園",
        "city": "臺北市", "town": town, "org_type": "私立",
        "phone": "(02)12345678", "reg_no": reg_no or f"北市教字第{n}號",
        "reg_date": "1996/08/17", "reg_id": "北市教前字第1120000001號",
        "owner": owner, "capacity_approved": 120, "pre_public": "113-115",
        "after_school": "有，人數：10", "penalty_note": "無",
        "area_total": "300 平方公尺", "area_indoor": "200 平方公尺",
        "area_outdoor": "100 平方公尺", "floor": "1樓", "shuttle": None,
        "url": None, "evaluations": [{"year": "110", "date": "2022/07/31",
                                      "result": "全數指標通過基礎評鑑"}],
        "postal_code": "100", "address": address,
        "lat": coords[1], "lng": coords[0],
    }


def pid(rec: dict) -> str:
    """測試用：某筆 schools.json 紀錄應得的機構 id（未撞號時即基本雜湊）。"""
    return "preschool:" + institutions.preschool_base_key(rec)


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
    (d / "data" / "ece").mkdir(parents=True, exist_ok=True)
    (raw / "babycare.json").write_text(
        json.dumps({"type": "FeatureCollection", "features": babies},
                   ensure_ascii=False), encoding="utf-8")
    (d / "data" / "ece" / "schools.json").write_text(
        json.dumps({"as_of": "2026-08-10", "records": pres}, ensure_ascii=False),
        encoding="utf-8")


def run(d: Path, as_of: str = "2026-08-09") -> int:
    return institutions.main(["--data-dir", str(d), "--no-fetch", "--as-of", as_of])


def load(d: Path) -> dict[str, dict]:
    return {r["id"]: r for r in
            json.loads((d / "data/institutions.json").read_text())["records"]}


def main():
    # 一、正常路徑：欄位正規化
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        # 座標偶有以字串存放，須正規化為 float
        p = preschool(1)
        p["lng"], p["lat"] = "121.526304", "25.031977"
        write_sources(d, [p], [babycare(1)])
        assert run(d) == 0
        recs = load(d)

        s = recs[pid(p)]
        assert (s["lng"], s["lat"]) == (121.526304, 25.031977), s
        assert s["source"] == "ece" and s["origin"] == "ece"
        assert s["postal_code"] == "100"
        assert s["address"] == "臺北市中正區測試路1號"
        assert s["capacity_approved"] == 120
        assert s["url"] is None and s["shuttle"] is None      # 空值轉 None
        assert s["owner"] == "王大明"                          # 個資留在私有主檔供歸戶
        assert s["is_active"] is True                         # 站方只列現存園所
        assert s["evaluations"][0]["year"] == "110"           # 評鑑歷史帶進主檔
        assert s["reg_id"] == "北市教前字第1120000001號"
        # 收費明細頁才有的兩欄，換源後暫缺
        assert s["free5"] is None and s["monthly_fee"] is None

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

    # 四、幼兒園主鍵規則（見 institutions 模組 docstring）
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        # 同縣市共用一張文號但園名不同 —— 雲林 19 家鄉鎮立幼兒園的真實情況，
        # 必須拿到不同 id，這正是主鍵含園名的理由
        a = preschool(1, name="雲林縣元長鄉立幼兒園", reg_no="八四考臺組貳一字第02400號")
        b = preschool(2, name="雲林縣元長鄉立幼兒園新生分班",
                      reg_no="八四考臺組貳一字第02400號")
        write_sources(d, [a, b], [babycare(1)])
        assert run(d) == 0
        assert pid(a) != pid(b) and {pid(a), pid(b)} <= set(load(d))

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        # 純排版差異（全形括號、台／臺）不得改變 id：站方寫法會隨時間飄
        full = preschool(1, name="臺北市實永非營利幼兒園（委託台灣某某基金會辦理）")
        half = preschool(1, name="臺北市實永非營利幼兒園(委託臺灣某某基金會辦理)")
        assert pid(full) == pid(half)
        # 改名則換 id —— 已知代價，實測改名率上限約 0.7%/年
        assert pid(preschool(1, name="新北市私立啟育幼兒園")) \
            != pid(preschool(1, name="新北市私立啟育藝術幼兒園"))
        # 三欄全同（站方重複列出）才走保底延伸：不中止，但兩筆仍拿到相異 id
        write_sources(d, [preschool(1), preschool(1)], [babycare(1)])
        assert run(d) == 0
        recs = load(d)
        assert len([r for r in recs.values() if r["kind"] == "preschool"]) == 2
        issues = json.loads((d / "data/institutions_issues.json").read_text())["issues"]
        assert all("主鍵撞號" in i["issue"] and i["gating"] is False for i in issues), issues

    # 四之二、托嬰中心 id 重複仍中止（上游主鍵語意改變，對照會錯接）
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        write_sources(d, [preschool(1)], [babycare(1), babycare(1)])
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

    # 五之二、墓碑名冊：消失的園結轉、復業的移出、裁罰對照才接得住歷史
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        all20 = [preschool(i) for i in range(20)]
        gone = all20[7]
        rest = [p for p in all20 if p is not gone]
        write_sources(d, all20, [babycare(1)])
        assert run(d, "2026-08-10") == 0
        assert load(d)[pid(gone)]["is_active"] is True
        hist = d / "data/ece/schools_history.json"
        assert json.loads(hist.read_text())["records"] == []

        # 從站方名冊消失 → 轉為墓碑，仍留在主檔供裁罰對照（20 家掉 1 家未觸發縮水門檻）
        write_sources(d, rest, [babycare(1)])
        assert run(d, "2026-08-11") == 0
        recs = load(d)
        assert recs[pid(gone)]["is_active"] is False
        assert recs[pid(gone)]["last_seen"] == "2026-08-10"   # 上一版的 as_of
        assert recs[pid(rest[0])]["is_active"] is True
        assert [r["id"] for r in json.loads(hist.read_text())["records"]] == [pid(gone)]

        # 墓碑不得讓縮水門檻失效：爬蟲半殘時筆數靠墓碑補回來，門檻仍須擋下
        write_sources(d, all20[:5], [babycare(1)])
        assert run(d, "2026-08-11") == 1

        # 換主鍵規則時整批 id 對不上，不可把舊主檔複製成墓碑（每家多一個分身）
        renamed = [preschool(i, reg_no=f"新規則字第{i}號") for i in range(20)]
        write_sources(d, renamed, [babycare(1)])
        assert run(d, "2026-08-11") == 1
        assert len(json.loads(hist.read_text())["records"]) == 1   # 墓碑名冊未被汙染

        # 復業 → 移出墓碑名冊，欄位以最新爬到的為準
        write_sources(d, all20, [babycare(1)])
        assert run(d, "2026-08-12") == 0
        assert load(d)[pid(gone)]["is_active"] is True
        assert json.loads(hist.read_text())["records"] == []

    # 六、--no-fetch 但無快照：明確失敗，不產生空主檔
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "data").mkdir(parents=True)
        assert run(d) == 1
        assert not (d / "data/institutions.json").exists()

    # 六之二、缺幼兒園主檔即中止：不像托嬰中心有 kiang 那份可退
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        write_sources(d, [preschool(1)], [babycare(1)])
        (d / "data" / "ece" / "schools.json").unlink()
        assert run(d) == 1
        assert not (d / "data/institutions.json").exists()

    print("test_institutions：全部通過")


if __name__ == "__main__":
    main()
