#!/bin/bash
# 教保網（幼兒園）裁罰備份：排程進入點
#
# 教保網封鎖境外 IP，GitHub Actions 跑不了，由台灣 IP 的機器執行本腳本
# （目前部署在 old-mini 的 systemd timer，每日 06:30，見 scripts/systemd/）。
# 平日增量（--new-only）、週六全量，與 Actions 端（社家署）同一套約定。
#
# 路徑：程式 repo 位置由腳本自身推得；資料 repo 預設為同層的
# preschool-resume-data，可用環境變數 DATA_DIR 覆蓋。

set -u
CODE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${DATA_DIR:-$(dirname "$CODE_DIR")/preschool-resume-data}"
LOG="${LOG:-$HOME/preschool-backup.log}"

exec >> "$LOG" 2>&1
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 開始 ====="

# 更新爬蟲程式碼與資料 repo（Actions 端的社家署 commit 可能先進來了）
git -C "$CODE_DIR" pull --ff-only --quiet || echo "警告：code pull 失敗，用現有版本"
git -C "$DATA_DIR" pull --rebase --quiet || echo "警告：data pull 失敗，用本地版本"

MODE_FLAG="--new-only"
MODE_NAME="new-only"
if [ "$(date +%u)" = "6" ]; then
  MODE_FLAG=""
  MODE_NAME="full"
fi

python3 "$CODE_DIR/crawler/ece_moe.py" --data-dir "$DATA_DIR" $MODE_FLAG
ECE=$?

cd "$DATA_DIR" || exit 1
mkdir -p status
printf '{"date": "%s", "exit": %d}\n' "$(TZ=Asia/Taipei date +%F)" "$ECE" > status/ece_backup.json
git add -A
if git diff --cached --quiet; then
  echo "無任何變更（同日重跑）"
elif git diff --cached --quiet -- . ':!status'; then
  git commit -q -m "心跳 $(TZ=Asia/Taipei date +%F)（ece）"
  git pull --rebase --quiet && git push -q || echo "錯誤：push 失敗"
else
  git commit -q -m "備份 $(date +%Y-%m-%d)（ece ${MODE_NAME}）"
  git pull --rebase --quiet && git push -q || echo "錯誤：push 失敗"
fi

echo "===== 結束 ece exit=$ECE ====="
exit $ECE
