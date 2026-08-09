"""托嬰中心主檔爬蟲的解析與座標換算測試（6.1 後續強化）。

零依賴，直接執行：python3 tests/test_ncwis.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "crawler"))

import ncwis_centers as ncwis  # noqa: E402

DETAIL = """<html><body>
<div class="dataPG style1"><div class="dataBlock w-0 main">
<div class="data-row sgl name"><div class="cell">機構名稱<span> 新北市私立測試托嬰中心</span></div></div>
<div class="data-row sgl"><span class="tag">所在地</span> 231新北市新店區中央一街20號1樓 </div>
<div class="data-row"><span class="tag">負責人姓名</span> 王大明 </div>
<div class="data-row"><span class="tag">聯絡電話</span> 02-82187139 </div>
<div class="data-row"><span class="tag">核定收托</span> 30 人 </div>
<div class="data-row"><span class="tag">實際收托</span> 25 人 </div>
<div class="data-row"><span class="tag">評鑑等級</span> 甲等 </div>
<div class="data-row"><span class="tag">最近一次評鑑年度</span> 114 </div>
<div class="data-row"><span class="tag">準公共化資格</span> 是 </div>
<div class="data-row sgl flex-wrap"><span class="tag">核備工作人員數</span>
  <div class="d-inline-block mr-3 p-1 alert-warning rounded my-1">主管人員 1 人</div>
  <div class="d-inline-block mr-3 p-1 alert-warning rounded my-1">托育人員 6 人</div>
</div>
<div class="data-row sgl flex-wrap align-items-start"><span class="tag">收費情形</span>
 <div class="p-0 col-xl-10"><p class="h6">托育方式：日間托育</p>
 <table><tbody>
 <tr><td data-label="費用名稱｜">托育費</td><td data-label="費用金額｜">18000</td>
     <td data-label="費用說明｜">月費</td></tr>
 <tr><td data-label="費用名稱｜">其他</td><td data-label="費用金額｜">0</td>
     <td data-label="費用說明｜">延長托育另計</td></tr>
 </tbody></table></div></div>
<div class="data-row sgl"><span class="tag">退費說明</span> 依定型化契約辦理。 </div>
<div class="data-row sgl"><span class="tag">違反法令紀錄</span> <ul> </ul> </div>
</div></div></body></html>"""

LIST = """<html><body>
<meta name="_csrf" content="aaaa-bbbb"/>
<script>
function initMarkers() {
  var x = '198634.25'; var y = '2667167.319'; var markerName = '甲中心';
  var windowsContent = '<a href="/home/childcare-center/detail/CW11507008">' + markerName + '</a>';
  var x = '0'; var y = '0'; var markerName = '乙中心';
  var windowsContent = '<a href="/home/childcare-center/detail/CW00000002">' + markerName + '</a>';
}
</script></body></html>"""


def main():
    # 一、詳情頁解析
    failures = []
    rec = ncwis.parse_detail(DETAIL, "CW00000001", failures)
    assert not failures, failures
    assert rec["name"] == "新北市私立測試托嬰中心"
    assert rec["address"] == "231新北市新店區中央一街20號1樓"
    assert rec["owner"] == "王大明"                       # 私有欄位，公開層另行排除
    assert (rec["capacity_approved"], rec["enrolled"]) == (30, 25)
    assert (rec["grade"], rec["grade_year"]) == ("甲等", "114")
    assert rec["pre_public"] == "是"
    assert rec["staff"] == {"主管人員": 1, "托育人員": 6}
    assert rec["fee_mode"] == "日間托育"
    assert [f["name"] for f in rec["fees"]] == ["托育費", "其他"]
    assert rec["fees"][0]["amount"] == "18000" and rec["fees"][0]["note"] == "月費"
    assert rec["refund_note"] == "依定型化契約辦理。"
    assert rec["violations"] == []                        # 站方此欄實務全空

    # 二、TWD97 二度分帶反算（實測基準：CW11507008 的站方標記）
    lat, lng = ncwis.tm2_to_wgs84(198634.25, 2667167.319)
    assert abs(lat - 24.108821) < 1e-5 and abs(lng - 120.494714) < 1e-5, (lat, lng)
    # 中央經線上的點：經度應正好落在 121
    _, lon0 = ncwis.tm2_to_wgs84(250000.0, 2600000.0)
    assert abs(lon0 - 121.0) < 1e-9, lon0

    # 三、從列表快照收集座標，站方為 0 者視為無座標
    with __import__("tempfile").TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "raw" / "ncwis" / "list").mkdir(parents=True)
        (d / "raw/ncwis/list/新北市_新店區.html").write_text(LIST, encoding="utf-8")
        coords, bad = ncwis.collect_coords(d)
        assert bad == 1, bad
        assert set(coords) == {"CW11507008"}, coords
        assert abs(coords["CW11507008"][0] - 24.108821) < 1e-5

    # 四、csrf token 每次連線都不同，存檔前必須剝除，否則每日快照都是假異動
    a = ncwis.strip_csrf('<meta name="_csrf" content="aaa"/>')
    b = ncwis.strip_csrf('<meta name="_csrf" content="bbb"/>')
    assert a == b, (a, b)

    print("test_ncwis：全部通過")


if __name__ == "__main__":
    main()
