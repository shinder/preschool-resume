/* 全台機構地圖：NLSC EMAP16 底圖（無等高線及門牌，全套裡最乾淨的一版）。
   兩種顯示模式，語意不同不混用同一組標記：
   - 裁罰紀錄：預設只載入近五年有裁罰者，9,700 多個點同時上圖既慢又難讀
   - 托嬰中心空位：核定收托減實際收托，只有托嬰中心有這組數字

   標記顏色不是唯一的區別管道：紅綠是色覺辨識最常見的失敗組合，所以綠取
   與硃砂明度差最大的亮綠（明度比 1.74；深綠只有 1.06 反而更難分），並讓
   「無裁罰」畫成空心環、「有裁罰」實心。聚合標記也依類型分群，否則一顆
   混合叢集會被塗成紅色，讓整區看起來都有裁罰。 */
(function () {
  var el = document.getElementById("map");
  if (!el) return;

  var map = L.map(el, { scrollWheelZoom: false });
  // 以台灣本島與離島範圍框定，避免固定 zoom 在寬螢幕留下大片海面
  map.fitBounds([[21.9, 119.3], [25.35, 122.05]]);
  L.tileLayer("https://wmts.nlsc.gov.tw/wmts/EMAP16/default/GoogleMapsCompatible/{z}/{y}/{x}", {
    maxZoom: 18,
    attribution:
      '底圖 <a href="https://maps.nlsc.gov.tw/" target="_blank" rel="noopener">國土測繪圖資服務雲</a>',
  }).addTo(map);
  map.on("click", function () { map.scrollWheelZoom.enable(); });

  var cache = {};      // 檔名 → rows
  var shown = [];      // 目前掛在地圖上的圖層
  var checkbox = document.getElementById("show-all");
  var showAllLabel = document.getElementById("show-all-label");
  var modeSelect = document.getElementById("map-mode");
  var status = document.getElementById("map-status");
  var legends = {
    penalty: document.getElementById("legend-penalty"),
    vacancy: document.getElementById("legend-vacancy"),
  };

  var STYLES = {
    penalty: { color: "#b23a2f", fill: "#b23a2f", opacity: 0.75, weight: 1 },
    clean: { color: "#2e9e5b", fill: "#2e9e5b", opacity: 0.2, weight: 1.5 },
    vacant: { color: "#234e70", fill: "#234e70", opacity: 0.7, weight: 1 },
    full: { color: "#7d94a4", fill: "#9bb0bf", opacity: 0.45, weight: 1 },
  };

  function marker(row, style, radius, label) {
    var m = L.circleMarker([row[1], row[0]], {
      radius: radius, color: style.color, weight: style.weight,
      fillColor: style.fill, fillOpacity: style.opacity,
    });
    m.bindPopup('<a href="' + row[4] + '"><b>' + row[3] + "</b></a><br>" + label);
    return m;
  }

  function group(extraClass) {
    return L.markerClusterGroup({
      chunkedLoading: true,
      maxClusterRadius: 55,
      spiderfyOnMaxZoom: true,
      iconCreateFunction: function (cluster) {
        var n = cluster.getChildCount();
        var size = n < 10 ? "small" : n < 100 ? "medium" : "large";
        return new L.DivIcon({
          html: "<div><span>" + n + "</span></div>",
          className: "marker-cluster marker-cluster-" + size + " " + extraClass,
          iconSize: new L.Point(40, 40),
        });
      },
    });
  }

  function fetchRows(file, done) {
    if (cache[file]) return done(cache[file]);
    if (status) status.textContent = "載入中…";
    fetch(file)
      .then(function (r) { return r.json(); })
      .then(function (data) { cache[file] = data.rows; done(data.rows); })
      .catch(function () {
        if (status) status.textContent = "地圖資料載入失敗，請重新整理。";
      });
  }

  function replace(layers) {
    shown.forEach(function (l) { map.removeLayer(l); });
    shown = layers;
    layers.forEach(function (l) { l.addTo(map); });
    if (status) status.textContent = "";
  }

  function renderPenalty(rows, includeClean) {
    var hit = group("cluster-penalty");
    var clean = group("cluster-clean");
    rows.forEach(function (row) {
      var n = row[2];
      if (n) {
        hit.addLayer(marker(row, STYLES.penalty, Math.min(5 + n, 11),
                            n + " 筆裁罰紀錄（近五年）"));
      } else if (includeClean) {
        clean.addLayer(marker(row, STYLES.clean, 4, "近五年無裁罰紀錄"));
      }
    });
    replace(includeClean ? [clean, hit] : [hit]);
  }

  function renderVacancy(rows) {
    var vacant = group("cluster-vacant");
    var full = group("cluster-full");
    rows.forEach(function (row) {
      var v = row[2];
      // 實際收托是站方回報值、非即時，標明時點免得家長照著撲空
      var label = (v > 0 ? "約 " + v + " 個空位" : "已額滿") +
                  "<br>核定 " + row[5] + " 人／實際 " + row[6] + " 人" +
                  (row[7] ? "（依 " + row[7] + " 名冊）" : "");
      if (v > 0) {
        vacant.addLayer(marker(row, STYLES.vacant, Math.min(4 + v / 3, 11), label));
      } else {
        full.addLayer(marker(row, STYLES.full, 4, label));
      }
    });
    replace([full, vacant]);
  }

  function render() {
    var vacancy = modeSelect && modeSelect.value === "vacancy";
    if (showAllLabel) showAllLabel.hidden = vacancy;
    if (legends.penalty) legends.penalty.hidden = vacancy;
    if (legends.vacancy) legends.vacancy.hidden = !vacancy;
    if (vacancy) {
      fetchRows("/data/points-vacancy.json", renderVacancy);
    } else if (checkbox && checkbox.checked) {
      fetchRows("/data/points-all.json", function (rows) { renderPenalty(rows, true); });
    } else {
      fetchRows("/data/points-penalized.json", function (rows) { renderPenalty(rows, false); });
    }
  }

  if (modeSelect) modeSelect.addEventListener("change", render);
  if (checkbox) checkbox.addEventListener("change", render);
  render();
})();
