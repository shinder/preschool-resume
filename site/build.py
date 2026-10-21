"""靜態站建置（工程執行計劃 6.4）：公開層 JSON → 機構頁／縣市頁／鄉鎮頁／地圖。

輸入是 6.3 的產物（`build/public/`），輸出可直接部署到 Workers static assets。
頁面型別五種：首頁地圖、機構頁（每家一頁）、縣市頁、鄉鎮市區頁、關於頁。
裁罰不另立頁而內嵌於機構頁——免費方案靜態資產上限 20,000 檔，這是控制檔案數的
主要手段（詳見第六階段技術決策表）。

兩道閘門：
- 檔案數超過 20,000 即中止（撞到就得升 Workers Paid 或縮小建頁範圍）
- 逸出掃描：資料裡含 `<>&"` 的字串若原樣出現在產物 HTML，代表某處漏了逸出，
  中止。Jinja2 已全域自動逸出，這道是防止日後有人加 `|safe`。

用法：.venv/bin/python site/build.py [--public PATH] [--out PATH]
      [--base-url URL] [--index] [--lastmod-state PATH]
      --index  移除 noindex（6b 掛上正式網域後才用）
      --lastmod-state  sitemap lastmod 的歷史檔（見 sitemap_lastmod()）；
               不給則每頁都標資料更新日
"""

import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "crawler"))

from common import log  # noqa: E402

try:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
except ModuleNotFoundError:  # pragma: no cover
    log("需要 Jinja2：python3 -m venv .venv && .venv/bin/pip install -r requirements.txt")
    raise

MAX_FILES = 20_000
DANGEROUS = ('<', '>', '&', '"')
NEIGHBOUR_LIMIT = 12


def inst_path(inst: dict) -> str:
    # 2026-08-11 拿掉 kind 前綴：兩種 native_id 格式互斥（幼兒園 16 位十六進位
    # 雜湊、托嬰 CW＋數字），共用 /i/ 不會撞；撞了由 main() 的路徑閘門擋下。
    # 舊網址由 _redirects 轉址接住。
    return f"/i/{inst['native_id']}.html"


def city_path(city: str) -> str:
    return f"/city/{city}.html"


def town_path(city: str, town: str) -> str:
    return f"/city/{city}/{town}.html"


def href(path: str) -> str:
    """檔案路徑 → 對外網址。檔名含中文，連結與 sitemap 一律百分比編碼。

    去掉 `.html`：Cloudflare 靜態資產的 `auto-trailing-slash` 會把 /foo.html
    301/307 導到 /foo，若連結與 sitemap 還寫 .html，等於每次瀏覽都多一跳。
    落地檔名仍是 .html（用 *_path()），只有對外網址不帶。
    """
    if path.endswith(".html"):
        path = path[: -len(".html")]
    return quote(path, safe="/-_.")


def external(url: str | None) -> str | None:
    if not url:
        return None
    return url if url.startswith(("http://", "https://")) else "https://" + url


def map_link(inst: dict) -> str | None:
    """導航用外連。家長看到地址第一件事是想知道在哪、怎麼去。"""
    q = inst.get("address") or inst.get("name")
    return f"https://www.google.com/maps/search/?api=1&query={quote(q)}" if q else None


def jsonld_dump(data: dict) -> str:
    """序列化為可直接嵌進 <script type="application/ld+json"> 的字串。

    模板必須用 `|safe` 輸出：script 元素的內容是純文字，HTML 實體不會被解碼，
    交給 Jinja 自動逸出的話 `&#34;` 會原樣留著，JSON 就解析不了
    （2026-08-10 發現線上的結構化資料一直是壞的，就是這個原因）。

    因此改由這裡負責安全性：把 DANGEROUS 那四個字元全部轉成 JSON 的 \\u 逸出。
    `<` 防的是提前關閉 script 標籤；`&`／`>` 則是為了讓 build 的逸出掃描仍然
    有意義——含這些字元的原始字串不會原樣出現在產物裡。`"` 由 json 自己處理。
    """
    return (json.dumps(data, ensure_ascii=False)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def jsonld_for(inst: dict, url: str) -> str:
    data = {
        "@context": "https://schema.org",
        "@type": "Preschool" if inst["kind"] == "preschool" else "ChildCare",
        "name": inst["name"],
        "url": url,
        "address": {"@type": "PostalAddress", "addressCountry": "TW",
                    "addressRegion": inst.get("city"),
                    "addressLocality": inst.get("town"),
                    "streetAddress": inst.get("address"),
                    "postalCode": inst.get("postal_code")},
    }
    if inst.get("lat") is not None and inst.get("lng") is not None:
        data["geo"] = {"@type": "GeoCoordinates",
                       "latitude": inst["lat"], "longitude": inst["lng"]}
    if inst.get("phone"):
        data["telephone"] = inst["phone"]
    return jsonld_dump(data)


def breadcrumb_jsonld(items: list[tuple[str, str | None]], base_url: str) -> str:
    """麵包屑結構化資料。畫面上本來就有「全台／縣市／鄉鎮」導覽，補這段是為了
    讓搜尋結果顯示成路徑而非裸網址。末項是當前頁，依慣例不給 item。"""
    data = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i, "name": name,
             **({"item": base_url + url} if url else {})}
            for i, (name, url) in enumerate(items, 1)
        ],
    }
    return jsonld_dump(data)


def dangerous_strings(*records) -> list[str]:
    out = set()
    for r in records:
        for v in r.values():
            if isinstance(v, str) and any(c in v for c in DANGEROUS):
                out.add(v)
    return sorted(out)


def fingerprint(html: str, as_of: str) -> str:
    """頁面內容指紋，供 sitemap lastmod 判斷「這頁是否真的變了」。

    每頁頁尾都印資料更新日，不剔除的話每天全站都算變動。"""
    return hashlib.sha1(html.replace(as_of, "").encode("utf-8")).hexdigest()[:16]


def sitemap_lastmod(fps: dict[str, str], state_path: Path | None,
                    as_of: str) -> dict[str, str]:
    """網址 → lastmod。指紋與上次相同就沿用上次的日期，變了才標今天。

    原本全站一律標建置日（每天 1 萬筆都「剛更新」），Google 會判定 lastmod
    不可信而整欄忽略，等於失去「哪幾頁有新裁罰」這個重新檢索的訊號。
    2026-10-21 查 GSC：上線兩個月 preschool 0 頁收錄、90 天只被檢索 17 次。

    歷史檔格式 {網址: [指紋, 日期]}，一行一筆以利 git diff。消失的網址
    自然不寫回。檔案由呼叫端在部署成功後才提交（見 site.yml），部署失敗
    時這次的變動不會被記成「已發布」。"""
    if state_path is None:
        return {u: as_of for u in fps}
    old = {}
    if state_path.exists():
        old = json.loads(state_path.read_text(encoding="utf-8"))
    state = {u: (old[u] if u in old and old[u][0] == fp else [fp, as_of])
             for u, fp in sorted(fps.items())}
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        "{\n" + ",\n".join(f"{json.dumps(u)}: {json.dumps(v)}"
                            for u, v in state.items()) + "\n}\n",
        encoding="utf-8")
    changed = sum(1 for u, v in state.items() if old.get(u, [None])[0] != v[0])
    log(f"sitemap lastmod：{len(state):,} 頁中 {changed:,} 頁內容有變，標為 {as_of}"
        f"（歷史檔 {'新建' if not old else '沿用'}）")
    return {u: v[1] for u, v in state.items()}


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main(argv: list[str]) -> int:
    def opt(name: str, default=None):
        return argv[argv.index(name) + 1] if name in argv else default

    known = {"--public", "--out", "--base-url", "--index", "--lastmod-state"}
    for a in argv:
        if a.startswith("--") and a not in known:
            log(f"未知參數：{a}")
            return 2

    repo = Path(__file__).resolve().parent.parent
    public = Path(opt("--public", repo / "build" / "public"))
    out = Path(opt("--out", repo / "build" / "site"))
    base_url = opt("--base-url", "https://preschool.shinder.work").rstrip("/")
    noindex = "--index" not in argv
    lastmod_state = Path(opt("--lastmod-state")) if "--lastmod-state" in argv else None

    if not (public / "institutions.json").exists():
        log(f"缺少公開層資料，請先執行 site/public_data.py：{public}")
        return 2

    def load(name, key):
        return json.loads((public / name).read_text(encoding="utf-8"))[key]

    institutions = load("institutions.json", "records")
    penalties = load("penalties.json", "records")
    aliases = load("aliases.json", "aliases")
    meta = json.loads((public / "meta.json").read_text(encoding="utf-8"))
    as_of = meta["as_of"]
    recent_since = meta.get("recent_since", "0000-00-00")
    env_recent_years = meta.get("recent_years", 5)

    by_inst = defaultdict(list)
    for p in penalties:
        by_inst[p["institution_id"]].append(p)
    for rows in by_inst.values():
        rows.sort(key=lambda r: (r.get("penalty_date") or "", r["id"]), reverse=True)
    alias_by_inst = defaultdict(list)
    for a in aliases:
        if a.get("institution_id"):
            alias_by_inst[a["institution_id"]].append(a)
    # 兩個來源的收錄起點不同（教保網回補自 2014、兒少法自 2011），分開算才不會誤述
    coverage = {}
    for p in penalties:
        src, d = p["source"], p.get("penalty_date")
        if d:
            coverage[src] = min(coverage.get(src, d), d)

    env = Environment(loader=FileSystemLoader(repo / "site" / "templates"),
                      autoescape=select_autoescape(["html", "xml"]),
                      trim_blocks=True, lstrip_blocks=True)
    env.globals.update(as_of=as_of, noindex=noindex, base_url=base_url,
                       recent_since=recent_since, recent_years=env_recent_years)

    # ---------- 索引：縣市／鄉鎮市區 ----------
    by_city, by_town = defaultdict(list), defaultdict(list)
    rows_by_id = {}
    for inst in institutions:
        row = {"name": inst["name"], "kind": inst["kind"], "town": inst.get("town"),
               "url": href(inst_path(inst)), "penalty_count": inst["penalty_count"],
               "recent_penalty_count": inst.get("recent_penalty_count", 0),
               "last_penalty_date": inst.get("last_penalty_date"),
               "is_active": inst.get("is_active", True),
               # 只有托嬰中心有實際收托數，幼兒園恆為 None（列表頁只在托嬰分頁出欄）。
               # 已停辦者不給空位：停辦後收托歸零，「核定減實際」會算出整批假空位。
               "vacancy": inst.get("vacancy") if inst.get("is_active", True) else None,
               "severe": inst.get("severe_recent_count", 0),
               "revoked": inst.get("revoked", False)}
        rows_by_id[inst["id"]] = row
        by_city[inst["city"] or "未分類"].append(row)
        if inst.get("town"):
            by_town[(inst["city"] or "未分類", inst["town"])].append(row)

    def order(rows):
        return sorted(rows, key=lambda r: (-(r["recent_penalty_count"] or 0),
                                           -(r["penalty_count"] or 0), r["name"]))

    urls = [{"path": "/", "priority": "1.0"}, {"path": href("/about.html")}]
    fps = {}  # 網址 → 內容指紋，供 sitemap lastmod
    files = 0

    # ---------- 機構頁 ----------
    # 路徑閘門：網址不帶 kind 前綴後，所有機構共用 /i/ 命名空間。現有兩種
    # native_id 格式互斥，這裡確保日後新增來源或改 id 規則時，撞出同一條路徑
    # 會讓建置失敗，而不是後寫的靜靜蓋掉先寫的。
    dupes = [p for p, n in Counter(inst_path(i) for i in institutions).items() if n > 1]
    if dupes:
        log(f"中止：機構路徑重複 {len(dupes)} 條，如 {dupes[:3]}")
        return 1

    # 同品牌提示（docs/07 方向一之 4）：清單與驗證在 public_data.brand_link_map，
    # 這裡只把其他成員的處分狀況攤開。廢照排最前，其次依近期重大處分與件數。
    inst_by_id = {i["id"]: i for i in institutions}

    def brand_related(inst: dict) -> list[dict]:
        links = inst.get("brand_links") or {}
        others = [inst_by_id[i] for i in links.get("ids", []) if i in inst_by_id]
        others.sort(key=lambda o: (not o.get("revoked"), -(o.get("severe_recent_count") or 0),
                                   -(o.get("recent_penalty_count") or 0), o["name"]))
        return [{"name": o["name"], "url": href(inst_path(o)),
                 "revoked": o.get("revoked", False), "revoked_date": o.get("revoked_date"),
                 "is_active": o.get("is_active", True),
                 "recent": o.get("recent_penalty_count", 0),
                 "severe": o.get("severe_recent_count", 0)} for o in others]

    escape_failures = []
    for inst in institutions:
        path = inst_path(inst)
        url = base_url + href(path)
        rows = by_inst.get(inst["id"], [])
        entries = [{**p, "archived": bool(p.get("removed_at"))} for p in rows]
        # 近五年預設展開，更早的收合——保留但不佔滿版面（見公開層 RECENT_YEARS）
        recent = [e for e in entries if (e.get("penalty_date") or "") >= recent_since]
        older = [e for e in entries if (e.get("penalty_date") or "") < recent_since]
        siblings = [r for r in by_town.get((inst["city"], inst.get("town")), [])
                    if r["url"] != href(path) and r["kind"] == inst["kind"]]
        html = env.get_template("institution.html").render(
            inst=inst, penalties=recent, older=older,
            aliases=alias_by_inst.get(inst["id"], []),
            archived_count=sum(1 for p in entries if p["archived"]),
            city_url=href(city_path(inst["city"])) if inst.get("city") else "/",
            town_url=href(town_path(inst["city"], inst["town"])) if inst.get("town") else None,
            neighbours=order(siblings)[:NEIGHBOUR_LIMIT],
            brand=(inst.get("brand_links") or {}).get("brand"),
            brand_related=brand_related(inst),
            full_url=external(inst.get("url")), jsonld=jsonld_for(inst, url),
            map_url=map_link(inst),
            coverage_from=coverage.get(inst["source"], as_of)[:4],
            canonical=href(path),
            breadcrumb=breadcrumb_jsonld(
                [("全台", "/")]
                + ([(inst["city"], href(city_path(inst["city"])))] if inst.get("city") else [])
                + ([(inst["town"], href(town_path(inst["city"], inst["town"])))]
                   if inst.get("town") else [])
                + [(inst["name"], None)], base_url))
        for raw in dangerous_strings(inst, *rows, *alias_by_inst.get(inst["id"], [])):
            if raw in html:
                escape_failures.append((path, raw[:60]))
        write(out / path.lstrip("/"), html)
        files += 1
        urls.append({"path": href(path), "priority": "0.8" if rows else None})
        fps[href(path)] = fingerprint(html, as_of)

    # ---------- 縣市頁與鄉鎮市區頁 ----------
    city_cards = []
    listing = env.get_template("listing.html")
    for city, rows in sorted(by_city.items()):
        towns = [{"name": town, "url": href(town_path(city, town)),
                  "penalized": sum(1 for r in trows if r["recent_penalty_count"])}
                 for (c, town), trows in sorted(by_town.items()) if c == city]
        html = listing.render(heading=city, rows=order(rows), towns=towns, parent=None,
                              penalized=sum(1 for r in rows if r["recent_penalty_count"]),
                              canonical=href(city_path(city)),
                              breadcrumb=breadcrumb_jsonld(
                                  [("全台", "/"), (city, None)], base_url))
        write(out / city_path(city).lstrip("/"), html)
        files += 1
        urls.append({"path": href(city_path(city)), "priority": "0.6"})
        fps[href(city_path(city))] = fingerprint(html, as_of)
        city_cards.append({"name": city, "url": href(city_path(city)),
                           "total": len(rows),
                           "penalized": sum(1 for r in rows
                                            if r["recent_penalty_count"])})

    for (city, town), rows in sorted(by_town.items()):
        html = listing.render(heading=f"{city}{town}", rows=order(rows), towns=None,
                              parent={"name": city, "url": href(city_path(city))},
                              penalized=sum(1 for r in rows if r["recent_penalty_count"]),
                              canonical=href(town_path(city, town)),
                              breadcrumb=breadcrumb_jsonld(
                                  [("全台", "/"), (city, href(city_path(city))),
                                   (town, None)], base_url))
        write(out / town_path(city, town).lstrip("/"), html)
        files += 1
        urls.append({"path": href(town_path(city, town)), "priority": "0.5"})
        fps[href(town_path(city, town))] = fingerprint(html, as_of)

    # ---------- 首頁、關於頁 ----------
    html = env.get_template("index.html").render(
        counts=meta["counts"], cities=city_cards, canonical="/")
    write(out / "index.html", html)
    fps["/"] = fingerprint(html, as_of)
    html = env.get_template("about.html").render(
        counts=meta["counts"], sources=meta["sources"], canonical=href("/about.html"))
    write(out / "about.html", html)
    fps[href("/about.html")] = fingerprint(html, as_of)
    write(out / "404.html", env.get_template("404.html").render())
    files += 3

    # ---------- 地圖點位 ----------
    # 點位檔為預設載入，體積要省：座標取 5 位小數（約 1 公尺，地圖用綽綽有餘），
    # 網址只存 id 片段，由前端補上 /i/ 與 .html
    def slug(inst):
        return inst["native_id"]

    def coord(v):
        return round(v, 5)

    def point(inst):
        return [coord(inst["lng"]), coord(inst["lat"]),
                inst.get("recent_penalty_count", 0), inst["name"], slug(inst)]

    def vacancy_point(inst):
        return [coord(inst["lng"]), coord(inst["lat"]), inst["vacancy"],
                inst["name"], slug(inst), inst["capacity_approved"],
                inst["enrolled"], inst.get("enrolled_as_of")]

    # 已停辦者不上地圖：墓碑名冊的用途是接住改名／借殼的裁罰歷史（見
    # institutions.carry_forward），不是要在圖上展示一家已經不存在的園。
    # 機構頁與列表頁仍然保留它們，並掛「已停辦」徽章。
    located = [i for i in institutions
               if i.get("lng") is not None and i.get("is_active", True)]
    closed = sum(1 for i in institutions
                 if i.get("lng") is not None and not i.get("is_active", True))
    penalized = [i for i in located if i.get("recent_penalty_count")]
    # 缺額圖層：只有托嬰中心有實際收托數
    with_vacancy = [i for i in located if i.get("vacancy") is not None]
    write(out / "data" / "points-all.json", json.dumps(
        {"cols": ["lng", "lat", "n", "name", "slug"],
         "rows": [point(i) for i in located]}, ensure_ascii=False))
    write(out / "data" / "points-vacancy.json", json.dumps(
        {"cols": ["lng", "lat", "vacancy", "name", "slug", "capacity", "enrolled",
                  "as_of"],
         "rows": [vacancy_point(i) for i in with_vacancy]}, ensure_ascii=False))
    files += 2

    # ---------- sitemap 與 robots ----------
    lastmod = sitemap_lastmod(fps, lastmod_state, as_of)
    for u in urls:
        u["lastmod"] = lastmod[u["path"]]
    write(out / "sitemap.xml", env.get_template("sitemap.xml").render(urls=urls))
    # 帶 kind 前綴的舊機構網址（2026-08-10 上線的第一版）轉到新網址。
    # Workers 靜態資產的 _redirects：動態規則上限 100 條，這裡用 2 條。
    write(out / "_redirects",
          "/i/preschool-* /i/:splat 301\n/i/babycare-* /i/:splat 301\n")
    files += 1
    # 6a 期間靠頁面上的 noindex，robots 仍允許檢索——擋掉檢索就讀不到 noindex
    write(out / "robots.txt",
          f"User-agent: *\nAllow: /\nSitemap: {base_url}/sitemap.xml\n")
    files += 2
    if noindex:
        # HTML 有 meta noindex，但 sitemap 與 data/*.json 沒有；用 _headers 一次蓋全站
        write(out / "_headers", "/*\n  X-Robots-Tag: noindex, nofollow\n")
        files += 1

    assets = repo / "site" / "assets"
    # 二進位資產（og:image 等）不能走 read_text，會 UnicodeDecodeError 讓建置整個掛掉
    BINARY = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2"}
    for f in sorted(assets.iterdir()):
        if f.is_file() and f.suffix.lower() in BINARY:
            dest = out / "assets" / f.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(f.read_bytes())
            files += 1
        elif f.is_file():
            text = f.read_text(encoding="utf-8")
            # SVG 是 XML，格式不合法時瀏覽器會靜靜地不畫，檔案本身仍然 200，
            # 從產物或 HTTP 狀態完全看不出來。favicon 第一版就是這樣壞的
            # （註解裡寫了 --indigo，XML 註解不得有連續兩個減號）。
            if f.suffix == ".svg":
                try:
                    ElementTree.fromstring(text)
                except ElementTree.ParseError as e:
                    log(f"中止：{f.name} 不是合法的 XML——{e}")
                    return 1
            write(out / "assets" / f.name, text)
            files += 1

    # 站根靜態檔（如 ads.txt、security.txt）：site/root/ 下的檔案原樣複製到
    # 產物根目錄。這類檔案必須在根路徑才有效，放 assets/ 不行。
    #
    # 檔名不要取 .html。wrangler.jsonc 的 html_handling auto-trailing-slash
    # 會把 /foo.html 以 307 導向 /foo，凡是要求該網址「直接回 200」的驗證
    # 機制都會失敗——2026-08-23 GSC 站主驗證改走 DNS TXT（shinder.work 網域
    # 資源，TXT 在頂點）就是踩到這一點，原本的 HTML 驗證檔已移除。
    root_files = repo / "site" / "root"
    if root_files.is_dir():
        for f in sorted(root_files.iterdir()):
            if f.is_file():
                write(out / f.name, f.read_text(encoding="utf-8"))
                files += 1

    log(f"產出 {files:,} 檔於 {out}：機構 {len(institutions):,}、"
        f"縣市 {len(city_cards)}、鄉鎮市區 {len(by_town)}；"
        f"地圖點位 {len(located):,}（近五年有裁罰 {len(penalized):,}；"
        f"已停辦 {closed:,} 家不上圖）、缺額圖層 {len(with_vacancy):,}"
        f"（有空位 {sum(1 for i in with_vacancy if i['vacancy'] > 0):,}）")
    if noindex:
        log("全站掛 noindex（6a）；6b 掛正式網域時加 --index")

    if escape_failures:
        for path, raw in escape_failures[:10]:
            log(f"  逸出失敗：{path} 出現原始字串「{raw}」")
        log(f"中止：{len(escape_failures)} 處資料未經 HTML 逸出")
        return 1
    if files >= MAX_FILES:
        log(f"中止：檔案數 {files:,} 已達免費方案上限 {MAX_FILES:,}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
