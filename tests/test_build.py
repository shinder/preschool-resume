"""靜態站建置驗收測試（工程執行計劃 6.4 完成定義）：
頁面齊全、裁罰內嵌不另立頁、下架狀態標示正確，且資料一律經過 HTML 逸出。

零依賴以外需要 Jinja2：.venv/bin/python tests/test_build.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site"))

import build  # noqa: E402

# 名稱裡帶 < > & " 的機構——這是逸出掃描要盯的對象
NASTY = '「甲」<script>alert(1)</script> & "測試" 幼兒園'

PUBLIC = {
    "institutions.json": {"as_of": "2026-08-09", "records": [
        {"id": "preschool:a", "kind": "preschool", "source": "ece", "native_id": "a",
         "name": NASTY, "city": "臺北市", "town": "中正區", "postal_code": "100",
         "address": "臺北市中正區測試路1號", "lng": 121.5, "lat": 25.0,
         "phone": "(02)12345678", "url": "example.tw/k1", "org_type": "私立",
         "capacity_approved": 120, "enrolled": None, "pre_public": "無",
         "monthly_fee": 3000, "is_active": True, "reg_no": "北市教字第1號",
         "penalty_count": 2, "last_penalty_date": "2024-01-01",
         "removed_penalty_count": 1, "archived_penalty_count": 0,
         "recent_penalty_count": 1},
        {"id": "preschool:b", "kind": "preschool", "source": "ece", "native_id": "b",
         "name": "臺北市私立乙幼兒園", "city": "臺北市", "town": "中正區",
         "address": "臺北市中正區測試路2號", "lng": 121.51, "lat": 25.01,
         "is_active": False, "penalty_count": 0, "last_penalty_date": None,
         "removed_penalty_count": 0, "archived_penalty_count": 0,
         "recent_penalty_count": 0},
        {"id": "babycare:CW1", "kind": "babycare", "source": "sfaa", "native_id": "CW1",
         "name": "新北市私立丙托嬰中心", "city": "新北市", "town": "板橋區",
         "address": "新北市板橋區測試路3號", "lng": 121.4, "lat": 25.0,
         "capacity_approved": 30, "enrolled": 25, "is_active": True,
         "penalty_count": 0, "last_penalty_date": None,
         "removed_penalty_count": 0, "archived_penalty_count": 0,
         "recent_penalty_count": 0, "vacancy": 5},
    ]},
    "penalties.json": {"as_of": "2026-08-09", "records": [
        {"id": "ece:1", "source": "ece", "institution_id": "preschool:a",
         "city": "臺北市", "doc_no": "北市教幼字第1號", "law": "幼兒教育及照顧法第52條",
         "violation": "第26條第2項 <未逸出測試>", "fact": None,
         "sanction": "罰鍰：6,000元", "penalty_date": "2024-01-01",
         "removed_at": None, "origin": None, "name_at_penalty": None},
        {"id": "ece:2", "source": "ece", "institution_id": "preschool:a",
         "city": "臺北市", "doc_no": "北市教幼字第2號", "law": "幼兒教育及照顧法第52條",
         "violation": "第8條", "fact": None, "sanction": "罰鍰：6,000元",
         "penalty_date": "2023-05-05", "removed_at": "2026-08-01", "origin": None,
         "name_at_penalty": "臺北市私立舊名幼兒園"},
    ]},
    "aliases.json": {"as_of": "2026-08-09", "aliases": [
        {"institution_id": "preschool:a", "current_name": NASTY,
         "former_name": "臺北市私立舊名幼兒園", "type": "renamed", "penalty_count": 1,
         "first_penalty_date": "2023-05-05", "last_penalty_date": "2023-05-05"},
    ]},
    "meta.json": {"as_of": "2026-08-09", "recent_since": "2024-01-01",
        "recent_years": 5, "counts": {
        "institutions": 3, "institutions_with_penalty": 1, "penalties": 2,
        "removed_penalties": 1, "archived_penalties": 0, "aliases": 1},
        "sources": [{"name": "測試來源", "url": "https://example.gov.tw"}],
        "notice": "測試"},
}


def main():
    with tempfile.TemporaryDirectory() as tmp:
        public, out = Path(tmp) / "public", Path(tmp) / "site"
        public.mkdir()
        for name, payload in PUBLIC.items():
            (public / name).write_text(json.dumps(payload, ensure_ascii=False))

        assert build.main(["--public", str(public), "--out", str(out),
                           "--base-url", "https://example.workers.dev"]) == 0

        # 頁面齊全：3 機構＋2 縣市＋2 鄉鎮＋首頁＋關於
        for rel in ("index.html", "about.html", "404.html", "_headers",
                    "sitemap.xml", "robots.txt",
                    "i/preschool-a.html", "i/preschool-b.html", "i/babycare-CW1.html",
                    "city/臺北市.html", "city/臺北市/中正區.html",
                    "city/新北市.html", "city/新北市/板橋區.html",
                    "data/points-all.json", "data/points-vacancy.json",
                    "assets/style.css", "assets/leaflet.js"):
            assert (out / rel).exists(), f"缺少 {rel}"
        # 裁罰內嵌於機構頁，不另立頁（檔案數控制的關鍵）
        assert not (out / "p").exists()

        page = (out / "i/preschool-a.html").read_text()

        # 完成定義：資料一律經過 HTML 逸出
        assert "<script>alert(1)</script>" not in page
        assert "&lt;script&gt;" in page
        assert "第26條第2項 <未逸出測試>" not in page

        # 揭露分層：近五年展開、更早的收合，但仍完整保留於頁面上
        assert "近 5 年 1 筆裁罰" in page and "更早 1 筆" in page
        assert "<summary>更早的紀錄（1 筆，最早 2023-05-05）</summary>" in page
        assert page.count('class="entry') == 2      # 兩筆都在 DOM 裡，只是一筆收合

        # 下架狀態：兩種標示要分得出來
        assert "官方公告中" in page and "官方已無、本站保留" in page
        assert "處分時名稱：臺北市私立舊名幼兒園" in page
        assert "官方已無 1 筆" in page
        # pre_public 為「無」時不該冒出徽章
        assert "準公共 無" not in page
        # 內部連結：同鄉鎮同類型的其他機構
        assert "i/preschool-b.html" in page

        # 6a 全站 noindex，且 robots 仍允許檢索（擋掉就讀不到 noindex）
        assert '<meta name="robots" content="noindex, nofollow">' in page
        assert "Disallow" not in (out / "robots.txt").read_text()
        # sitemap 與 data/*.json 沒有 meta 可掛，靠 _headers 蓋全站
        assert "X-Robots-Tag: noindex" in (out / "_headers").read_text()

        # 無裁罰的機構頁要有實質內容與空狀態說明
        empty = (out / "i/babycare-CW1.html").read_text()
        assert "目前查無裁罰紀錄" in empty and "核定收托" in empty

        # 地圖點位：預設只給有裁罰者
        allp = json.loads((out / "data/points-all.json").read_text())
        # 預設圖層放全部機構（沒被罰的也要看得到），件數為近五年
        assert len(allp["rows"]) == 3
        hit = [r for r in allp["rows"] if r[2]]
        assert len(hit) == 1 and hit[0][2] == 1
        assert hit[0][4] == "preschool-a"          # 網址片段，前端補 /i/ 與 .html
        # 缺額圖層只收有實際收托數的托嬰中心
        vac = json.loads((out / "data/points-vacancy.json").read_text())
        assert len(vac["rows"]) == 1 and vac["rows"][0][2] == 5
        assert "約 5 個" in (out / "i/babycare-CW1.html").read_text()

        # sitemap 收錄所有頁面且網址經過編碼
        sitemap = (out / "sitemap.xml").read_text()
        assert sitemap.count("<loc>") == 3 + 2 + 2 + 2
        assert "/city/%E8%87%BA%E5%8C%97%E5%B8%82.html" in sitemap

    # 逸出閘門：有人在模板加了 |safe 就要擋下來
    with tempfile.TemporaryDirectory() as tmp:
        public, out = Path(tmp) / "public", Path(tmp) / "site"
        public.mkdir()
        for name, payload in PUBLIC.items():
            (public / name).write_text(json.dumps(payload, ensure_ascii=False))
        tpl = Path(build.__file__).parent / "templates" / "institution.html"
        original = tpl.read_text(encoding="utf-8")
        tpl.write_text(original.replace("<h1>{{ inst.name }}</h1>",
                                        "<h1>{{ inst.name|safe }}</h1>"),
                       encoding="utf-8")
        try:
            assert build.main(["--public", str(public), "--out", str(out)]) == 1
        finally:
            tpl.write_text(original, encoding="utf-8")

    print("test_build：全部通過")


if __name__ == "__main__":
    main()
