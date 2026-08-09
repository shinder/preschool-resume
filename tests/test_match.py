"""裁罰↔機構對照驗收測試（工程執行計劃 6.2 完成定義）：
命中方法正確、換牌別名抓得到、未對上者全部有分類、品質門檻會擋。

零依賴，直接執行：python3 tests/test_match.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site"))

import match  # noqa: E402


def inst(iid: str, kind: str, city: str, name: str) -> dict:
    return {"id": f"{kind}:{iid}", "kind": kind,
            "source": "ece" if kind == "preschool" else "sfaa",
            "city": city, "town": None, "name": name}


def ece(pid: str, city: str, school: str, institution: str | None = None) -> dict:
    return {"id": f"ece:{pid}", "source": "ece", "city": city, "school": school,
            "institution": institution or school, "penalty_date": "2024-01-01"}


def sfaa(pid: str, city: str, institution: str | None,
         category: str = "兒童及少年福利機構") -> dict:
    return {"id": f"sfaa:{pid}", "source": "sfaa", "city": city,
            "institution": institution, "person": None if institution else "王大明",
            "targets": [{"category": category, "name": institution or "王大明"}],
            "penalty_date": "2024-01-01"}


def write(d: Path, insts: list[dict], eces: list[dict], sfaas: list[dict]):
    (d / "data/ece").mkdir(parents=True, exist_ok=True)
    (d / "data/sfaa").mkdir(parents=True, exist_ok=True)
    (d / "data/institutions.json").write_text(
        json.dumps({"as_of": "2026-08-09", "records": insts}, ensure_ascii=False),
        encoding="utf-8")
    for name, rs in (("ece", eces), ("sfaa", sfaas)):
        (d / f"data/{name}/penalties.json").write_text(
            json.dumps({"as_of": "2026-08-09", "records": rs}, ensure_ascii=False),
            encoding="utf-8")


def run(d: Path) -> int:
    return match.main(["--data-dir", str(d), "--as-of", "2026-08-09"])


def out(d: Path, name: str, key: str):
    return json.loads((d / "data" / name).read_text())[key]


MASTER = [
    inst("a", "preschool", "臺北市", "臺北市私立甲幼兒園"),
    inst("b", "preschool", "臺中市", "臺中市私立乙幼兒園"),
    inst("c", "preschool", "新北市", "新北市丙非營利幼兒園（委託新協會辦理）"),
    inst("d", "babycare", "臺中市", "臺中市私立丁托嬰中心"),
    inst("e", "babycare", "高雄市", "高雄市私立戊托嬰中心"),
]


def main():
    # 一、四種命中方法、別名、以及「只差縣市前綴不算改名」
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        write(d, MASTER, [
            ece("1", "台北市", "臺北市私立甲幼兒園"),                       # exact（台／臺）
            ece("2", "新北市", "新北市丙非營利幼兒園（委託新協會辦理）",
                "新北市丙非營利幼兒園（委託舊協會辦理）"),                   # 換受託單位
            ece("3", "臺中市", "臺中市私立乙幼兒園", "臺中市私立舊名幼兒園"),  # 改名
        ], [
            sfaa("1", "臺中市", "私立丁托嬰中心"),                          # 缺縣市前綴
            sfaa("2", "高雄市", "高雄市私立戊托嬰中心(負責人陳某)"),          # 括號註記
        ])
        assert run(d) == 0
        links = out(d, "penalty_institution.json", "links")
        assert links["ece:1"]["method"] == "exact"
        assert links["ece:2"]["method"] == "exact"  # 現名精準命中，舊名另記為別名
        assert links["ece:3"]["institution_id"] == "preschool:b"
        assert links["sfaa:1"]["method"] == "city_prefix"
        assert links["sfaa:2"]["method"] == "paren_strip"

        aliases = {a["former_name"]: a for a in out(d, "aliases.json", "aliases")}
        assert aliases["臺中市私立舊名幼兒園"]["type"] == "renamed"
        assert aliases["臺中市私立舊名幼兒園"]["current_name"] == "臺中市私立乙幼兒園"
        assert aliases["新北市丙非營利幼兒園（委託舊協會辦理）"]["type"] == "operator_change"
        # 缺縣市前綴與括號註記都不是改名，不該進別名表
        assert "私立丁托嬰中心" not in aliases, aliases
        assert "高雄市私立戊托嬰中心(負責人陳某)" not in aliases, aliases

    # 二、未對上者的分類，以及兒少法罰幼兒園時跨名冊查找
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        write(d, MASTER, [], [
            sfaa("1", "臺北市", None),                                  # 自然人
            sfaa("2", "臺北市", "李小美(托嬰中心稽查時尚無招牌)"),         # 未立案
            sfaa("3", "臺北市", "某某課後照顧中心", "課後照顧服務班及中心"),  # 非本站範圍
            sfaa("4", "臺北市", "張三"),                                # 站方分類錯置
            sfaa("5", "臺北市", "臺北市私立不存在托嬰中心"),              # 已停辦
            sfaa("6", "新北市", "陳某(新北市私立庚托嬰中心，現已變更負責人及中心名稱)"),
            sfaa("7", "臺北市", "臺北市私立甲幼兒園"),                    # 兒少法罰幼兒園
        ])
        assert run(d) == 0
        cls = {u["penalty_id"]: u["class"] for u in
               out(d, "match_unresolved.json", "unresolved")}
        assert cls == {"sfaa:1": "person", "sfaa:2": "unlicensed",
                       "sfaa:3": "out_of_scope", "sfaa:4": "misclassified",
                       "sfaa:5": "closed", "sfaa:6": "renamed_unlinked"}, cls
        # 幼兒園名稱出現在 sfaa 也要接到幼兒園名冊
        assert out(d, "penalty_institution.json",
                   "links")["sfaa:7"]["institution_id"] == "preschool:a"
        # 接不到現機構的換牌線索仍要留存
        unlinked = [a for a in out(d, "aliases.json", "aliases")
                    if a["type"] == "renamed_unlinked"]
        assert len(unlinked) == 1 and unlinked[0]["institution_id"] is None, unlinked

    # 三、ece 對照率低於門檻即中止
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        write(d, MASTER,
              [ece("1", "臺北市", "臺北市私立甲幼兒園")]
              + [ece(str(i), "臺北市", f"臺北市私立查無{i}幼兒園") for i in range(2, 5)],
              [])
        assert run(d) == 1
        assert not (d / "data/penalty_institution.json").exists()

    # 四、對照數大幅縮水即中止，且不覆寫既有對照檔
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        base = [ece(str(i), "臺北市", "臺北市私立甲幼兒園") for i in range(20)]
        write(d, MASTER, base, [])
        assert run(d) == 0
        assert len(out(d, "penalty_institution.json", "links")) == 20
        write(d, MASTER, base[:15], [])
        assert run(d) == 1
        assert len(out(d, "penalty_institution.json", "links")) == 20

    print("test_match：全部通過")


if __name__ == "__main__":
    main()
