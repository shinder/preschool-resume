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
         "recent_penalty_count": 1, "severe_recent_count": 1,
         "fine_recent_total": 66_000, "revoked": False, "dense": False},
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
         "removed_at": None, "origin": None, "name_at_penalty": None,
         "severe": True},
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
                    "i/a.html", "i/b.html", "i/CW1.html",
                    "city/臺北市.html", "city/臺北市/中正區.html",
                    "city/新北市.html", "city/新北市/板橋區.html",
                    "data/points-all.json", "data/points-vacancy.json",
                    "assets/style.css", "assets/leaflet.js"):
            assert (out / rel).exists(), f"缺少 {rel}"
        # 裁罰內嵌於機構頁，不另立頁（檔案數控制的關鍵）
        assert not (out / "p").exists()

        page = (out / "i/a.html").read_text()

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

        # 警示分級（docs/07 方向一）：徽章、累計罰鍰與卷宗條目的重大處分標記
        assert "重大處分 1 筆" in page
        assert "近 5 年罰鍰累計 66,000 元" in page
        assert 'class="sevmark"' in page
        # 未廢照、未密集者不得冒出對應徽章
        assert "遭廢止設立許可" not in page and "短期密集裁罰" not in page
        # 列表頁：重大處分標記跟著機構列
        assert "重大處分" in (out / "city/臺北市.html").read_text()
        # 無重大處分的機構頁與其所在列表不受影響
        assert "重大處分" not in (out / "i/CW1.html").read_text()
        assert "重大處分" not in (out / "city/新北市.html").read_text()
        # 內部連結：同鄉鎮同類型的其他機構。網址不帶 .html——落地檔名是 .html，
        # 但 Cloudflare 的 auto-trailing-slash 會把 /foo.html 導到 /foo，
        # 連結若寫 .html 等於每次瀏覽都多一跳。
        assert 'href="/i/b"' in page, "內部連結應不帶 .html"
        assert "i/b.html" not in page
        # 模板裡手寫的連結也不能帶 .html——href() 只管程式產生的網址，
        # 頁首導覽的 /about.html 就是這樣漏了一輪，每頁都白吃一次 307
        import re as _re2
        stale = _re2.findall(r'href="(/[^"]*\.html)"', page)
        assert not stale, f"仍有帶 .html 的連結：{stale}"
        # canonical 指向同一個無副檔名網址
        assert ('<link rel="canonical" href="https://example.workers.dev/i/a">'
                in page), "缺少 canonical"

        # 結構化資料必須真的能被 JSON.parse——script 內容是純文字，HTML 實體
        # 不會被解碼，若交給 Jinja 自動逸出，&#34; 會原樣留著而解析失敗
        # （線上曾經就是這樣壞掉的，肉眼看 HTML 完全看不出來）
        import re as _re
        blocks = _re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, _re.S)
        assert len(blocks) == 2, f"應有 Preschool 與 BreadcrumbList 兩段，實得 {len(blocks)}"
        types = []
        for b in blocks:
            types.append(json.loads(b)["@type"])          # 解析失敗就直接爆
        assert types == ["Preschool", "BreadcrumbList"], types
        crumb = json.loads(blocks[1])["itemListElement"]
        assert [c["name"] for c in crumb] == ["全台", "臺北市", "中正區", NASTY], crumb
        assert crumb[1]["item"] == "https://example.workers.dev/city/%E8%87%BA%E5%8C%97%E5%B8%82"
        assert "item" not in crumb[-1]                     # 末項是當前頁
        # 危險字元一律走 JSON 的 \u 逸出，不得原樣出現（否則 </script> 可提前收尾）
        assert "\\u003cscript" in blocks[0] and "<script>alert" not in blocks[0]

        # 分享預覽與 favicon
        assert '<meta property="og:title" content="' in page
        assert '<meta property="og:url" content="https://example.workers.dev/i/a">' in page
        assert '<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml">' in page
        assert '<meta property="og:image" content="https://example.workers.dev/assets/og.png">' in page
        assert '<meta name="twitter:card" content="summary_large_image">' in page
        # 二進位資產必須原樣複製——走 read_text 會 UnicodeDecodeError 讓建置整個掛掉
        src = Path(build.__file__).parent / "assets" / "og.png"
        assert (out / "assets/og.png").read_bytes() == src.read_bytes()
        from xml.etree import ElementTree
        # SVG 壞掉時瀏覽器只是靜靜不畫、檔案照樣 200，肉眼與 HTTP 都看不出來
        ElementTree.fromstring((out / "assets/favicon.svg").read_text())

        # 聯絡方式：每頁頁尾都有，關於頁另有更正原則
        assert 'mailto:service@shinder.work' in page
        about = (out / "about.html").read_text()
        assert "更正原則" in about and "不因當事人要求而刪除" in about

        # 6a 全站 noindex，且 robots 仍允許檢索（擋掉就讀不到 noindex）
        assert '<meta name="robots" content="noindex, nofollow">' in page
        assert "Disallow" not in (out / "robots.txt").read_text()
        # sitemap 與 data/*.json 沒有 meta 可掛，靠 _headers 蓋全站
        assert "X-Robots-Tag: noindex" in (out / "_headers").read_text()

        # 帶 kind 前綴的第一版網址要轉到新網址
        redirects = (out / "_redirects").read_text()
        assert "/i/preschool-* /i/:splat 301" in redirects
        assert "/i/babycare-* /i/:splat 301" in redirects

        # 無裁罰的機構頁要有實質內容與空狀態說明
        empty = (out / "i/CW1.html").read_text()
        # 四分之三的機構頁沒有裁罰，這段空狀態要說清楚「沒查到」是有意義的結論，
        # 而不是資料不全——這正是本站收錄下架公告的價值所在
        assert "這家目前沒有裁罰紀錄" in empty and "核定收托" in empty
        assert "含官方已下架的部分" in empty and "都在這裡了" in empty

        # 地圖點位：預設圖層放全部「現存」機構（沒被罰的也要看得到），件數為近五年
        allp = json.loads((out / "data/points-all.json").read_text())
        assert len(allp["rows"]) == 2
        # 已停辦者不上圖——墓碑名冊是為了接住改名／借殼的裁罰歷史，不是要展示
        assert not [r for r in allp["rows"] if r[4] == "b"], allp["rows"]
        # 但機構頁與列表頁仍然保留，並掛「已停辦」徽章
        assert "已停辦" in (out / "i/b.html").read_text()
        hit = [r for r in allp["rows"] if r[2]]
        assert len(hit) == 1 and hit[0][2] == 1
        assert hit[0][4] == "a"          # 網址片段，前端補 /i/
        # 缺額圖層只收有實際收托數的托嬰中心
        vac = json.loads((out / "data/points-vacancy.json").read_text())
        assert len(vac["rows"]) == 1 and vac["rows"][0][2] == 5
        assert "約 5 個" in (out / "i/CW1.html").read_text()
        # 列表頁只有托嬰分頁有空位欄（幼兒園沒有實際收托數）
        listing_page = (out / "city/新北市.html").read_text()
        assert listing_page.count("<th>空位</th>") == 1
        assert '<td class="num">5</td>' in listing_page

        # sitemap 收錄所有頁面且網址經過編碼
        sitemap = (out / "sitemap.xml").read_text()
        assert sitemap.count("<loc>") == 3 + 2 + 2 + 2
        assert "<loc>https://example.workers.dev/city/%E8%87%BA%E5%8C%97%E5%B8%82</loc>" in sitemap
        assert ".html<" not in sitemap          # sitemap 也不得帶 .html，否則每筆都是一跳

    # 近五年若有已下架紀錄，要就地說明「輕罰比重罰早下架」——這個說明原本只在
    # 「更早的紀錄」收合區塊裡，十筆都在近五年的頁面完全看不到（實例：花蓮縣
    # 私立何嘉仁幼兒園，3,000／6,000 元的輕罰已下架，60,000 元的重罰還在）
    with tempfile.TemporaryDirectory() as tmp:
        public, out = Path(tmp) / "public", Path(tmp) / "site"
        public.mkdir()
        payloads = json.loads(json.dumps(PUBLIC))       # 深拷貝，不污染其他案例
        payloads["penalties.json"]["records"][0]["removed_at"] = "2026-07-01"
        for name, payload in payloads.items():
            (public / name).write_text(json.dumps(payload, ensure_ascii=False))
        assert build.main(["--public", str(public), "--out", str(out)]) == 0
        page = (out / "i/a.html").read_text()
        assert "其中 1 筆官方已無" in page, "近五年有下架紀錄時要就地說明"
        assert "輕罰比重罰早下架" in page

    # 沒有下架紀錄時不該冒出這段說明
    with tempfile.TemporaryDirectory() as tmp:
        public, out = Path(tmp) / "public", Path(tmp) / "site"
        public.mkdir()
        for name, payload in PUBLIC.items():
            (public / name).write_text(json.dumps(payload, ensure_ascii=False))
        assert build.main(["--public", str(public), "--out", str(out)]) == 0
        assert "筆官方已無。" not in (out / "i/CW1.html").read_text()

    # 廢照徽章蓋過「已停辦」——兩者是不同訊息，廢照是官方處分、已停辦只是
    # 名冊狀態；密集裁罰徽章同場驗證
    with tempfile.TemporaryDirectory() as tmp:
        public, out = Path(tmp) / "public", Path(tmp) / "site"
        public.mkdir()
        payloads = json.loads(json.dumps(PUBLIC))
        payloads["institutions.json"]["records"][1]["revoked"] = True
        payloads["institutions.json"]["records"][0]["dense"] = True
        for name, payload in payloads.items():
            (public / name).write_text(json.dumps(payload, ensure_ascii=False))
        assert build.main(["--public", str(public), "--out", str(out)]) == 0
        page_b = (out / "i/b.html").read_text()
        assert "遭廢止設立許可" in page_b
        assert "名冊顯示已停辦" not in page_b
        listing_page = (out / "city/臺北市.html").read_text()
        assert "廢止許可" in listing_page and ">已停辦<" not in listing_page
        assert "短期密集裁罰" in (out / "i/a.html").read_text()

    # 同品牌提示：其他成員的處分狀況攤在機構頁頂部，廢照排最前；沒有清單的機構不出現
    with tempfile.TemporaryDirectory() as tmp:
        public, out = Path(tmp) / "public", Path(tmp) / "site"
        public.mkdir()
        payloads = json.loads(json.dumps(PUBLIC))
        recs = payloads["institutions.json"]["records"]
        recs[1].update(revoked=True, revoked_date="2026-07-29")
        recs[0]["brand_links"] = {"brand": "私立", "ids": ["preschool:b"]}
        recs[1]["brand_links"] = {"brand": "私立", "ids": ["preschool:a"]}
        for name, payload in payloads.items():
            (public / name).write_text(json.dumps(payload, ensure_ascii=False))
        assert build.main(["--public", str(public), "--out", str(out)]) == 0
        page = (out / "i/a.html").read_text()
        assert '<h2 id="brandnote-h">同品牌機構</h2>' in page
        assert 'href="/i/b">臺北市私立乙幼兒園</a>：<strong>遭廢止設立許可</strong>' in page
        assert "2026-07-29" in page and "共用「私立」品牌" in page
        page_b = (out / "i/b.html").read_text()
        assert "重大處分 <span class=\"num\">1</span> 筆" in page_b, page_b
        assert "同品牌機構" not in (out / "i/CW1.html").read_text()

    # 路徑閘門：網址不帶 kind 前綴後，native_id 撞名要讓建置失敗而不是互蓋
    with tempfile.TemporaryDirectory() as tmp:
        public, out = Path(tmp) / "public", Path(tmp) / "site"
        public.mkdir()
        payloads = json.loads(json.dumps(PUBLIC))
        clash = json.loads(json.dumps(payloads["institutions.json"]["records"][-1]))
        clash["id"], clash["native_id"] = "babycare:a", "a"   # 與 preschool:a 同路徑
        payloads["institutions.json"]["records"].append(clash)
        for name, payload in payloads.items():
            (public / name).write_text(json.dumps(payload, ensure_ascii=False))
        assert build.main(["--public", str(public), "--out", str(out)]) == 1

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

    # sitemap lastmod：內容沒變的頁沿用上次日期，變了才標新的資料更新日。
    # 全站每天都標今天，Google 會判定 lastmod 不可信而整欄忽略。
    with tempfile.TemporaryDirectory() as tmp:
        public, out = Path(tmp) / "public", Path(tmp) / "site"
        state = Path(tmp) / "lastmod.json"
        public.mkdir()

        def build_on(as_of, payloads):
            for name, payload in payloads.items():
                payload["as_of"] = as_of
                (public / name).write_text(json.dumps(payload, ensure_ascii=False))
            assert build.main(["--public", str(public), "--out", str(out),
                               "--lastmod-state", str(state)]) == 0
            import re as _re3
            return dict(_re3.findall(r"<loc>https://[^/]+(/[^<]*)</loc>"
                                     r"<lastmod>([^<]+)</lastmod>",
                                     (out / "sitemap.xml").read_text()))

        first = build_on("2026-08-09", json.loads(json.dumps(PUBLIC)))
        assert set(first.values()) == {"2026-08-09"}      # 首次全標當天
        payloads = json.loads(json.dumps(PUBLIC))
        payloads["institutions.json"]["records"][2]["enrolled"] = 28
        second = build_on("2026-08-10", payloads)
        changed = {u for u, d in second.items() if d == "2026-08-10"}
        # 頁尾的資料更新日不算變動；改了托嬰中心收托數，只有它與所在列表頁變
        assert "/i/CW1" in changed and "/i/a" not in changed, changed
        assert "/about" not in changed, changed
        assert second["/i/a"] == "2026-08-09"
        assert all(u.startswith(("/i/CW1", "/city/")) or u == "/" for u in changed), changed
        # 歷史檔一行一筆，供 git diff 閱讀
        assert len(state.read_text().splitlines()) == len(second) + 2

    print("test_build：全部通過")


if __name__ == "__main__":
    main()
