#!/bin/bash
# 教保網（幼兒園）裁罰備份：排程進入點
#
# 教保網封鎖境外 IP，GitHub Actions 跑不了，由台灣 IP 的機器執行本腳本
# （目前部署在 old-mini 的 systemd timer，每日 06:30，見 scripts/systemd/）。
#
# **每日全量**（2026-08-09 起）。原本比照 Actions 端採平日增量、週六全量，
# 但兩者的列表語意不同：社家署每筆裁罰有自己的 Detail id，新裁罰當天就會出現
# 在列表上；教保網的列表是「有裁罰紀錄的園所」，已在列表上的園再被罰時，
# 新紀錄只存在於該園的詳情頁，增量模式不會重抓——最慢要等到週六全量，
# 延遲七天。實測 2,418 家受罰機構有 47% 不只被罰一次、再犯間隔下四分位僅
# 48 天，這個延遲會讓第三階段的推播失去意義。
#
# 當初採增量是為了 Actions 每月 2,000 分鐘的免費額度，但這一軌搬到 old-mini
# 之後不再消耗額度，代價只剩本機約一小時的機器時間，因此取消增量模式。
#
# 路徑：程式 repo 位置由腳本自身推得；資料 repo 預設為同層的
# preschool-resume-data，可用環境變數 DATA_DIR 覆蓋。

set -u
CODE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${DATA_DIR:-$(dirname "$CODE_DIR")/preschool-resume-data}"
LOG="${LOG:-$HOME/preschool-backup.log}"

exec >> "$LOG" 2>&1

# 本腳本會 pull 自己所在的 repo，所以必須先 pull、再用新版重新啟動自己。
# 不重啟的話，改動當天那一輪跑的仍是舊腳本：git checkout 是寫暫存檔再 rename，
# bash 開著的 fd 還指向舊 inode。實測 faaca31（改每日全量）在 2026-08-10 那輪
# 沒生效，commit 訊息露了餡——「備份 2026-08-10（ece new-only）」、只跑 3 分鐘。
if [ "${PRESCHOOL_RELAUNCHED:-}" != "1" ]; then
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') 開始 ====="
  git -C "$CODE_DIR" pull --ff-only --quiet || echo "警告：code pull 失敗，用現有版本"
  export PRESCHOOL_RELAUNCHED=1
  exec bash "$0" "$@"
fi

git -C "$DATA_DIR" pull --rebase --quiet || echo "警告：data pull 失敗，用本地版本"

python3 "$CODE_DIR/crawler/ece_moe.py" --data-dir "$DATA_DIR"
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
  git commit -q -m "備份 $(date +%Y-%m-%d)（ece 全量）"
  git pull --rebase --quiet && git push -q || echo "錯誤：push 失敗"
fi

echo "===== 結束 ece exit=$ECE ====="
exit $ECE
