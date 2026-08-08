"""爬蟲共用工具：HTTP session、禮貌延遲、重試、快照落地。

只用標準函式庫，本機與 GitHub Actions 皆零依賴。
"""

import http.cookiejar
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = (
    "Mozilla/5.0 (compatible; preschool-resume-archiver/1.0; "
    "+https://github.com/shinder/preschool-resume; contact: shinder.lin@gmail.com)"
)

DEFAULT_DELAY = 2.0
RETRY_BACKOFF = [5, 15, 45]


class Fetcher:
    """帶 cookie session 的抓取器，內建禮貌延遲與重試。"""

    def __init__(self, delay: float = DEFAULT_DELAY, verify_tls: bool = True):
        self.delay = delay
        self.cookiejar = http.cookiejar.CookieJar()
        ctx = ssl.create_default_context()
        if not verify_tls:
            # 全國教保資訊網憑證鏈不完整，無法通過一般驗證
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ctx),
            urllib.request.HTTPCookieProcessor(self.cookiejar),
        )
        self._last_request_at = 0.0
        self.failures: list[str] = []

    def _wait(self):
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)

    def fetch_bytes(self, url: str, data: dict | None = None, timeout: int = 60,
                    headers: dict | None = None) -> bytes | None:
        """GET（data=None）或表單 POST。回傳原始 bytes；重試耗盡回傳 None 並記錄。"""
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        for attempt, backoff in enumerate([*RETRY_BACKOFF, None]):
            self._wait()
            self._last_request_at = time.monotonic()
            try:
                req = urllib.request.Request(url, body, {"User-Agent": USER_AGENT, **(headers or {})})
                with self.opener.open(req, timeout=timeout) as resp:
                    return resp.read()
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if backoff is None:
                    log(f"放棄：{url}（{e}）")
                    self.failures.append(url)
                    return None
                log(f"重試 {attempt + 1}：{url}（{e}），{backoff}s 後")
                time.sleep(backoff)
        return None

    def fetch(self, url: str, data: dict | None = None, timeout: int = 60,
              headers: dict | None = None) -> str | None:
        """同 fetch_bytes，但解碼為文字。"""
        raw = self.fetch_bytes(url, data, timeout, headers)
        return raw.decode("utf-8", errors="replace") if raw is not None else None


def strip_session_noise(html: str) -> str:
    """剝除 ASP.NET session 雜訊，避免每日快照產生無意義 diff。

    - VIEWSTATE 等 hidden 欄位值
    - punish_view.aspx 的 sch= 加密 token（綁 session，每次連線都不同；
      快照身分由穩定檔名承載，token 無資料價值）
    """
    html = re.sub(
        r'(id="(?:__VIEWSTATE|__VIEWSTATEGENERATOR|__EVENTVALIDATION)") value="[^"]*"',
        r"\1",
        html,
    )
    return re.sub(r"(punish_view\.aspx\?sch=)[A-Za-z0-9+/=%]*", r"\1", html)


def save(path: Path, html: str) -> bool:
    """寫入 HTML 快照；內容與現存檔案相同時不動檔案。回傳是否有寫入。

    快照規格：行尾統一為 LF。伺服器回應是 CRLF，統一後 git diff 乾淨，
    也不受任何工具的隱式行尾轉換影響。二進位檔（PDF）不走此函式。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = html.replace("\r\n", "\n").encode("utf-8")
    if path.exists() and path.read_bytes() == data:
        return False
    path.write_bytes(data)
    return True


def safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", name.strip())


def log(msg: str):
    print(msg, flush=True)


def parse_args(argv: list[str]) -> dict:
    """極簡參數解析：--data-dir PATH --delay N --limit N"""
    opts = {"data_dir": Path(__file__).resolve().parent.parent.parent / "preschool-resume-data",
            "delay": DEFAULT_DELAY, "limit": None, "new_only": False}
    it = iter(argv)
    for arg in it:
        if arg == "--data-dir":
            opts["data_dir"] = Path(next(it))
        elif arg == "--delay":
            opts["delay"] = float(next(it))
        elif arg == "--limit":
            opts["limit"] = int(next(it))
        elif arg == "--new-only":
            opts["new_only"] = True
        else:
            log(f"未知參數：{arg}")
            sys.exit(2)
    return opts
