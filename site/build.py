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
      [--base-url URL] [--index]
      --index  移除 noindex（6b 掛上正式網域後才用）
"""

import json
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

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
    return f"/i/{inst['kind']}-{inst['native_id']}.html"


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
    # 內嵌於 <script>，把 < 轉義避免提前關閉標籤
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")


def dangerous_strings(*records) -> list[str]:
    out = set()
    for r in records:
        for v in r.values():
            if isinstance(v, str) and any(c in v for c in DANGEROUS):
                out.add(v)
    return sorted(out)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main(argv: list[str]) -> int:
    def opt(name: str, default=None):
        return argv[argv.index(name) + 1] if name in argv else default

    known = {"--public", "--out", "--base-url", "--index"}
    for a in argv:
        if a.startswith("--") and a not in known:
            log(f"未知參數：{a}")
            return 2

    repo = Path(__file__).resolve().parent.parent
    public = Path(opt("--public", repo / "build" / "public"))
    out = Path(opt("--out", repo / "build" / "site"))
    base_url = opt("--base-url", "https://preschool.ggggg.app").rstrip("/")
    noindex = "--index" not in argv

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
               "is_active": inst.get("is_active", True)}
        rows_by_id[inst["id"]] = row
        by_city[inst["city"] or "未分類"].append(row)
        if inst.get("town"):
            by_town[(inst["city"] or "未分類", inst["town"])].append(row)

    def order(rows):
        return sorted(rows, key=lambda r: (-(r["recent_penalty_count"] or 0),
                                           -(r["penalty_count"] or 0), r["name"]))

    urls = [{"path": "/", "priority": "1.0"}, {"path": href("/about.html")}]
    files = 0

    # ---------- 機構頁 ----------
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
            full_url=external(inst.get("url")), jsonld=jsonld_for(inst, url),
            map_url=map_link(inst),
            coverage_from=coverage.get(inst["source"], as_of)[:4],
            canonical=href(path))
        for raw in dangerous_strings(inst, *rows, *alias_by_inst.get(inst["id"], [])):
            if raw in html:
                escape_failures.append((path, raw[:60]))
        write(out / path.lstrip("/"), html)
        files += 1
        urls.append({"path": href(path), "priority": "0.8" if rows else None})

    # ---------- 縣市頁與鄉鎮市區頁 ----------
    city_cards = []
    listing = env.get_template("listing.html")
    for city, rows in sorted(by_city.items()):
        towns = [{"name": town, "url": href(town_path(city, town)),
                  "penalized": sum(1 for r in trows if r["recent_penalty_count"])}
                 for (c, town), trows in sorted(by_town.items()) if c == city]
        html = listing.render(heading=city, rows=order(rows), towns=towns, parent=None,
                              penalized=sum(1 for r in rows if r["recent_penalty_count"]),
                              canonical=href(city_path(city)))
        write(out / city_path(city).lstrip("/"), html)
        files += 1
        urls.append({"path": href(city_path(city)), "priority": "0.6"})
        city_cards.append({"name": city, "url": href(city_path(city)),
                           "total": len(rows),
                           "penalized": sum(1 for r in rows
                                            if r["recent_penalty_count"])})

    for (city, town), rows in sorted(by_town.items()):
        html = listing.render(heading=f"{city}{town}", rows=order(rows), towns=None,
                              parent={"name": city, "url": href(city_path(city))},
                              penalized=sum(1 for r in rows if r["recent_penalty_count"]),
                              canonical=href(town_path(city, town)))
        write(out / town_path(city, town).lstrip("/"), html)
        files += 1
        urls.append({"path": href(town_path(city, town)), "priority": "0.5"})

    # ---------- 首頁、關於頁 ----------
    write(out / "index.html", env.get_template("index.html").render(
        counts=meta["counts"], cities=city_cards, canonical="/"))
    write(out / "about.html", env.get_template("about.html").render(
        counts=meta["counts"], sources=meta["sources"], canonical=href("/about.html")))
    write(out / "404.html", env.get_template("404.html").render())
    files += 3

    # ---------- 地圖點位 ----------
    # 點位檔為預設載入，體積要省：座標取 5 位小數（約 1 公尺，地圖用綽綽有餘），
    # 網址只存 id 片段，由前端補上 /i/ 與 .html
    def slug(inst):
        return f"{inst['kind']}-{inst['native_id']}"

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
    write(out / "sitemap.xml", env.get_template("sitemap.xml").render(urls=urls))
    # 6a 期間靠頁面上的 noindex，robots 仍允許檢索——擋掉檢索就讀不到 noindex
    write(out / "robots.txt",
          f"User-agent: *\nAllow: /\nSitemap: {base_url}/sitemap.xml\n")
    files += 2
    if noindex:
        # HTML 有 meta noindex，但 sitemap 與 data/*.json 沒有；用 _headers 一次蓋全站
        write(out / "_headers", "/*\n  X-Robots-Tag: noindex, nofollow\n")
        files += 1

    assets = repo / "site" / "assets"
    for f in sorted(assets.iterdir()):
        if f.is_file():
            write(out / "assets" / f.name, f.read_text(encoding="utf-8"))
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
