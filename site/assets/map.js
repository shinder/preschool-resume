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

  // 以台灣本島與離島範圍框定，避免固定 zoom 在寬螢幕留下大片海面
  var TW_BOUNDS = [[21.9, 119.3], [25.35, 122.05]];

  /* 網址雜湊 #縮放/緯度/經度[/模式]：讓地圖視角跟著網址走。
     沒有它的話，從地圖點進機構頁再返回，地圖會重置回全台——使用者剛才
     找到的那一區就白找了。順帶讓「某一區的地圖」可以直接複製網址分享。
     瀏覽器的 bfcache 有時會救到，但不可依賴，這裡做成確定的行為。 */
  function readHash() {
    var p = location.hash.replace(/^#/, "").split("/");
    var z = parseInt(p[0], 10), lat = parseFloat(p[1]), lng = parseFloat(p[2]);
    if (!isFinite(z) || !isFinite(lat) || !isFinite(lng)) return null;
    if (z < 1 || z > 18 || lat < -90 || lat > 90 || lng < -180 || lng > 180) return null;
    return { z: z, lat: lat, lng: lng, mode: p[3] === "vacancy" ? "vacancy" : "penalty" };
  }

  var view = readHash();
  var map = L.map(el, { scrollWheelZoom: false });
  if (view) map.setView([view.lat, view.lng], view.z);
  else map.fitBounds(TW_BOUNDS);
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
  if (view && modeSelect) modeSelect.value = view.mode;

  function writeHash() {
    var c = map.getCenter();
    // replaceState 而非 pushState：每拖曳一次就疊一筆歷史的話，
    // 返回鍵會變成要按幾十次才離得開這一頁。
    history.replaceState(null, "", location.pathname + location.search +
      "#" + map.getZoom() + "/" + c.lat.toFixed(4) + "/" + c.lng.toFixed(4) +
      (modeSelect && modeSelect.value === "vacancy" ? "/vacancy" : ""));
  }

  // 初次載入那一次的 moveend 不寫入，讓沒動過地圖的訪客保有乾淨的網址；
  // 萬一漏擋也只是寫入當下這個（正確的）視角，無害。
  var settling = true;
  map.whenReady(function () { setTimeout(function () { settling = false; }, 0); });
  map.on("moveend", function () { if (!settling) writeHash(); });

  // 同一頁只改雜湊時瀏覽器不會重新載入，本檔也就不會再跑一次——不接這個事件的話
  // 會出現「網址寫一個視角、地圖停在另一個視角」。replaceState 不觸發 hashchange，
  // 所以不會跟上面的寫入互相打架。
  window.addEventListener("hashchange", function () {
    var v = readHash();
    if (!v) return;
    // 先套模式再移動視角：setView 觸發的 moveend 會回寫網址，若那時模式還沒更新，
    // 就會把舊模式寫回去，變成「網址寫 vacancy、畫面其實是 penalty」。
    if (modeSelect && modeSelect.value !== v.mode) {
      modeSelect.value = v.mode;
      render();
    }
    map.setView([v.lat, v.lng], v.z);
  });

  var RADIUS = 8;
  var UNCLUSTER_ZOOM = 15;   // 此層級起不再聚合；聚合點擊最多帶到這裡
  var STYLES = {
    penalty: { color: "#b23a2f", fill: "#b23a2f", opacity: 0.8, weight: 1 },
    clean: { color: "#2e9e5b", fill: "#2e9e5b", opacity: 0.4, weight: 2 },
    // 空位圖層的兩態。原本「有空位」用靛藍 #234e70——它對紙底其實有 7.7:1 的
    // 對比，但跟站上的結構色與圖磚的水體同一個色相，讀起來像底圖的一部分而不是
    // 資料標記。底圖已去飽和，飽和度是這裡唯一沒被佔用的通道，故改用高飽和亮藍。
    // 已額滿的底色本來就淺，改以較粗邊框撐出輪廓——同時延續「實心／空心」這個
    // 非色彩的區別管道，與裁罰圖層的紅實心／綠空心同一套邏輯。
    vacant: { color: "#0b72d9", fill: "#0b72d9", opacity: 0.8, weight: 1 },
    full: { color: "#5f7d91", fill: "#c3d3dd", opacity: 0.5, weight: 2 },
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
      // 點擊行為自己接管（見 bindClusterClick），關掉套件的兩個預設：
      // 內建的 zoomToBounds 對「小而密」的叢集會算不出該去哪一級而原地不動，
      // 實測點計數 2 的叢集完全沒反應——不放大也沒展開。
      spiderfyOnMaxZoom: false,
      zoomToBoundsOnClick: false,
      // 放大到街廓層級就全部攤開，否則綠點永遠躲在叢集裡看不到
      disableClusteringAtZoom: UNCLUSTER_ZOOM,
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

  /* 聚合點擊：一律至少放大一級，最多到 UNCLUSTER_ZOOM（該層級所有標記都攤開）。
     大叢集依子點範圍逐級靠近，小叢集直接跳到攤開層級——後者正是內建行為失效的
     情況：兩三個點擠在一起時算不出中間層級，於是原地不動，使用者只覺得點了沒用。*/
  function bindClusterClick(group) {
    group.on("clusterclick", function (e) {
      var fit = map.getBoundsZoom(e.layer.getBounds());
      map.setView(e.latlng, Math.max(map.getZoom() + 1, Math.min(fit, UNCLUSTER_ZOOM)));
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
    bindClusterClick(group);
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

  if (modeSelect) {
    modeSelect.addEventListener("change", function () { writeHash(); render(); });
  }
  render();
})();
