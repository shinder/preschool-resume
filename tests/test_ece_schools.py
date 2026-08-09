"""幼兒園主檔爬蟲的解析測試（脫離 kiang 的最後一塊）。

零依賴，直接執行：python3 tests/test_ece_schools.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "crawler"))

import ece_schools as ece  # noqa: E402


def card(idx: int, name: str, *, town="南竿鄉", zip_addr="[209]介壽村13號",
         evaluations=True, with_map=True) -> str:
    ev = ("""<table id="GridView1_GridView11_%d">
      <tr><th>評鑑學年度</th><th>評鑑完成日</th><th>評鑑結果</th><th>評鑑報告</th></tr>
      <tr><td> 110 </td><td> 2022/07/31 </td><td> 全數指標通過基礎評鑑 </td><td>檢視</td></tr>
      <tr><td> 105 </td><td> 2017/01/05 </td><td> 全數指標通過基礎評鑑 </td><td>檢視</td></tr>
      </table>""" % idx) if evaluations else ""
    addr = (f'<a id="GridView1_hlAddr_{idx}" class="icon-map" '
            f'onclick="popwin=window.open(&#39;map/showmap.aspx?new=1&amp;sch=TOKEN{idx}&#39;,'
            f'&#39;&#39;);return false;">{zip_addr}</a>') if with_map else \
           f'<a id="GridView1_hlAddr_{idx}">{zip_addr}</a>'
    return f"""
    <h4><span id="GridView1_lblSchName_{idx}">{name}</span></h4>
    <span id="GridView1_lblCity_{idx}">連江縣</span>
    <span id="GridView1_lblArea_{idx}">{town}</span>
    <span id="GridView1_lblPub_{idx}">公立</span>
    {addr}
    <span id="GridView1_lblTel_{idx}">(08)3622192#181</span>
    <span id="GridView1_lblRegNum_{idx}">(80)連教字第05090號</span>
    <span id="GridView1_lblRegDate_{idx}">1991/07/09</span>
    <span id="GridView1_lblRegId_{idx}">府教學字第1090000249號</span>
    <span id="GridView1_lblCharge_{idx}">吳健忠</span>
    <a id="GridView1_hlUrl_{idx}" href="http://https://www.jsps.matsu.edu.tw/">www.jsps.matsu.edu.tw</a>
    {ev}
    <span id="GridView1_lblGenStd_{idx}">2,44</span>
    <span id="GridView1_lblStdPub_{idx}">無</span>
    <span id="GridView1_lblChildSvc_{idx}">有</span>
    <span id="GridView1_lblNoPunish_{idx}">無</span>
    <span id="GridView1_lblTSpace_{idx}">2,346.61&nbsp;平方公尺</span>
    <span id="GridView1_lblInSpace_{idx}">1,629.21&nbsp;平方公尺</span>
    <span id="GridView1_lblOASpace_{idx}">717.4&nbsp;平方公尺</span>
    <span id="GridView1_lblFloor_{idx}">1樓、2樓</span>
    <span id="GridView1_lblCar_{idx}">無</span>"""


PAGE = ("<html><body>" + card(0, "連江縣立介壽國民中小學附設幼兒園")
        + card(1, "連江縣立東引國民中小學附設幼兒園", town="東引鄉",
               evaluations=False, with_map=False)
        + '<span id="PageControl1_lblTotalCount">2</span></body></html>')


def main():
    rows = ece.page_rows(PAGE)
    assert set(rows) == {"0", "1"}, rows

    rec = ece.normalise(rows["0"])
    assert rec["name"] == "連江縣立介壽國民中小學附設幼兒園"
    assert (rec["city"], rec["town"], rec["org_type"]) == ("連江縣", "南竿鄉", "公立")
    assert rec["postal_code"] == "209"
    assert rec["address"] == "連江縣南竿鄉介壽村13號"      # 郵遞區號剝離、縣市區組合
    assert rec["capacity_approved"] == 244                # "2,44" 去逗號
    assert rec["reg_no"] == "(80)連教字第05090號"
    assert rec["owner"] == "吳健忠"                        # 個資，公開層另行排除
    # 站方 href 寫成 http://https://…，要改用連結文字
    assert rec["url"] == "www.jsps.matsu.edu.tw"
    # 「無」統一成 None，「有」保留
    assert rec["pre_public"] is None and rec["shuttle"] is None
    assert rec["after_school"] == "有"
    assert rec["area_total"] == "2,346.61 平方公尺"        # &nbsp; 轉一般空白
    assert [e["year"] for e in rec["evaluations"]] == ["110", "105"]
    assert rec["evaluations"][0]["result"] == "全數指標通過基礎評鑑"

    # 沒有地圖連結的那筆不該有 token，評鑑為空陣列
    assert "map_token" not in rows["1"]
    assert ece.normalise(rows["1"])["evaluations"] == []
    assert rows["0"]["map_token"] == "TOKEN0"

    # hInfo：「緯度,經度,鄉鎮,門牌,園名」，範圍外視為無效
    assert ece.parse_hinfo("26.160329,119.949821,連江縣南竿鄉,13號,某園") == (26.160329, 119.949821)
    assert ece.parse_hinfo("0,0,,,") is None
    assert ece.parse_hinfo("亂碼") is None

    # 從快照重新解析：座標由 coords tsv 帶入
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "raw/ece_schools/list").mkdir(parents=True)
        (d / "raw/ece_schools/coords").mkdir(parents=True)
        (d / "raw/ece_schools/list/連江縣_p0001.html").write_text(PAGE, encoding="utf-8")
        (d / "raw/ece_schools/coords/連江縣.tsv").write_text(
            "連江縣立介壽國民中小學附設幼兒園\t26.160329,119.949821,連江縣南竿鄉,13號,某園\n",
            encoding="utf-8")
        records, failures = ece.parse_all(d)
        assert len(records) == 2
        assert records[0]["lat"] == 26.160329 and records[0]["lng"] == 119.949821
        assert records[1]["lat"] is None
        assert len(failures) == 1 and "無座標" in failures[0]

    print("test_ece_schools：全部通過")


if __name__ == "__main__":
    main()
