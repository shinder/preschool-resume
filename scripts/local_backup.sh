#!/bin/zsh
# 教保網（幼兒園）裁罰備份：本機每日排程
#
# 教保網封鎖境外 IP，GitHub Actions 跑不了，由這支腳本在本機執行。
# launchd 於每日 06:30 觸發（Mac 睡眠時順延到喚醒後補跑）。
# 平日增量（--new-only）、週六全量，與 Actions 端（社家署）同一套約定。

set -u
CODE_DIR="$HOME/practices/preschool-resume"
DATA_DIR="$HOME/practices/preschool-resume-data"
LOG="$HOME/Library/Logs/preschool-backup.log"

exec >> "$LOG" 2>&1
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 開始 ====="

# 同步遠端（Actions 端的社家署 commit 可能先進來了）
git -C "$DATA_DIR" pull --rebase --quiet || echo "警告：pull 失敗，繼續用本地版本"

MODE_FLAG="--new-only"
MODE_NAME="new-only"
if [ "$(date +%u)" = "6" ]; then
  MODE_FLAG=""
  MODE_NAME="full"
fi

python3 "$CODE_DIR/crawler/ece_moe.py" --data-dir "$DATA_DIR" $MODE_FLAG
ECE=$?

cd "$DATA_DIR" || exit 1
git add -A
if git diff --cached --quiet; then
  echo "本次無異動"
else
  git commit -q -m "備份 $(date +%Y-%m-%d)（ece ${MODE_NAME}）"
  git pull --rebase --quiet && git push -q || echo "錯誤：push 失敗"
fi

echo "===== 結束 ece exit=$ECE ====="
exit $ECE
