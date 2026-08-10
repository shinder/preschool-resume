/* 全台機構地圖：NLSC EMAP16 底圖（無等高線及門牌，全套裡最乾淨的一版）。

   聚合層與個別層分工不同，配色也分開：
   - 聚合（縮小時）：一顆橘色圓圈標該區「所有機構總數」。混合叢集不該被塗成
     紅或綠——那會讓整區看起來都有裁罰、或都沒有；橘色是中性的計數標記。
   - 個別（放大後）：紅＝近五年有裁罰、綠＝無。兩者大小一致，件數放在彈出視窗，
     不用半徑編碼，免得「大紅點」在縮圖層級被誤讀成嚴重程度。
     紅綠是色覺辨識最常見的失敗組合，故綠取與硃砂明度差最大的亮綠（明度比 1.74，
     深綠只有 1.06 反而更難分），並讓無裁罰畫成空心環、有裁罰實心，
     多給一個非色彩的區別管道。

   兩種顯示模式（裁罰紀錄／托嬰中心空位）語意不同，不共用同一組標記。 */
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

  var cache = {};
  var current = null;
  var modeSelect = document.getElementById("map-mode");
  var status = document.getElementById("map-status");
  var legends = {
    penalty: document.getElementById("legend-penalty"),
    vacancy: document.getElementById("legend-vacancy"),
  };

  var RADIUS = 8;
  var STYLES = {
    penalty: { color: "#b23a2f", fill: "#b23a2f", opacity: 0.8, weight: 1 },
    clean: { color: "#2e9e5b", fill: "#2e9e5b", opacity: 0.4, weight: 2 },
    vacant: { color: "#234e70", fill: "#234e70", opacity: 0.75, weight: 1 },
    full: { color: "#7d94a4", fill: "#9bb0bf", opacity: 0.45, weight: 1 },
  };

  function marker(row, style, label) {
    var m = L.circleMarker([row[1], row[0]], {
      radius: RADIUS, color: style.color, weight: style.weight,
      fillColor: style.fill, fillOpacity: style.opacity,
    });
    var url = "/i/" + row[4];
    m.bindPopup('<a href="' + url + '"><b>' + row[3] + "</b></a><br>" + label);
    return m;
  }

  function cluster() {
    return L.markerClusterGroup({
      chunkedLoading: true,
      maxClusterRadius: 55,
      spiderfyOnMaxZoom: true,
      // 放大到街廓層級就全部攤開，否則綠點永遠躲在叢集裡看不到
      disableClusteringAtZoom: 15,
      iconCreateFunction: function (c) {
        var n = c.getChildCount();
        var size = n < 10 ? "small" : n < 100 ? "medium" : "large";
        return new L.DivIcon({
          html: "<div><span>" + n + "</span></div>",
          className: "marker-cluster marker-cluster-" + size,
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

  function draw(rows, build) {
    var group = cluster();
    rows.forEach(function (row) { group.addLayer(build(row)); });
    if (current) map.removeLayer(current);
    current = group;
    group.addTo(map);
    if (status) status.textContent = "";
  }

  function penaltyMarker(row) {
    var n = row[2];
    return marker(row, n ? STYLES.penalty : STYLES.clean,
                  n ? n + " 筆裁罰紀錄（近五年）" : "近五年無裁罰紀錄");
  }

  function vacancyMarker(row) {
    var v = row[2];
    // 實際收托是站方回報值、非即時，標明時點免得家長照著撲空
    var label = (v > 0 ? "約 " + v + " 個空位" : "已額滿") +
                "<br>核定 " + row[5] + " 人／實際 " + row[6] + " 人" +
                (row[7] ? "（依 " + row[7] + " 名冊）" : "");
    return marker(row, v > 0 ? STYLES.vacant : STYLES.full, label);
  }

  function render() {
    var vacancy = modeSelect && modeSelect.value === "vacancy";
    if (legends.penalty) legends.penalty.hidden = vacancy;
    if (legends.vacancy) legends.vacancy.hidden = !vacancy;
    if (vacancy) {
      fetchRows("/data/points-vacancy.json", function (rows) {
        draw(rows, vacancyMarker);
      });
    } else {
      fetchRows("/data/points-all.json", function (rows) {
        draw(rows, penaltyMarker);
      });
    }
  }

  if (modeSelect) modeSelect.addEventListener("change", render);
  render();
})();
