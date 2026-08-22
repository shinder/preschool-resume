# 幼兒園履歷（preschool-resume）

整合托嬰中心與幼兒園的立案、評鑑、收費與裁罰紀錄，建立「一間機構一份完整履歷」的資料產品，核心價值在裁罰公告的持續備份（官方到期即下架）、負責人歸戶（換牌重開偵測）與異動推播。

本專案源自 `~/practices/app2` 的開放資料題目評估（題目 5），評估過程與最終判定已整合至 `docs/`。

## 文件索引

| 文件 | 內容 |
|---|---|
| [docs/01_專案背景與定位.md](docs/01_專案背景與定位.md) | 題目來源、篩選標準、競品查核後的最終定位 |
| [docs/02_競品全景.md](docs/02_競品全景.md) | 官方平台、免費地圖、商業產品的完整盤點 |
| [docs/03_資料源與護城河.md](docs/03_資料源與護城河.md) | 資料源實況、「官方下架」護城河、法遵注意事項 |
| [docs/04_成本評估.md](docs/04_成本評估.md) | 技術、人力、時間與主機成本，前端形式判斷 |
| [docs/05_行動計畫.md](docs/05_行動計畫.md) | 動工前的前置驗證、第一步工作、觀察清單 |
| [docs/06_工程執行計劃.md](docs/06_工程執行計劃.md) | 資料源實測、技術選型、系統架構、里程碑與風險 |
| [docs/07_警示分級與負責人歸戶改善方向.md](docs/07_警示分級與負責人歸戶改善方向.md) | 夏莉絲案觸發的檢討：警示分級做法與負責人歸戶的個資紅線 |
| [docs/08_old-mini_換機復原_runbook.md](docs/08_old-mini_換機復原_runbook.md) | 排程主機 old-mini 的換機復原步驟 |
| [docs/09_公益開源評估.md](docs/09_公益開源評估.md) | 公益角度開源的利弊、程式碼與資料分開決策、前置工作與待決事項 |

## 授權與資料取得

- 本 repo 的程式碼與文件以 [MIT License](LICENSE) 釋出。
- **公開層資料 dump**：本 repo 的 [Releases（tag `public-data`）](../../releases/tag/public-data)每日隨建站更新四檔（institutions／penalties／aliases／meta）。本專案彙整部分以 CC BY 4.0 釋出；原始政府資料依「政府資料開放授權條款—第 1 版」使用並標示來源；歷史回補資料取自 kiang 的開放資料（MIT）。資料時點見 `meta.json` 的 `as_of`。
- **原始資料 repo（preschool-resume-data）維持私有**：它是私有層，含依站方決策不對外的自然人資料（自然人裁罰整筆、負責人姓名欄、公文原始快照）。公開釋出的資料一律經過公開層三道防線（白名單逐欄位重建、全欄位姓名遮罩、姓名稽核掃描），與站台呈現同一口徑，詳見[關於頁](https://preschool.shinder.work/about)與 docs/09。

## 開源與接手門檻

程式碼全部開源（含歸戶與監看模組——演算法公開受檢驗，個資紅線控管在資料的公開層防線與對外呈現決策，見 docs/07、docs/09）。fork 後要讓管線運轉，需要知道：

- **GitHub Actions 三軌（sfaa 備份、diff、site）＋watchdog**：fork 後補上 secrets（`DATA_REPO_SSH_KEY`、`CLOUDFLARE_API_TOKEN`、`CLOUDFLARE_ACCOUNT_ID`，選用 `WATCHLIST_ISSUE_TOKEN`）即可復活。
- **台灣 IP 三軌（ece 備份日更、ncwis 週更、ece_schools 月更）**：教保網封鎖境外 IP，這三軌**無法**跑在 GitHub Actions（其 runner 是美國 IP），須自備台灣 IP 的 Linux 機器，照 [docs/08 換機復原 runbook](docs/08_old-mini_換機復原_runbook.md) 約十分鐘重建。fork 不會讓這三軌自動復活，這是本專案可接手性的硬限制。

## 現況摘要

在五個候選題目中，本題經競品查核後被下修為最低優先：「裁罰查詢＋地圖」已是紅海（至少五個免費地圖加一個免費 app），C 端查詢收不到錢。若要做，合理形態是**資料層供應商**——持續備份、負責人歸戶、B 端 API——且動工前應先訪談 2–3 家潛在買家確認付費意願。唯一不必等待的行動是**立即開始備份裁罰公告**：歷史資料是時間累積型資產，越晚開始差距越大。
