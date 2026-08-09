/* 全台機構地圖：NLSC EMAP16 底圖（無等高線及門牌，全套裡最乾淨的一版）。
   預設只載入有裁罰紀錄的機構——9,709 個點同時上圖既慢又難讀。 */
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
  var status = document.getElementById("map-status");

  function marker(row) {
    // row: [lng, lat, penaltyCount, name, url]
    var n = row[2];
    var m = L.circleMarker([row[1], row[0]], {
      radius: n ? Math.min(5 + n, 11) : 4,
      color: n ? "#b23a2f" : "#7d94a4",
      weight: 1,
      fillColor: n ? "#b23a2f" : "#9bb0bf",
      fillOpacity: n ? 0.75 : 0.5,
    });
    var label = n ? n + " 筆裁罰紀錄" : "無裁罰紀錄";
    m.bindPopup(
      '<a href="' + row[4] + '"><b>' + row[3] + "</b></a><br>" + label
    );
    return m;
  }

  function load(name, file, done) {
    if (layers[name]) return done(layers[name]);
    fetch(file)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var group = L.markerClusterGroup({
          chunkedLoading: true,
          maxClusterRadius: 55,
          spiderfyOnMaxZoom: true,
        });
        data.rows.forEach(function (row) { group.addLayer(marker(row)); });
        layers[name] = group;
        done(group);
      })
      .catch(function () {
        if (status) status.textContent = "地圖資料載入失敗，請重新整理。";
      });
  }

  function show(name, file) {
    Object.keys(layers).forEach(function (k) {
      if (k !== name && map.hasLayer(layers[k])) map.removeLayer(layers[k]);
    });
    if (status) status.textContent = "載入中…";
    load(name, file, function (group) {
      group.addTo(map);
      if (status) status.textContent = "";
    });
  }

  show("penalized", "/data/points-penalized.json");

  if (checkbox) {
    checkbox.addEventListener("change", function () {
      show(checkbox.checked ? "all" : "penalized",
           checkbox.checked ? "/data/points-all.json" : "/data/points-penalized.json");
    });
  }
})();
