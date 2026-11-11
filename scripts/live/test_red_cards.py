"""赛果红牌：只在临时库上验证写入规则，不打开正式库。"""
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import result_ingest as R  # noqa: E402

TZ = R.TZ
DAY = "2026-10-10"
KO = "2026-10-10T13:00:00+08:00"


def mkdb(tmp: Path) -> str:
    directory = tmp / "v2d3"
    directory.mkdir()
    db = str(directory / "app.db")
    conn = sqlite3.connect(db)
    conn.executescript("""
    create table import_batches(id integer primary key autoincrement, source_file text not null, month text,
      imported_at text not null default (datetime('now')), row_count integer, status text not null default 'ok');
    create table matches(id integer primary key, match_uid text, scope text, jingcai_date text, jc_id text,
      home_team text, away_team text, kickoff_at text);
    create table results(match_id integer primary key, home_goals integer, away_goals integer,
      total_goals integer, wdl text);
    """)
    conn.execute(
        "insert into matches values (1,'a','jingcai',?,'六001','大阪樱花','横滨水手',?)", (DAY, KO))
    conn.commit()
    conn.close()
    return db


def finished_item(number, goals, reds, *, status="finished", with_cards=True):
    item = {
        "status": status,
        "goals": {"home": None if goals is None else goals[0], "away": None if goals is None else goals[1]},
        "lottery": {"jingcailottery": {"number": number}},
    }
    if with_cards:
        item["cards"] = {
            "home": {"yellow": 3, "red": None if reds is None else reds[0]},
            "away": {"yellow": 4, "red": None if reds is None else reds[1]},
        }
    return item


def write_csl(directory: Path, items: list[dict]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "csl.json").write_text(
        json.dumps({"jingcai_date": DAY, "data": items}, ensure_ascii=False), encoding="utf-8")


def write_main_csv(directory: Path, name: str, rows: list[str]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    header = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,HR,AR\n"
    (directory / name).write_text(header + "\n".join(rows) + "\n", encoding="utf-8")


def write_team_map(path: Path, pairs: list[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["中文,英文"] + [f"{zh},{en}" for zh, en in pairs]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def reds(db: str, match_id: int):
    conn = sqlite3.connect(db)
    row = conn.execute(
        "select home_goals, away_goals, total_goals, wdl, home_red_cards, away_red_cards "
        "from results where match_id = ?", (match_id,)).fetchone()
    conn.close()
    return row


def columns(db: str) -> list[str]:
    conn = sqlite3.connect(db)
    names = [row[1] for row in conn.execute("pragma table_info(results)")]
    conn.close()
    return names


def test_finished_5df_cards_keep_confirmed_zero_and_ignore_missing():
    assert R.csl_red_cards(finished_item("周六001", (1, 0), (0, 2))) == (0, 2)
    assert R.csl_red_cards(finished_item("周六001", (1, 0), ("0", "1"))) == (0, 1)
    assert R.csl_red_cards(finished_item("周六001", (1, 0), (None, 1))) is None
    assert R.csl_red_cards(finished_item("周六001", (1, 0), (0, 0), with_cards=False)) is None
    assert R.parse_card_count("") is None
    assert R.parse_card_count(True) is None


def test_scheduled_5df_zero_cards_are_not_confirmed():
    item = finished_item("周六001", None, (0, 0), status="scheduled")
    assert R.csl_score(item) is None
    assert R.csl_red_cards(item) is None
    decision = R.annotate_red_cards(R.decide(None, item), item, None)
    assert decision["action"] == "wait"
    assert decision.get("red_cards") is None


def test_apply_writes_finished_5df_cards_including_zero(tmp_path):
    db = mkdb(tmp_path)
    item = finished_item("周六001", (1, 0), (0, 2))
    decision = R.annotate_red_cards(R.decide(None, item), item, None)
    assert decision["red_cards"] == (0, 2) and decision["red_card_source"] == "5df_csl"
    now = dt.datetime(2026, 10, 10, 20, 0, tzinfo=TZ)
    R.apply(db, [({"id": 1}, decision)], tmp_path / "bk", "red", now)
    assert reds(db, 1) == (1, 0, 1, "胜", 0, 2)
    assert "home_yellow_cards" not in columns(db)
    assert "away_yellow_cards" not in columns(db)


def test_apply_leaves_sporttery_only_and_scheduled_zeros_null(tmp_path):
    db = mkdb(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "insert into matches values (2,'b','jingcai',?,'六002','首尔FC','济州SK',?)", (DAY, KO))
    conn.commit()
    conn.close()
    scheduled = finished_item("周六002", None, (0, 0), status="scheduled")
    sporttery = {
        "matchNumStr": "周六001", "matchDate": DAY, "homeTeam": "大阪樱花", "awayTeam": "横滨水手",
        "allHomeTeam": "大阪樱花", "allAwayTeam": "横滨水手", "sectionsNo999": "2:0", "poolStatus": "Payout",
    }
    only_sporttery = R.annotate_red_cards(R.decide(sporttery, None), None, None)
    with_scheduled = R.annotate_red_cards(
        R.decide({**sporttery, "matchNumStr": "周六002", "sectionsNo999": "1:0"}, scheduled), scheduled, None)
    assert only_sporttery["source"] == "sporttery" and only_sporttery["red_cards"] is None
    assert with_scheduled["red_cards"] is None
    now = dt.datetime(2026, 10, 10, 20, 0, tzinfo=TZ)
    R.apply(db, [({"id": 1}, only_sporttery), ({"id": 2}, with_scheduled)], tmp_path / "bk", "red", now)
    assert reds(db, 1) == (2, 0, 2, "胜", None, None)
    assert reds(db, 2) == (1, 0, 1, "胜", None, None)


def test_apply_does_not_overwrite_existing_score_to_store_cards(tmp_path):
    db = mkdb(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("insert into results values (1, 9, 8, 17, '胜')")
    conn.commit()
    conn.close()
    item = finished_item("周六001", (1, 0), (2, 0))
    decision = R.annotate_red_cards(R.decide(None, item), item, None)
    now = dt.datetime(2026, 10, 10, 20, 0, tzinfo=TZ)
    result = R.apply(db, [({"id": 1}, decision)], tmp_path / "bk", "red", now)
    assert result["inserted"] == 0
    assert reds(db, 1) == (9, 8, 17, "胜", None, None)


def test_fd_rows_use_main_league_counts_and_skip_new_files(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "TEAMS_CSV", str(tmp_path / "none.csv"))
    raw = tmp_path / "fd"
    write_main_csv(raw, "mmz4281_2627_E0.csv", [
        "E0,10/10/2026,Inter Turku,KuPS,1,0,0,2",
        "E0,10/10/2026,Alpha,Beta,2,2,,",
    ])
    (raw / "new_FIN.csv").write_text(
        "Country,League,Season,Date,Time,Home,Away,HG,AG,Res,HR,AR\n"
        "Finland,Veikkausliiga,2026,10/10/2026,18:00,Inter Turku,KuPS,1,0,H,9,8\n",
        encoding="utf-8")
    (raw / "new_BLANK.csv").write_text(
        "Country,League,Season,Date,Time,Home,Away,HG,AG,Res\n"
        "Finland,Veikkausliiga,2026,10/10/2026,18:00,Other,Side,3,0,H\n",
        encoding="utf-8")
    rows = {((row["home_raw"], row["away_raw"]), row["file"]): row["red_cards"] for row in R.fd_rows(raw)}
    assert rows[(("Inter Turku", "KuPS"), "mmz4281_2627_E0.csv")] == (0, 2)
    assert rows[(("Alpha", "Beta"), "mmz4281_2627_E0.csv")] is None
    assert rows[(("Inter Turku", "KuPS"), "new_FIN.csv")] is None
    assert rows[(("Other", "Side"), "new_BLANK.csv")] is None


def test_insert_uses_fd_counts_only_when_5df_has_no_usable_cards(tmp_path):
    db = mkdb(tmp_path)
    item = finished_item("周六001", (1, 0), None, with_cards=False)
    filled = R.annotate_red_cards(R.decide(None, item), item, {"score": (1, 0), "red_cards": (0, 1)})
    assert filled["red_cards"] == (0, 1) and filled["red_card_source"] == "football_data_co_uk"
    skipped = R.annotate_red_cards(R.decide(None, item), item, {"score": (3, 0), "red_cards": (4, 4)})
    assert skipped["red_cards"] is None
    now = dt.datetime(2026, 10, 10, 20, 0, tzinfo=TZ)
    R.apply(db, [({"id": 1}, filled)], tmp_path / "bk", "red", now)
    assert reds(db, 1) == (1, 0, 1, "胜", 0, 1)


def test_conflict_on_insert_leaves_both_columns_null(tmp_path):
    db = mkdb(tmp_path)
    item = finished_item("周六001", (1, 0), (1, 0))
    fd_row = {"score": (1, 0), "red_cards": (0, 2), "file": "mmz4281_2627_E0.csv"}
    decision = R.annotate_red_cards(R.decide(None, item), item, fd_row)
    assert decision["red_cards"] is None
    assert decision["flags"][0][0] == "red_card_conflict"
    now = dt.datetime(2026, 10, 10, 20, 0, tzinfo=TZ)
    R.apply(db, [({"id": 1}, decision)], tmp_path / "bk", "red", now)
    assert reds(db, 1) == (1, 0, 1, "胜", None, None)
    conn = sqlite3.connect(db)
    flag = conn.execute(
        "select flag, detail_json from result_ingest_flags where match_id = 1").fetchone()
    conn.close()
    assert flag[0] == "red_card_conflict"
    detail = json.loads(flag[1])
    assert detail["5df"] == [1, 0] and detail["football_data"] == [0, 2]


def test_backfill_updates_only_null_red_cards_and_keeps_scores(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "TEAMS_CSV", str(tmp_path / "none.csv"))
    db = mkdb(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("insert into matches values (2,'b','jingcai',?,'六002','首尔FC','济州SK',?)", (DAY, KO))
    conn.execute("insert into matches values (3,'c','jingcai',?,'六003','英特杜古','古比斯',?)", (DAY, KO))
    conn.execute("insert into matches values (4,'d','jingcai',?,'六004','某甲','某乙',?)", (DAY, KO))
    conn.execute("insert into results values (2, 9, 8, 17, '胜')")
    conn.execute("insert into results values (3, 2, 1, 3, '胜')")
    conn.execute("insert into results values (4, 0, 0, 0, '平')")
    R.ensure_results_red_card_columns(conn)
    conn.execute("update results set home_red_cards = 4, away_red_cards = 1 where match_id = 2")
    conn.commit()
    conn.close()
    csl_dir = tmp_path / "csl"
    write_csl(csl_dir, [
        finished_item("周六002", (9, 8), (9, 9)),
        finished_item("周六003", (2, 1), (0, 3)),
    ])
    fd_dir = tmp_path / "fd"
    write_main_csv(fd_dir, "mmz4281_2627_E0.csv", ["E0,10/10/2026,Inter Turku,KuPS,2,1,0,3"])
    team_map = tmp_path / "fd_team_map.csv"
    write_team_map(team_map, [("英特杜古", "Inter Turku"), ("古比斯", "KuPS")])
    now = dt.datetime(2026, 10, 11, 9, 0, tzinfo=TZ)
    preview = R.run_red_card_backfill(
        db, [str(csl_dir)], fd_dir, team_map, tmp_path / "bk", now, since=None, apply=False)
    assert preview["dry_run"] is True and preview["updated"] == 0 and preview["backup"] is None
    assert reds(db, 2) == (9, 8, 17, "胜", 4, 1)
    assert reds(db, 3) == (2, 1, 3, "胜", None, None)
    applied = R.run_red_card_backfill(
        db, [str(csl_dir)], fd_dir, team_map, tmp_path / "bk", now, since=None, apply=True)
    assert applied["updated"] == 1 and applied["backup"]
    assert reds(db, 2) == (9, 8, 17, "胜", 4, 1)
    assert reds(db, 3) == (2, 1, 3, "胜", 0, 3)
    assert reds(db, 4) == (0, 0, 0, "平", None, None)
    blocked = R.apply_red_card_fills(db, [{
        "match": {"id": 2, "home_goals": 9, "away_goals": 8, "total_goals": 17, "wdl": "胜"},
        "action": "fill", "cards": (1, 1), "source": "5df_csl", "resolve_conflict": False,
    }], tmp_path / "bk2", now, dry_run=False)
    assert blocked["updated"] == 0
    assert reds(db, 2) == (9, 8, 17, "胜", 4, 1)


def test_backfill_uses_fd_when_5df_has_no_cards_and_conflict_stays_null(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "TEAMS_CSV", str(tmp_path / "none.csv"))
    db = mkdb(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("insert into matches values (5,'e','jingcai',?,'六005','圣保罗','维多利亚',?)", (DAY, "2026-10-10T08:00:00+08:00"))
    conn.execute("insert into results values (1, 1, 0, 1, '胜')")
    conn.execute("insert into results values (5, 2, 0, 2, '胜')")
    conn.commit()
    conn.close()
    csl_dir = tmp_path / "csl"
    write_csl(csl_dir, [
        finished_item("周六001", (1, 0), (1, 0)),
        finished_item("周六005", (2, 0), (0, 0), with_cards=False),
    ])
    fd_dir = tmp_path / "fd"
    write_main_csv(fd_dir, "mmz4281_2627_E0.csv", [
        "E0,10/10/2026,Cerezo Osaka,Yokohama F Marinos,1,0,0,2",
        "E0,10/10/2026,Sao Paulo,Vitoria,2,0,0,0",
    ])
    team_map = tmp_path / "map.csv"
    write_team_map(team_map, [
        ("大阪樱花", "Cerezo Osaka"), ("横滨水手", "Yokohama F Marinos"),
        ("圣保罗", "Sao Paulo"), ("维多利亚", "Vitoria"),
    ])
    now = dt.datetime(2026, 10, 11, 9, 0, tzinfo=TZ)
    result = R.run_red_card_backfill(
        db, [str(csl_dir)], fd_dir, team_map, tmp_path / "bk", now, since=None, apply=True)
    assert reds(db, 1) == (1, 0, 1, "胜", None, None)
    assert reds(db, 5) == (2, 0, 2, "胜", 0, 0)
    assert result["conflicts"] == 1
    conn = sqlite3.connect(db)
    flagged = conn.execute(
        "select match_id from result_ingest_flags where flag = 'red_card_conflict'").fetchall()
    conn.close()
    assert flagged == [(1,)]


def test_red_cards_cli_refuses_missing_db_and_formal_path_and_defaults_to_dry_run(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FIVEDOLLAR_FOOTBALL_API_KEY", "super-secret-key")
    monkeypatch.setattr(R, "FORMAL_MERGED", True)
    with pytest.raises(SystemExit, match="必须显式传入 --db"):
        R.main(["red_cards"])
    with pytest.raises(SystemExit, match="必须显式传入 --db"):
        R.main(["red_cards", "--apply"])
    with pytest.raises(SystemExit, match="正式库"):
        R.main(["red_cards", "--db", R.PROD_DB, "--apply"])
    db = mkdb(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("insert into results values (1, 1, 0, 1, '胜')")
    conn.commit()
    conn.close()
    csl_dir = tmp_path / "csl"
    write_csl(csl_dir, [finished_item("周六001", (1, 0), (0, 1))])
    R.main(["red_cards", "--db", db, "--red-csl-dir", str(csl_dir), "--backup-root", str(tmp_path / "bk")])
    preview = json.loads(capsys.readouterr().out)
    assert preview["dry_run"] is True and preview["updated"] == 0
    assert "super-secret-key" not in json.dumps(preview)
    assert "home_red_cards" not in columns(db)
    conn = sqlite3.connect(db)
    assert conn.execute(
        "select home_goals, away_goals, total_goals, wdl from results where match_id = 1").fetchone() == (1, 0, 1, "胜")
    conn.close()
    R.main(["red_cards", "--db", db, "--red-csl-dir", str(csl_dir), "--backup-root", str(tmp_path / "bk"), "--apply"])
    applied = json.loads(capsys.readouterr().out)
    assert applied["dry_run"] is False and applied["updated"] == 1
    assert "super-secret-key" not in json.dumps(applied)
    assert reds(db, 1) == (1, 0, 1, "胜", 0, 1)
