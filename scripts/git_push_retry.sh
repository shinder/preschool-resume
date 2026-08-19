#!/bin/bash
# 帶重試的 git push：多軌排程共寫同一個資料 repo，就算先 rebase 再推
# （2026-08-15 引入），rebase 到 push 之間仍有幾秒的競爭窗口——另一軌恰好
# 在這期間推了 commit，push 還是會被拒（non-fast-forward）。本腳本把這個
# 殘餘窗口收掉：被拒就再 rebase 再推，最多三次，間隔遞增以錯開對方的重試。
#
# 用法：在「已 commit、待 push」的 repo 目錄下執行。成功 exit 0，否則 exit 1。
# 呼叫端：backup.yml、diff.yml、site.yml、scripts/local_*.sh。

set -u
ATTEMPTS=3
for i in $(seq 1 $ATTEMPTS); do
  if git pull --rebase --quiet && git push -q; then
    [ "$i" -gt 1 ] && echo "push 第 $i 次成功"
    exit 0
  fi
  if [ "$i" -lt "$ATTEMPTS" ]; then
    echo "push 第 $i 次失敗，$((i * 5)) 秒後重試"
    sleep $((i * 5))
  fi
done
echo "錯誤：push 連續 $ATTEMPTS 次失敗"
exit 1
