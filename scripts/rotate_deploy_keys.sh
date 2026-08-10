#!/bin/bash
# 重新產生兩把 GitHub deploy key 並登錄上去。
#
# 為什麼需要：排程機（目前是 old-mini）用兩把 deploy key 存取 GitHub，私鑰
# **只存在那台機器的 ~/.ssh/ 裡**，沒有備份也不該進 repo。機器若燒毀而非關機，
# 私鑰就跟著沒了。
#
# 與其要求「記得備份私鑰」（做不到就整條復原路徑斷掉），不如讓金鑰隨時可以
# 重新產生——反正 deploy key 的權威在 GitHub 那邊，不是在檔案裡。所以換機流程是：
#
#     1. bash scripts/rotate_deploy_keys.sh     ← 產生新金鑰並登錄
#     2. bash scripts/setup_runner.sh           ← 佈署排程
#
# 這兩支都不需要舊機器還活著，只需要一個已登入的 gh CLI。
#
# 產出兩把（分開是為了讓程式 repo 維持唯讀，降低誤推的風險）：
#   ~/.ssh/gh_preschool_code   程式 repo   read-only
#   ~/.ssh/gh_preschool_data   資料 repo   可寫（爬蟲要 push 快照與心跳）
#
# 用法：bash scripts/rotate_deploy_keys.sh [--label 名稱] [--revoke-others] [--dry-run]
#   --label           寫進 deploy key 標題，預設為本機 hostname
#   --revoke-others   刪掉兩個 repo 上其餘的 deploy key（舊機器遺失時用來撤銷）。
#                     actions-backup 會自動保留——那是 workflow 在用的，刪了三條管線全斷。
#
# 無密碼保護是刻意的：排程要無人值守執行，加了 passphrase 就得掛 ssh-agent。
# 代價由「金鑰只有單一 repo 的權限、可隨時撤銷重發」承擔。

set -euo pipefail

CODE_REPO="shinder/preschool-resume"
DATA_REPO="shinder/preschool-resume-data"
PREFIX="preschool-runner"

LABEL="$(hostname -s 2>/dev/null || echo runner)"
REVOKE=false
DRY_RUN=false
while [ $# -gt 0 ]; do
  case "$1" in
    --label) LABEL="$2"; shift 2 ;;
    --revoke-others) REVOKE=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    *) echo "未知參數：$1" >&2; exit 2 ;;
  esac
done

say() { printf '\n== %s ==\n' "$1"; }
run() { if $DRY_RUN; then echo "[dry-run] $*"; else eval "$@"; fi; }
die() { echo "錯誤：$1" >&2; exit 1; }

say "檢查先決條件"
command -v ssh-keygen >/dev/null || die "缺 ssh-keygen"
command -v gh >/dev/null || die "缺 gh CLI：https://cli.github.com/"
gh auth status >/dev/null 2>&1 || die "gh 尚未登入：gh auth login（需要 admin:public_key 與 repo 權限）"
echo "gh 已登入，label=${LABEL}"

# repo, 金鑰檔名, 是否唯讀
TARGETS=(
  "$CODE_REPO gh_preschool_code true"
  "$DATA_REPO gh_preschool_data false"
)

say "現有的 deploy key"
for t in "${TARGETS[@]}"; do
  set -- $t
  echo "--- $1 ---"
  gh api "repos/$1/keys" --jq '.[] | "  id=\(.id)  \(.title)  read_only=\(.read_only)  建立於 \(.created_at)"' 2>/dev/null \
    || echo "  （讀不到，權限不足？）"
done

say "產生新金鑰並登錄"
for t in "${TARGETS[@]}"; do
  set -- $t
  repo="$1"; keyfile="$HOME/.ssh/$2"; readonly_flag="$3"
  title="${PREFIX}-${2#gh_preschool_}@${LABEL}"

  if [ -f "$keyfile" ]; then
    ts=$(date +%Y%m%d%H%M%S)
    echo "已存在 ${keyfile}，先備份為 ${keyfile}.${ts}.bak"
    run "mv '$keyfile' '${keyfile}.${ts}.bak'"
    [ -f "$keyfile.pub" ] && run "mv '$keyfile.pub' '${keyfile}.${ts}.bak.pub'"
  fi
  # 無密碼：排程需無人值守；-C 只是註解，方便日後在 GitHub 上認人
  run "ssh-keygen -q -t ed25519 -N '' -C '${title}' -f '$keyfile'"
  run "chmod 600 '$keyfile'"

  # 同名舊金鑰先刪，否則 GitHub 會以「key already in use」拒絕
  if ! $DRY_RUN; then
    old_id=$(gh api "repos/$repo/keys" --jq ".[] | select(.title==\"$title\") | .id" 2>/dev/null | head -1)
    [ -n "${old_id:-}" ] && { echo "移除同名舊金鑰 id=${old_id}"; gh api -X DELETE "repos/$repo/keys/$old_id"; }
    gh api "repos/$repo/keys" -f "title=$title" -f "key=$(cat "$keyfile.pub")" \
      -F "read_only=$readonly_flag" --jq '"已登錄 id=\(.id) \(.title) read_only=\(.read_only)"'
  else
    echo "[dry-run] gh api repos/$repo/keys -f title=$title -f key=<新公鑰> -F read_only=$readonly_flag"
  fi
done

say "設定 SSH host 別名"
SSH_CONF="$HOME/.ssh/config"
run "touch '$SSH_CONF'"; run "chmod 600 '$SSH_CONF'"
for pair in "github-code:gh_preschool_code" "github-data:gh_preschool_data"; do
  alias_name="${pair%%:*}"; key="${pair##*:}"
  if grep -q "^Host ${alias_name}\$" "$SSH_CONF" 2>/dev/null; then
    echo "${alias_name} 已存在，略過"
  else
    run "printf '\nHost %s\n  HostName github.com\n  User git\n  IdentityFile ~/.ssh/%s\n  IdentitiesOnly yes\n' '$alias_name' '$key' >> '$SSH_CONF'"
    echo "已加入 ${alias_name}"
  fi
done
run "ssh-keyscan -t rsa,ecdsa,ed25519 github.com 2>/dev/null >> '$HOME/.ssh/known_hosts'"

say "驗證"
if $DRY_RUN; then
  echo "[dry-run] 略過驗證"
else
  git ls-remote "github-code:${CODE_REPO}.git" HEAD >/dev/null \
    && echo "程式 repo 讀取正常" || die "程式 repo 連不上"
  git ls-remote "github-data:${DATA_REPO}.git" HEAD >/dev/null \
    && echo "資料 repo 讀取正常" || die "資料 repo 連不上"
  echo "（寫入權限要等實際 push 才驗得到；setup_runner.sh 的驗收步驟會跑一次真的備份）"
fi

if $REVOKE; then
  say "撤銷其他金鑰"
  # 不能只比對本腳本的命名前綴：現存的金鑰是手工建的 old-mini-ro／old-mini-rw，
  # 前綴對不上就撤銷不到，而那正是換機時最該撤的兩把。故改為「除了保護名單與
  # 本次新建的，其餘全刪」。
  # PROTECTED 一定要有 actions-backup——那是 secrets.DATA_REPO_SSH_KEY 對應的
  # 公鑰，刪掉會讓 backup／diff／site 三條 workflow 一起斷。
  PROTECTED="actions-backup"
  for t in "${TARGETS[@]}"; do
    set -- $t
    repo="$1"; kind="${2#gh_preschool_}"
    keep="${PREFIX}-${kind}@${LABEL}"
    gh api "repos/$repo/keys" --jq ".[] | \"\(.id)\t\(.title)\"" 2>/dev/null |
      while IFS=$'\t' read -r id title; do
        case "$title" in
          "$keep"|"$PROTECTED") echo "保留 ${repo}：$title"; continue ;;
        esac
        echo "刪除 ${repo}：$title (id=$id)"
        run "gh api -X DELETE 'repos/$repo/keys/$id'"
      done
  done
else
  echo
  echo "提示：舊金鑰仍然有效（目前有手工建的 old-mini-ro／old-mini-rw）。"
  echo "      機器遺失或報廢時加 --revoke-others 撤銷；actions-backup 會自動保留。"
fi

say "完成"
echo "私鑰在 ~/.ssh/gh_preschool_{code,data}，**不要 commit、不要複製到別處**。"
echo "下一步：bash scripts/setup_runner.sh"
