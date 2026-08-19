#!/bin/bash
# 幼兒園主檔月更：排程進入點（部署於 old-mini，見 scripts/systemd/preschool-ece-schools.*）
#
# 每月 1 號 01:00。模式依月份自動決定：
#   1／4／7／10 月：全量（約 4 小時 15 分）——順便校正座標
#   其餘月份：--new-only（約 30 分鐘）
#
# 為什麼增量幾乎不損失東西：2026-08-10 實測全量 4 小時 14 分，其中列表頁只佔
# 約 23 分（699 頁），**其餘三小時五十分全花在每園一次的 showmap 座標請求**。
# 而 --new-only 只跳過「已有座標者」的 showmap，**列表頁照樣全抓、全部重新解析**，
# 所以園名、立案文號、核定人數、準公共、評鑑歷史、新園與停辦都會更新。
# 座標不會移動，季更校正一次綽綽有餘。
#
# 為什麼是 01:00：**教保網不接受同 IP 併發 session**（2026-08-10 實測，另開連線
# 四次全 500），所以必須完全避開每日 06:30–07:30 的教保網備份，否則備份會拿到
# 500、心跳變紅。01:00 起跑，全量最晚 05:15 結束、增量 01:30 結束，兩者都留著
# 一小時以上的餘裕；當天 09:00 建站也吃得到。
#
# **日後若把月費抓取併進來（每園多一個請求、約 +4 小時），這個窗口就不夠了**，
# 得重新規劃——可能只在季更的全量跑月費，並改到備份結束後的 08:00 起跑，
# 代價是當天的建站吃不到、隔天才會上站。

set -u
CODE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${DATA_DIR:-$(dirname "$CODE_DIR")/preschool-resume-data}"
LOG="${LOG:-$HOME/preschool-ece-schools.log}"

# 同 local_backup.sh：log 超過 10MB 輪替成 .old，只在第一次啟動時做
if [ "${PRESCHOOL_RELAUNCHED:-}" != "1" ] && [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 10485760 ]; then
  mv "$LOG" "$LOG.old"
fi

exec >> "$LOG" 2>&1

# 同 local_backup.sh：先 pull 再以新版腳本重啟自己，否則改動要延後一輪才生效
if [ "${PRESCHOOL_RELAUNCHED:-}" != "1" ]; then
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') 開始 ====="
  git -C "$CODE_DIR" pull --ff-only --quiet || echo "警告：code pull 失敗，用現有版本"
  export PRESCHOOL_RELAUNCHED=1
  exec bash "$0" "$@"
fi

git -C "$DATA_DIR" pull --rebase --quiet || echo "警告：data pull 失敗，用本地版本"

case "$(TZ=Asia/Taipei date +%m)" in
  01|04|07|10) MODE_FLAG=""; MODE_NAME="全量" ;;
  *)           MODE_FLAG="--new-only"; MODE_NAME="增量" ;;
esac
echo "模式：${MODE_NAME}"

python3 "$CODE_DIR/crawler/ece_schools.py" --data-dir "$DATA_DIR" $MODE_FLAG
RC=$?

cd "$DATA_DIR" || exit 1
mkdir -p status
printf '{"date": "%s", "exit": %d}\n' "$(TZ=Asia/Taipei date +%F)" "$RC" > status/ece_schools.json
git add raw/ece_schools data/ece/schools.json status
if git diff --cached --quiet; then
  echo "無任何變更（同日重跑）"
elif git diff --cached --quiet -- . ':!status'; then
  git commit -q -m "心跳 $(TZ=Asia/Taipei date +%F)（ece_schools exit=${RC}）"
  bash "$CODE_DIR/scripts/git_push_retry.sh"
else
  git commit -q -m "幼兒園主檔${MODE_NAME}更新 $(TZ=Asia/Taipei date +%F)（exit=${RC}）"
  bash "$CODE_DIR/scripts/git_push_retry.sh"
fi

echo "===== 結束 ece_schools exit=${RC} ====="
exit $RC
