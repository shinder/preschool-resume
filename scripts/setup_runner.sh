#!/bin/bash
# 台灣 IP 排程機的佈署腳本（換機／重建用）。
#
# 為什麼需要這支：教保網封鎖境外 IP，所以「抓 ap.ece.moe.edu.tw」這件事非得
# 從台灣的機器跑不可，GitHub Actions 代替不了。目前那台機器是家裡的 old-mini
# （2014 Mac mini，Ubuntu Server），它掛掉時幼兒園裁罰就停止更新。
#
# 常備一台備援 VPS 對每日一次的資料不划算，所以策略是「不預先開機器，但確保
# 十分鐘能重建」——這支腳本就是那十分鐘。冪等，可重複執行。
#
# **搬到新機器前要先準備好的東西（腳本做不到的部分）**：
#   1. 兩把 GitHub deploy key 的私鑰（~/.ssh/gh_preschool_code 與 gh_preschool_data）。
#      **不需要事先備份**——舊機器燒毀也沒關係，直接跑
#          bash scripts/rotate_deploy_keys.sh
#      重新產生並登錄到 GitHub（deploy key 的權威在 GitHub，不在檔案裡）。
#   2. 這台機器要有台灣 IP（家用網路或台灣 VPS 皆可）
#
# 用法：bash scripts/setup_runner.sh [--dry-run]
#
# 注意：解析＋diff 那一軌已於 2026-08-10 搬到 GitHub Actions（.github/workflows/diff.yml），
# 若在此安裝 preschool-diff.timer 會與 Actions 打架。
#
# 本腳本佈署台灣 IP 機器上的全部三軌（2026-08-19 前只佈署教保網備份，
# 換機會漏掉後兩軌，watchdog 要等 8／35 天的容許期過了才會發現）：
#   preschool-backup       每日 06:30  教保網裁罰備份
#   preschool-ncwis        週六 04:30  托嬰中心主檔週更
#   preschool-ece-schools  每月 1 號 01:00  幼兒園主檔月更

set -euo pipefail

DRY_RUN=false
[ "${1:-}" = "--dry-run" ] && DRY_RUN=true

CODE_REPO="github-code:shinder/preschool-resume.git"
DATA_REPO="github-data:shinder/preschool-resume-data.git"
CODE_DIR="$HOME/preschool-resume"
DATA_DIR="$HOME/preschool-resume-data"

say() { printf '\n== %s ==\n' "$1"; }
run() { if $DRY_RUN; then echo "[dry-run] $*"; else eval "$@"; fi; }
die() { echo "錯誤：$1" >&2; exit 1; }

say "檢查先決條件"
command -v git >/dev/null || die "缺 git：sudo apt install -y git"
command -v python3 >/dev/null || die "缺 python3"
PYV=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
case "$PYV" in
  3.1[0-9]|3.[2-9]*) echo "python3 $PYV" ;;
  *) die "python3 $PYV 太舊，爬蟲用到 3.10+ 的語法（如 X | None）" ;;
esac
for k in gh_preschool_code gh_preschool_data; do
  # ${k} 的大括號不能省：locale 非 UTF-8 時 bash 會把後面的中文位元組併進變數名
  [ -f "$HOME/.ssh/$k" ] || die "缺私鑰 ~/.ssh/${k}——先跑 bash scripts/rotate_deploy_keys.sh 產生並登錄"
  run "chmod 600 '$HOME/.ssh/$k'"
done

say "設定 SSH host 別名"
# 兩個 repo 用不同的 deploy key，同一台主機名無法靠 IdentityFile 區分，故立別名
SSH_CONF="$HOME/.ssh/config"
touch "$SSH_CONF"; chmod 600 "$SSH_CONF"
for pair in "github-code:gh_preschool_code" "github-data:gh_preschool_data"; do
  alias_name="${pair%%:*}"; key="${pair##*:}"
  if grep -q "^Host $alias_name\$" "$SSH_CONF"; then
    echo "$alias_name 已存在，略過"
  else
    run "printf '\nHost %s\n  HostName github.com\n  User git\n  IdentityFile ~/.ssh/%s\n  IdentitiesOnly yes\n' '$alias_name' '$key' >> '$SSH_CONF'"
    echo "已加入 $alias_name"
  fi
done
run "ssh-keyscan -t rsa,ecdsa,ed25519 github.com 2>/dev/null >> '$HOME/.ssh/known_hosts'"

say "取得 repo"
if [ -d "$CODE_DIR/.git" ]; then
  run "git -C '$CODE_DIR' pull --ff-only --quiet"
else
  run "git clone --quiet '$CODE_REPO' '$CODE_DIR'"
fi
if [ -d "$DATA_DIR/.git" ]; then
  run "git -C '$DATA_DIR' pull --rebase --quiet"
else
  # 資料 repo 含 300MB+ 的公告 PDF，只取最新一版，不要整段歷史
  run "git clone --quiet --depth 1 '$DATA_REPO' '$DATA_DIR'"
fi
if ! $DRY_RUN; then
  git -C "$DATA_DIR" config user.name  "$(git -C "$DATA_DIR" config user.name  || echo backup-bot)"
  git -C "$DATA_DIR" config user.email "$(git -C "$DATA_DIR" config user.email || echo shinder.lin@gmail.com)"
fi

say "安裝 systemd 單元（三軌；diff 已在 Actions）"
TIMERS=""
for name in preschool-backup preschool-ncwis preschool-ece-schools; do
  for ext in service timer; do
    run "sudo install -m 644 '$CODE_DIR/scripts/systemd/$name.$ext' /etc/systemd/system/$name.$ext"
  done
  TIMERS="$TIMERS $name.timer"
done
run "sudo systemctl daemon-reload"
run "sudo systemctl enable --now$TIMERS"

say "驗收"
if $DRY_RUN; then
  echo "[dry-run] 略過驗收"
else
  systemctl list-timers 'preschool-*' --no-pager
  echo
  echo "下一步："
  echo "  1. 手動跑一次確認能連上教保網並推得上去："
  echo "       bash $CODE_DIR/scripts/local_backup.sh && tail -5 ~/preschool-backup.log"
  echo "  2. 確認心跳有進資料 repo：git -C $DATA_DIR show --stat HEAD"
  echo "  3. 舊機器若還活著，把它的三個 timer 都停掉，避免兩台同時抓："
  echo "       sudo systemctl disable --now preschool-backup.timer preschool-ncwis.timer preschool-ece-schools.timer"
fi
