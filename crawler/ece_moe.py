"""全國教保資訊網（幼兒園）裁罰紀錄備份爬蟲。

站點形態：ASP.NET WebForms。先 GET 取得 VIEWSTATE，POST 空關鍵字查全國，
翻頁靠 PageControl1$lbNextPage postback；每園的裁罰詳情是列表頁內
punish_view.aspx?sch=<token> 連結，token 綁 session，需同一 session 內抓完。
站方 TLS 憑證鏈不完整，關閉驗證。

快照落地：raw/ece/list/pNNNN.html、raw/ece/detail/{縣市}_{園名}.html。
存檔前剝除 VIEWSTATE 等 session 雜訊，避免每日無意義 diff。
"""

import re
import sys
from pathlib import Path

from common import Fetcher, log, parse_args, safe_filename, save, strip_session_noise

SEARCH_URL = "https://ap.ece.moe.edu.tw/webecems/punishSearch.aspx"
DETAIL_URL = "https://ap.ece.moe.edu.tw/webecems/dtl/punish_view.aspx?sch={token}"

HIDDEN_RE = re.compile(r'<input type="hidden" name="(__[^"]+)" id="[^"]*" value="([^"]*)"')
NAME_RE = re.compile(r'id="GridView1_lblSchName_(\d+)"[^>]*>([^<]+)</span>')
CITY_RE = re.compile(r'id="GridView1_lblCity_(\d+)"[^>]*>([^<]+)</span>')
TOKEN_RE = re.compile(
    r'<a\b[^>]*punish_view\.aspx\?sch=([A-Za-z0-9+/=%]+)&#39;[^>]*id="GridView1_lbView_(\d+)"'
)
NEXT_PAGE = "PageControl1$lbNextPage"


def form_fields(html: str) -> dict:
    fields = dict(HIDDEN_RE.findall(html))
    fields.update({"ddlKey": "school_name", "txtKeyNameS": "", "ddlCityS": "", "ddlAreaS": ""})
    return fields


def page_rows(html: str) -> list[tuple[str, str, str]]:
    """回傳當頁 [(縣市, 園名, 詳情token)]，以列索引配對。"""
    names = dict(NAME_RE.findall(html))
    cities = dict(CITY_RE.findall(html))
    tokens = {idx: tok for tok, idx in TOKEN_RE.findall(html)}
    rows = []
    for idx, name in names.items():
        if idx not in tokens:
            log(f"警告：列 {idx}（{name}）找不到詳情 token，略過")
            continue
        rows.append((cities.get(idx, ""), name.strip(), tokens[idx]))
    return rows


def crawl(data_dir: Path, delay: float, limit: int | None, new_only: bool = False) -> int:
    """new_only：列表全掃，但詳情頁已落地者不重抓（每日增量模式；
    既有園所的新裁罰由每週全量模式補上）。"""
    raw = data_dir / "raw" / "ece"
    f = Fetcher(delay=delay, verify_tls=False)

    html = f.fetch(SEARCH_URL)
    if html is None:
        log("無法連上教保網，中止")
        return 1

    # 送出全國空關鍵字查詢
    html = f.fetch(SEARCH_URL, data={**form_fields(html), "btnSearch": "搜尋"})
    if html is None:
        log("查詢送出失敗，中止")
        return 1

    seen: set[tuple[str, str]] = set()
    used_names: set[str] = set()
    page = 1
    changed = 0
    total = 0
    while True:
        rows = [r for r in page_rows(html) if (r[0], r[1]) not in seen]
        if not rows and page > 1:
            break
        save(raw / "list" / f"p{page:04d}.html", strip_session_noise(html))
        for city, name, token in rows:
            seen.add((city, name))
            # 同名園所（極少數）依出現順序加序號，避免覆蓋
            fname = safe_filename(f"{city}_{name}")
            n = 2
            while fname in used_names:
                fname = safe_filename(f"{city}_{name}_{n}")
                n += 1
            used_names.add(fname)
            if new_only and (raw / "detail" / f"{fname}.html").exists():
                continue
            detail = f.fetch(DETAIL_URL.format(token=token), headers={"Referer": SEARCH_URL})
            if detail is None:
                continue
            if save(raw / "detail" / f"{fname}.html", strip_session_noise(detail)):
                changed += 1
            total += 1
            if limit is not None and total >= limit:
                log(f"達到 --limit {limit}，提前結束")
                log(f"完成：{page} 頁、詳情 {total} 筆（{changed} 筆有異動）、失敗 {len(f.failures)} 筆")
                return 2 if f.failures else 0
        log(f"列表第 {page} 頁：{len(rows)} 園（累計 {len(seen)}）")

        if NEXT_PAGE not in html:
            break
        html = f.fetch(SEARCH_URL, data={
            **form_fields(html), "__EVENTTARGET": NEXT_PAGE, "__EVENTARGUMENT": "",
        })
        if html is None:
            break
        page += 1

    log(f"完成：{page} 頁、詳情 {total} 筆（{changed} 筆有異動）、失敗 {len(f.failures)} 筆")
    return 2 if f.failures else 0


if __name__ == "__main__":
    opts = parse_args(sys.argv[1:])
    sys.exit(crawl(opts["data_dir"], opts["delay"], opts["limit"], opts["new_only"]))
