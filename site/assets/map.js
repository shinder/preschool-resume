/* 全台機構地圖：NLSC EMAP16 底圖（無等高線及門牌，全套裡最乾淨的一版）。
   兩種顯示模式，語意不同不混用同一組標記：
   - 裁罰紀錄：預設只載入近五年有裁罰者，9,709 個點同時上圖既慢又難讀
   - 托嬰中心空位：核定收托減實際收托，只有托嬰中心有這組數字 */
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

  var layers = {};
  var checkbox = document.getElementById("show-all");
  var showAllLabel = document.getElementById("show-all-label");
  var modeSelect = document.getElementById("map-mode");
  var status = document.getElementById("map-status");
  var legends = {
    penalty: document.getElementById("legend-penalty"),
    vacancy: document.getElementById("legend-vacancy"),
  };

  function penaltyMarker(row) {
    // row: [lng, lat, 近五年裁罰筆數, name, url]
    var n = row[2];
    return styled(row, {
      radius: n ? Math.min(5 + n, 11) : 4,
      color: n ? "#b23a2f" : "#7d94a4",
      fill: n ? "#b23a2f" : "#9bb0bf",
      opacity: n ? 0.75 : 0.5,
    }, n ? n + " 筆裁罰紀錄（近五年）" : "近五年無裁罰紀錄");
  }

  function vacancyMarker(row) {
    // row: [lng, lat, 空位, name, url, 核定, 實際]
    var v = row[2];
    return styled(row, {
      radius: v > 0 ? Math.min(4 + v / 3, 11) : 4,
      color: v > 0 ? "#234e70" : "#7d94a4",
      fill: v > 0 ? "#234e70" : "#9bb0bf",
      opacity: v > 0 ? 0.7 : 0.45,
    }, (v > 0 ? "尚有 " + v + " 個空位" : "已額滿") +
       "<br>核定 " + row[5] + " 人／實際 " + row[6] + " 人");
  }

  function styled(row, style, label) {
    var m = L.circleMarker([row[1], row[0]], {
      radius: style.radius, color: style.color, weight: 1,
      fillColor: style.fill, fillOpacity: style.opacity,
    });
    m.bindPopup('<a href="' + row[4] + '"><b>' + row[3] + "</b></a><br>" + label);
    return m;
  }

  function load(name, file, make, done) {
    if (layers[name]) return done(layers[name]);
    fetch(file)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var group = L.markerClusterGroup({
          chunkedLoading: true,
          maxClusterRadius: 55,
          spiderfyOnMaxZoom: true,
        });
        data.rows.forEach(function (row) { group.addLayer(make(row)); });
        layers[name] = group;
        done(group);
      })
      .catch(function () {
        if (status) status.textContent = "地圖資料載入失敗，請重新整理。";
      });
  }

  function show(name, file, make) {
    Object.keys(layers).forEach(function (k) {
      if (k !== name && map.hasLayer(layers[k])) map.removeLayer(layers[k]);
    });
    if (status) status.textContent = "載入中…";
    load(name, file, make, function (group) {
      group.addTo(map);
      if (status) status.textContent = "";
    });
  }

  function render() {
    var vacancy = modeSelect && modeSelect.value === "vacancy";
    el.classList.toggle("mode-vacancy", !!vacancy);
    if (showAllLabel) showAllLabel.hidden = vacancy;
    if (legends.penalty) legends.penalty.hidden = vacancy;
    if (legends.vacancy) legends.vacancy.hidden = !vacancy;
    if (vacancy) {
      show("vacancy", "/data/points-vacancy.json", vacancyMarker);
    } else if (checkbox && checkbox.checked) {
      show("all", "/data/points-all.json", penaltyMarker);
    } else {
      show("penalized", "/data/points-penalized.json", penaltyMarker);
    }
  }

  if (modeSelect) modeSelect.addEventListener("change", render);
  if (checkbox) checkbox.addEventListener("change", render);
  render();
})();
