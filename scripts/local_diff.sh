#!/bin/bash
# 解析＋diff 事件產出：排程進入點
#
# 兩軌備份（Actions 05:30 社家署、old-mini 06:30 教保網）都完成後執行，
# 單點跑在 old-mini（每日 08:30，見 scripts/systemd/preschool-diff.*），
# 避免兩處同時改 penalties.json 的競態。
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
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 開始 ====="

# 取最新程式碼與快照（今晨兩軌的 commit 都應已進來）
git -C "$CODE_DIR" pull --ff-only --quiet || echo "警告：code pull 失敗，用現有版本"
git -C "$DATA_DIR" pull --rebase --quiet || echo "警告：data pull 失敗，用本地版本"

python3 "$CODE_DIR/crawler/diff.py" --data-dir "$DATA_DIR"
RC=$?
if [ $RC -ne 0 ]; then
  echo "diff 引擎異常（exit=$RC），不 commit"
  echo "===== 結束 diff exit=$RC ====="
  exit $RC
fi

cd "$DATA_DIR" || exit 1
git add data events
if git diff --cached --quiet; then
  echo "本次無事件"
else
  git commit -q -m "解析 $(date +%Y-%m-%d)：penalties 現況與事件"
  git pull --rebase --quiet && git push -q || echo "錯誤：push 失敗"
fi

echo "===== 結束 diff exit=0 ====="
