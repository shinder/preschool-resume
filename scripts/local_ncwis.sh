#!/bin/bash
# 托嬰中心主檔週更：排程進入點（部署於 old-mini，見 scripts/systemd/preschool-ncwis.*）
#
# 為什麼要重抓整份：缺額＝核定收托−實際收托，而**實際收托只在詳情頁**，
# 查詢結果頁只有核定收托。所以刷新缺額就得重抓 1,979 頁詳情，約 80 分鐘
# （2026-08-10 實測 79 分 50 秒）。
#
# 為什麼是週六 04:30：
#   - 托育中心平日回報收托數，週六跑等於收齊整週（含週五）的變動；跑週五會漏掉週五。
#   - 家長找空位多半在週末，週六清晨更新正好讓週末看到最新數字。排週一最糟——
#     整個週末顯示的都是一週前的資料。
#   - 04:30 起跑約 05:50 結束，趕在 06:30 的教保網備份之前（同一台機器），
#     也趕得上當天 09:00 的建站。
#
# 不排日更：站方那個數字未必天天變，對政府網站每日發 2,000 次請求換不到新資訊。
# 頻率是否合適可事後量：raw 快照都在 git 裡，跑第二次後 `git diff` 就知道
# 有多少家真的改了，再據以調整。
#
# 此站（ncwisweb）與教保網是不同站台，不會與 06:30 那軌搶 session。

set -u
CODE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${DATA_DIR:-$(dirname "$CODE_DIR")/preschool-resume-data}"
LOG="${LOG:-$HOME/preschool-ncwis.log}"

exec >> "$LOG" 2>&1

# 同 local_backup.sh：先 pull 再以新版腳本重啟自己，否則改動要延後一輪才生效
# （git checkout 是寫暫存檔再 rename，bash 開著的 fd 還指向舊 inode）
if [ "${PRESCHOOL_RELAUNCHED:-}" != "1" ]; then
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') 開始 ====="
  git -C "$CODE_DIR" pull --ff-only --quiet || echo "警告：code pull 失敗，用現有版本"
  export PRESCHOOL_RELAUNCHED=1
  exec bash "$0" "$@"
fi

git -C "$DATA_DIR" pull --rebase --quiet || echo "警告：data pull 失敗，用本地版本"

python3 "$CODE_DIR/crawler/ncwis_centers.py" --data-dir "$DATA_DIR"
RC=$?

cd "$DATA_DIR" || exit 1
mkdir -p status
printf '{"date": "%s", "exit": %d}\n' "$(TZ=Asia/Taipei date +%F)" "$RC" > status/ncwis.json
git add raw/ncwis data/ncwis status
if git diff --cached --quiet; then
  echo "無任何變更（同日重跑）"
elif git diff --cached --quiet -- . ':!status'; then
  git commit -q -m "心跳 $(TZ=Asia/Taipei date +%F)（ncwis exit=${RC}）"
  git pull --rebase --quiet && git push -q || echo "錯誤：push 失敗"
else
  git commit -q -m "托嬰主檔週更 $(TZ=Asia/Taipei date +%F)（exit=${RC}）"
  git pull --rebase --quiet && git push -q || echo "錯誤：push 失敗"
fi

echo "===== 結束 ncwis exit=${RC} ====="
exit $RC
