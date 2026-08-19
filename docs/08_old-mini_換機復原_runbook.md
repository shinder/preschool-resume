# old-mini 換機復原 runbook

台灣 IP 排程機（目前是家裡的 old-mini，2014 Mac mini／Ubuntu Server）跑三軌
「非台灣 IP 不可」的工作（教保網封鎖境外 IP）：

| 軌 | 排程 | 心跳檔 |
|---|---|---|
| 教保網裁罰備份 | 每日 06:30 | `status/ece_backup.json` |
| 托嬰中心主檔週更 | 週六 04:30 | `status/ncwis.json` |
| 幼兒園主檔月更 | 每月 1 號 01:00 | `status/ece_schools.json` |

策略是「不預先開備援機，但確保十分鐘能重建」。本文是那十分鐘的操作順序；
細節都在各腳本的註解裡，這裡只串流程。

## 怎麼知道它掛了

- watchdog（每日台北 14:00）報 `ece_backup 心跳過期`：機器離線或 timer 沒跑，
  當天就會知道。
- `ncwis`／`ece_schools` 心跳過期：低頻軌有 8／35 天容許期，報出來時
  可能已停擺一陣子。
- 三軌全部過期而 Actions 那三軌（sfaa_backup、diff、site）正常：幾乎可以
  確定是機器本身掛了，不是個別爬蟲的問題。

## 先試遠端救回（機器還開著的話）

```
ssh old-mini
systemctl list-timers 'preschool-*' --no-pager   # timer 還在嗎
tail -50 ~/preschool-backup.log                  # 最後一輪死在哪
sudo systemctl start preschool-backup.service    # 手動補跑一輪
```

補跑成功、心跳恢復當日，warning 隔天自動消。

## 機器救不回來：換機重建

任何一台台灣 IP 的 Linux 機器（家用網路或台灣 VPS 皆可，需 systemd 與
python3 3.10+）。**不需要舊機器還活著，也不需要任何備份檔**——deploy key
的權威在 GitHub，隨時可重發。

1. 新機器上裝好 `git`、`python3`、`gh`，並 `gh auth login`。
2. 先用 https 抓程式 repo（deploy key 還沒生出來）：
   `gh repo clone shinder/preschool-resume ~/preschool-resume`
3. 重發金鑰並登錄到 GitHub（舊機器遺失時加 `--revoke-others` 撤銷舊 key，
   它會自動保留 Actions 在用的那把）：
   `bash ~/preschool-resume/scripts/rotate_deploy_keys.sh --revoke-others`
4. 佈署（冪等，裝齊三軌的 systemd timer）：
   `bash ~/preschool-resume/scripts/setup_runner.sh`
5. 驗收照 setup_runner.sh 結尾印出的三步：手動跑一輪、確認心跳推上
   資料 repo、舊機器若還活著要停掉它的三個 timer。

隔天台北 14:00 的 watchdog 轉綠即復原完成。

## 日常維運備忘

- log 在 `~/preschool-{backup,ncwis,ece-schools}.log`，各腳本自帶輪替
  （超過 10MB 搬成 `.old`，只留一代），不必裝 logrotate。
- 腳本每輪開跑會先 `git pull` 程式 repo 再重啟自己，改排程腳本只要推上
  main，隔輪生效，不用登入機器。
- 改 systemd 單元檔（`scripts/systemd/`）則要重跑 `setup_runner.sh`
  （或手動 `install` ＋ `daemon-reload`），pull 不會自動套用。
- diff 那軌在 GitHub Actions，**不要**在這台機器裝 preschool-diff.timer，
  會兩邊打架。
