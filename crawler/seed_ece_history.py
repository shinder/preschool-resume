"""幼兒園墓碑名冊的一次性播種（比照 2.4 的 backfill_kiang.py）。

站方的基本資料查詢只列**現存**園所，但裁罰活得比園所久：已停辦或已改名的園
仍有裁罰要展示。實測只用現存名冊，ece 的裁罰↔機構對照率會從 97.6% 掉到
87.0%（898 筆接不上），低於 match.py 的 95% 門檻。

kiang 的 preschools.json 是累積不刪的（改名產生新 UUID、舊紀錄留著），正好
補得上這段歷史。本腳本把「只在 kiang 有、我方現存名冊沒有」的園寫成墓碑名冊
`data/ece/schools_history.json`，之後由 site/institutions.py 自行維護：每次
建置把消失的園加進去、復業的移出去，不再需要 kiang 的更新。

也就是說 kiang 從「每天要抓的上游」降為「凍結的歷史種子」。MIT 致謝見資料
repo 的 README。

**只需要跑一次。** 墓碑名冊已存在時預設拒絕覆寫（--force 可強制重播）。

用法：python3 crawler/seed_ece_history.py [--data-dir PATH] [--force] [--dry-run]
"""

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site"))

from common import log  # noqa: E402

import institutions as I  # noqa: E402


def kiang_to_school(p: dict, coords) -> dict:
    """kiang 的 properties → 我方 schools.json 的紀錄形狀。

    注意兩邊的文號欄位命名相反：kiang 的 reg_docno 才是我方的 reg_no
    （最初設立許可文號），kiang 的 reg_no 是我方的 reg_id（重發證書字號）。
    """
    lng, lat = (coords or [None, None])[:2]
    return {
        "name": p.get("title"), "city": p.get("city"), "town": p.get("town"),
        "org_type": p.get("type"), "phone": p.get("tel"),
        "reg_no": p.get("reg_docno"), "reg_date": p.get("reg_date"),
        "reg_id": p.get("reg_no"), "owner": p.get("owner"),
        "capacity_approved": p.get("count_approved"),
        "pre_public": p.get("pre_public"), "after_school": p.get("is_after"),
        "area_total": p.get("size"), "area_indoor": p.get("size_in"),
        "area_outdoor": p.get("size_out"), "floor": p.get("floor"),
        "shuttle": p.get("shuttle"), "url": p.get("url"), "evaluations": [],
        "postal_code": None, "address": p.get("address") or None,
        "lng": lng, "lat": lat,
    }


def main(argv: list[str]) -> int:
    def opt(name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default

    data_dir = Path(opt("--data-dir",
                        Path(__file__).resolve().parent.parent.parent
                        / "preschool-resume-data"))
    dry_run = "--dry-run" in argv
    out = I.history_path(data_dir)
    if out.exists() and "--force" not in argv:
        log(f"墓碑名冊已存在，不重播：{out}（要強制重播加 --force）")
        return 1

    snapshot = data_dir / "raw" / "kiang" / "preschools.json"
    if not snapshot.exists():
        log(f"缺少 kiang 快照：{snapshot}")
        return 1
    live, _ = I.load_schools(data_dir)
    if not live:
        log("缺少 data/ece/schools.json，先跑 crawler/ece_schools.py")
        return 1

    feats = json.loads(snapshot.read_text(encoding="utf-8"))["features"]
    issues: list = []

    # 用**基本雜湊**判斷是不是同一家園，不能用最終 id：撞號延伸會隨「同批資料裡
    # 有沒有撞號對象」而不同，兩邊各自延伸就對不起來。實測 kiang 的實永非營利
    # 幼兒園有兩筆只差全形半形括號，在 kiang 那批會撞號延伸、在站方現存名冊裡
    # 不會，用最終 id 比對會給營運中的園造出一個「已停辦」的分身。
    live_keys = {I.preschool_base_key(r) for r in live}
    candidates = [kiang_to_school(f["properties"], (f.get("geometry") or {}).get("coordinates"))
                  for f in feats]
    candidates = [c for c in candidates
                  if c.get("name") and I.preschool_base_key(c) not in live_keys]

    # 剩下的才走 build_preschool 配 id，主鍵規則與現存名冊一致
    tombstones = [dict(r, is_active=False, origin="kiang", last_seen=None)
                  for r in I.build_preschool(candidates, issues)]

    log(f"kiang {len(feats):,} 筆、現存名冊 {len(live_keys):,} 筆 → 墓碑 {len(tombstones):,} 筆")
    log(f"（其中有座標 {sum(1 for r in tombstones if r['lng'] is not None):,} 筆）")
    if dry_run:
        log("--dry-run：不寫檔")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"as_of": date.today().isoformat(),
                               "seeded_from": "kiang/ap.ece.moe.edu.tw docs/preschools.json (MIT)",
                               "records": sorted(tombstones, key=lambda r: r["id"])},
                              ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log(f"寫入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
