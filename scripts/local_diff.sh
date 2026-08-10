#!/bin/bash
# 解析＋diff 事件產出：排程進入點
#
# 兩軌備份（Actions 05:30 社家署、old-mini 06:30 教保網）都完成後執行。
#
# **這一軌已於 2026-08-10 搬到 GitHub Actions（.github/workflows/diff.yml）。**
# 本檔保留供離線手動重跑，systemd 單元已刪除——排程若在兩處同時跑，會產生
# 改 penalties.json 的競態。手動跑之前請先確認當天 Actions 那邊沒有在跑。
#
# diff.py 的安全機制：解析有任何失敗、或單次 removed 異常量，都會
# exit 1 且不寫檔——此時本腳本不 commit，異常留在 log 裡等人工處理。
#
# 路徑約定與 local_backup.sh 相同：程式 repo 由腳本自身推得，
# 資料 repo 預設同層 preschool-resume-data，可用 DATA_DIR 覆蓋。

set -u
CODE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${DATA_DIR:-$(dirname "$CODE_DIR")/preschool-resume-data}"
LOG="${LOG:-$HOME/preschool-diff.log}"

exec >> "$LOG" 2>&1

# 同 local_backup.sh：先 pull 程式碼，再以新版腳本重啟自己，
# 否則改動當天這一輪跑的還是舊 inode 上的舊內容。
if [ "${PRESCHOOL_RELAUNCHED:-}" != "1" ]; then
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') 開始 ====="
  git -C "$CODE_DIR" pull --ff-only --quiet || echo "警告：code pull 失敗，用現有版本"
  export PRESCHOOL_RELAUNCHED=1
  exec bash "$0" "$@"
fi

# 取最新快照（今晨兩軌的 commit 都應已進來）
git -C "$DATA_DIR" pull --rebase --quiet || echo "警告：data pull 失敗，用本地版本"

python3 "$CODE_DIR/crawler/diff.py" --data-dir "$DATA_DIR"
RC=$?

# 心跳檔一律寫入並推送（含失敗的 exit code），供 watchdog 檢查；
# diff 產出（data/、events/）只在成功時 commit
cd "$DATA_DIR" || exit 1
mkdir -p status
printf '{"date": "%s", "exit": %d}\n' "$(TZ=Asia/Taipei date +%F)" "$RC" > status/diff.json

if [ $RC -ne 0 ]; then
  echo "diff 引擎異常（exit=${RC}），只推心跳不 commit 產出"
  git add status
else
  git add data events status
fi

if git diff --cached --quiet; then
  echo "無任何變更（同日重跑）"
elif git diff --cached --quiet -- . ':!status'; then
  git commit -q -m "心跳 $(TZ=Asia/Taipei date +%F)（diff exit=${RC}）"
  git pull --rebase --quiet && git push -q || echo "錯誤：push 失敗"
else
  git commit -q -m "解析 $(date +%Y-%m-%d)：penalties 現況與事件"
  git pull --rebase --quiet && git push -q || echo "錯誤：push 失敗"
fi

echo "===== 結束 diff exit=$RC ====="
exit $RC
