"""社家署違反兒少法裁罰專區（含托嬰中心）備份爬蟲。

站點形態：伺服器端渲染，列表為 GET 參數分頁，每筆有 /ChildYoungLaw/Detail/{id} 詳情頁。
快照落地：raw/sfaa/list/pNNNN.html、raw/sfaa/detail/{id}.html（穩定路徑，git 歷史即時間軸）。
"""

import re
import sys
import urllib.parse
from pathlib import Path

from common import Fetcher, log, parse_args, prune_stale_lists, save

BASE = "https://crc.sfaa.gov.tw"
LIST_URL = (
    BASE + "/ChildYoungLaw/Sanction"
    "?page={page}&pagesize={pagesize}&name=&target=&city=0&startDate=&endDate=&dosearch=true"
)
PAGESIZE = 100
# 詳情頁（含公告 PDF）失敗幾筆以上才算整軌失敗。站方偶發 Connection refused，
# 2,000 筆裡重試後仍漏 1 筆、隔日即補回（2026-09-26、10-17 各一次），不值得
# 讓 watchdog 報紅。列表頁不適用：一頁就是 100 筆，列表沒走完一律失敗。
DETAIL_FAIL_LIMIT = 3

DETAIL_RE = re.compile(r'href="/ChildYoungLaw/Detail/(\d+)"')
FILE_RE = re.compile(r'href="(/UploadFile/Sanction/([^"]+))"')


def download_files(f: Fetcher, raw: Path, html: str) -> None:
    """下載詳情頁引用的公告檔案（正式公文 PDF）：內容不會變動，已存在即跳過。"""
    for href, fname in FILE_RE.findall(html):
        dest = raw / "files" / urllib.parse.unquote(fname)
        if dest.exists():
            continue
        blob = f.fetch_bytes(BASE + href)
        if blob is not None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(blob)


def files_only(data_dir: Path, delay: float) -> int:
    """從已落地的詳情快照掃描公告檔案連結，補下載缺少的 PDF。"""
    raw = data_dir / "raw" / "sfaa"
    f = Fetcher(delay=delay, verify_tls=False)
    pages = sorted((raw / "detail").glob("*.html"))
    for n, p in enumerate(pages, 1):
        download_files(f, raw, p.read_text(encoding="utf-8"))
        if n % 100 == 0:
            log(f"掃描 {n}/{len(pages)}")
    log(f"完成：掃描 {len(pages)} 頁、失敗 {len(f.failures)} 筆")
    return 2 if f.failures else 0


def crawl(data_dir: Path, delay: float, limit: int | None, new_only: bool = False) -> int:
    """new_only：列表全掃，但詳情頁已落地者不重抓（每日增量模式；
    既有紀錄的內容變更由每週全量模式補上）。"""
    raw = data_dir / "raw" / "sfaa"
    # 站方 CA 憑證缺 Subject Key Identifier，過不了 OpenSSL 3.x 嚴格驗證
    f = Fetcher(delay=delay, verify_tls=False)

    # 首次請求經 AspxAutoDetectCookieSupport 轉址取得 cookie
    if f.fetch(BASE + "/ChildYoungLaw/Sanction") is None:
        log("無法連上社家署站點，中止")
        return 1

    # 逐頁抓列表，收集詳情 id
    ids: list[str] = []
    page = 1
    complete = False
    while True:
        html = f.fetch(LIST_URL.format(page=page, pagesize=PAGESIZE))
        if html is None:
            break
        found = DETAIL_RE.findall(html)
        new = [i for i in found if i not in ids]
        if not new:
            complete = True
            break
        ids.extend(new)
        save(raw / "list" / f"p{page:04d}.html", html)
        log(f"列表第 {page} 頁：{len(new)} 筆（累計 {len(ids)}）")
        page += 1
    if complete:
        # 列表完整走完才整理；中途失敗時保留舊快照以免誤刪
        prune_stale_lists(raw / "list", page - 1)

    # 逐筆抓詳情
    if new_only:
        ids = [i for i in ids if not (raw / "detail" / f"{i}.html").exists()]
    if limit is not None:
        ids = ids[:limit]
    changed = 0
    for n, pid in enumerate(ids, 1):
        html = f.fetch(f"{BASE}/ChildYoungLaw/Detail/{pid}")
        if html is None:
            continue
        if save(raw / "detail" / f"{pid}.html", html):
            changed += 1
        download_files(f, raw, html)
        if n % 50 == 0:
            log(f"詳情 {n}/{len(ids)}")

    log(f"完成：列表 {page - 1} 頁、詳情 {len(ids)} 筆（{changed} 筆有異動）、失敗 {len(f.failures)} 筆")
    if not complete:
        log("列表未完整走完，視為失敗")
        return 2
    if len(f.failures) >= DETAIL_FAIL_LIMIT:
        return 2
    if f.failures:
        # 未達門檻：不報紅，但留在 log 裡；漏抓的詳情隔日增量模式會再抓
        log(f"詳情失敗 {len(f.failures)} 筆，未達 {DETAIL_FAIL_LIMIT} 筆門檻，不視為失敗：{f.failures}")
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    mode_files_only = "--files-only" in argv
    if mode_files_only:
        argv = [a for a in argv if a != "--files-only"]
    opts = parse_args(argv)
    if mode_files_only:
        sys.exit(files_only(opts["data_dir"], opts["delay"]))
    sys.exit(crawl(opts["data_dir"], opts["delay"], opts["limit"], opts["new_only"]))
