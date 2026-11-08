"""赛前积分榜快照：只在临时库上验证，不请求外网，不写正式库。"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_LIVE = Path(__file__).resolve().parent
if str(_LIVE) not in sys.path:
    sys.path.insert(0, str(_LIVE))

import prematch_standings as ps  # noqa: E402

TZ = timezone(timedelta(hours=8))
KICK = datetime(2026, 11, 8, 19, 30, tzinfo=TZ)
BEFORE = datetime(2026, 11, 8, 10, 0, tzinfo=TZ)
LATER = datetime(2026, 11, 8, 11, 0, tzinfo=TZ)
AFTER = datetime(2026, 11, 8, 19, 31, tzinfo=TZ)


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, name, path, params=None):
        self.calls.append({"name": name, "path": path, "params": dict(params or {})})
        league = int(params["league"])
        body = self.payload[league]
        if callable(body):
            body = body()
        return body


def _table(home_pos=2, away_pos=5):
    return {
        "success": 1,
        "data": {
            "league_id": 42,
            "season": "26/27",
            "type": "total",
            "table": [
                {
                    "position": home_pos,
                    "team": {"id": 101, "name": "主队甲"},
                    "played": 8, "win": 5, "draw": 2, "lose": 1,
                    "goals_for": 12, "goals_against": 6, "points": 17,
                },
                {
                    "position": away_pos,
                    "team": {"id": 202, "name": "客队乙"},
                    "played": 8, "win": 3, "draw": 2, "lose": 3,
                    "goals_for": 9, "goals_against": 9, "points": 11,
                },
                {"position": 0, "team": {"id": 303, "name": "无效名次"}, "points": 1},
            ],
        },
    }


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "v2d3" / "app.db"
    path.parent.mkdir(parents=True)
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_uid TEXT UNIQUE,
            kickoff_at TEXT,
            home_team TEXT,
            away_team TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE stats (
            match_id INTEGER PRIMARY KEY,
            recent_json TEXT,
            rank_home INTEGER,
            rank_away INTEGER,
            source_rank TEXT,
            as_of_rank TEXT
        )"""
    )
    conn.commit()
    conn.close()
    return path


def _add_match(db: Path, uid: str, kickoff: datetime, *, recent="keep", **stats):
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO matches (match_uid, kickoff_at, home_team, away_team) VALUES (?,?,?,?)",
        (uid, ps.iso(kickoff), "主队甲", "客队乙"),
    )
    match_id = conn.execute("SELECT id FROM matches WHERE match_uid=?", (uid,)).fetchone()[0]
    cols = ["match_id", "recent_json"]
    vals = [match_id, recent]
    for key, value in stats.items():
        cols.append(key)
        vals.append(value)
    conn.execute(
        f"INSERT INTO stats ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
        vals,
    )
    conn.commit()
    conn.close()
    return match_id


def _match(uid, kickoff, league_id=42, home_id=101, away_id=202):
    return {
        "match_uid": uid,
        "league_id": league_id,
        "league": "测试联赛",
        "kickoff_at": ps.iso(kickoff),
        "home_id": home_id,
        "away_id": away_id,
        "home": "主队甲",
        "away": "客队乙",
    }


def _plans(*matches):
    return [{"jingcai_date": "2026-11-08", "matches": list(matches)}]


def _rank(db: Path, uid: str):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """SELECT s.rank_home, s.rank_away, s.source_rank, s.as_of_rank, s.recent_json
           FROM stats s JOIN matches m ON m.id=s.match_id WHERE m.match_uid=?""",
        (uid,),
    ).fetchone()
    names = {item[0] for item in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    obs = None
    if "stats_obs" in names:
        obs = conn.execute(
            """SELECT o.as_of, o.source, o.quality, o.payload_json, o.fetched_at
               FROM stats_obs o JOIN matches m ON m.id=o.match_id
               WHERE m.match_uid=? AND o.kind='rank' ORDER BY o.as_of DESC LIMIT 1""",
            (uid,),
        ).fetchone()
    conn.close()
    return row, obs


def test_classify_unavailable_and_empty_table():
    kind, _ = ps.classify_standings(404, {"success": 0, "error": "standings_not_available"})
    assert kind == "unavailable"
    kind, _ = ps.classify_standings(200, {"data": {"error": {"code": "standings_not_available"}}})
    assert kind == "unavailable"
    kind, data = ps.classify_standings(200, {"success": 1, "data": {"season": "26/27", "table": []}})
    assert kind == "table" and data["table"] == []
    assert ps.classify_standings(500, {"success": 0, "error": "temporary"})[0] == "error"
    assert ps.classify_standings(200, {"success": 1, "data": {"season": "2026"}})[0] == "error"


def test_decide_matrix():
    kick = KICK
    empty = ps.RankState()
    assert ps.decide(empty, kick, BEFORE, BEFORE) == "write"
    assert ps.decide(empty, kick, AFTER, AFTER) == "skip"
    assert ps.decide(empty, kick, KICK, BEFORE) == "skip"
    own = ps.RankState(2, 5, ps.SOURCE_RANK, BEFORE, "ok")
    assert ps.decide(own, kick, LATER, LATER) == "refresh"
    assert ps.decide(own, kick, LATER, AFTER) == "skip"
    manual = ps.RankState(9, 8, "manual_seed", BEFORE, None)
    assert ps.decide(manual, kick, LATER, LATER) == "skip"
    unfinished = ps.RankState(None, None, None, None, None)
    assert ps.decide(unfinished, datetime(2026, 6, 1, 20, 0, tzinfo=TZ), BEFORE, BEFORE) == "skip"


def test_snapshot_stores_fetch_time_and_batches_by_league(tmp_path):
    db = _db(tmp_path)
    _add_match(db, "2026-11-08|日001", KICK)
    _add_match(db, "2026-11-08|日002", KICK)
    client = FakeClient({42: (200, _table())})
    report = ps.capture_for_plans(
        client,
        _plans(_match("2026-11-08|日001", KICK), _match("2026-11-08|日002", KICK)),
        db,
        now=BEFORE,
        clock=lambda: BEFORE,
        cache_dir=tmp_path / "cache",
    )
    assert report["status"] == "ok"
    assert report["written"] == 2
    assert report["leagues_fetched"] == 1
    assert len(client.calls) == 1
    assert client.calls[0]["path"] == "/standings"
    assert client.calls[0]["params"]["league"] == 42
    row, obs = _rank(db, "2026-11-08|日001")
    assert (row["rank_home"], row["rank_away"]) == (2, 5)
    assert row["source_rank"] == "5df_standings"
    assert row["as_of_rank"] == ps.iso(BEFORE)
    assert row["as_of_rank"].endswith("+08:00")
    assert "26/27" not in row["as_of_rank"]
    assert row["recent_json"] == "keep"
    assert obs["as_of"] == ps.iso(BEFORE)
    assert obs["fetched_at"] == ps.iso(BEFORE)
    assert obs["source"] == "5df_standings"
    payload = json.loads(obs["payload_json"])
    assert payload["home_row"]["points"] == 17
    assert payload["away_row"]["played"] == 8
    assert payload["table_as_of"] == ps.iso(BEFORE)
    assert payload["season"] == "26/27"


def test_not_applied_after_kickoff_or_when_fetch_crosses_kickoff(tmp_path):
    db = _db(tmp_path)
    _add_match(db, "2026-11-08|日001", KICK)
    client = FakeClient({42: (200, _table(9, 10))})
    report = ps.capture_for_plans(
        client,
        _plans(_match("2026-11-08|日001", KICK)),
        db,
        now=AFTER,
        clock=lambda: AFTER,
        cache_dir=tmp_path / "cache",
    )
    assert client.calls == []
    assert report["written"] == 0
    row, obs = _rank(db, "2026-11-08|日001")
    assert row["rank_home"] is None and row["source_rank"] is None and obs is None

    client2 = FakeClient({42: (200, _table(9, 10))})
    report2 = ps.capture_for_plans(
        client2,
        _plans(_match("2026-11-08|日001", KICK)),
        db,
        now=BEFORE,
        clock=lambda: AFTER,
        cache_dir=tmp_path / "cache2",
    )
    assert len(client2.calls) == 1
    assert report2["written"] == 0
    assert report2["skipped_after_kickoff"] == 1
    row, obs = _rank(db, "2026-11-08|日001")
    assert row["rank_home"] is None and row["source_rank"] is None and obs is None


def test_national_team_unavailable_does_not_invent_rank(tmp_path):
    db = _db(tmp_path)
    _add_match(db, "2026-11-08|日009", KICK)
    client = FakeClient({7: (404, {"success": 0, "error": "standings_not_available"})})
    report = ps.capture_for_plans(
        client,
        _plans(_match("2026-11-08|日009", KICK, league_id=7, home_id=1, away_id=2)),
        db,
        now=BEFORE,
        clock=lambda: BEFORE,
        cache_dir=tmp_path / "cache",
    )
    assert report["unavailable"] == 1
    row, obs = _rank(db, "2026-11-08|日009")
    assert row["rank_home"] is None and row["rank_away"] is None
    assert row["source_rank"] == "5df_standings"
    assert row["as_of_rank"] == ps.iso(BEFORE)
    assert obs["quality"] == "unavailable"
    payload = json.loads(obs["payload_json"])
    assert payload["status"] == "unavailable"
    assert payload["reason"] == "no_standings"
    assert payload["home"] is None and payload["away"] is None


def test_existing_prematch_snapshot_not_overwritten_after_kickoff(tmp_path):
    db = _db(tmp_path)
    _add_match(
        db, "2026-11-08|日001", KICK,
        rank_home=2, rank_away=5, source_rank="5df_standings", as_of_rank=ps.iso(BEFORE),
    )
    client = FakeClient({42: (200, _table(9, 10))})
    ps.capture_for_plans(
        client,
        _plans(_match("2026-11-08|日001", KICK)),
        db,
        now=AFTER,
        clock=lambda: AFTER,
        cache_dir=tmp_path / "cache",
    )
    assert client.calls == []
    row, _ = _rank(db, "2026-11-08|日001")
    assert (row["rank_home"], row["rank_away"], row["as_of_rank"]) == (2, 5, ps.iso(BEFORE))


def test_refresh_before_kickoff_keeps_latest_prematch_observation(tmp_path):
    db = _db(tmp_path)
    _add_match(db, "2026-11-08|日001", KICK)
    cache = tmp_path / "cache"
    first = FakeClient({42: (200, _table(2, 5))})
    ps.capture_for_plans(
        first, _plans(_match("2026-11-08|日001", KICK)), db,
        now=BEFORE, clock=lambda: BEFORE, cache_dir=cache,
    )
    second = FakeClient({42: (200, _table(1, 4))})
    report = ps.capture_for_plans(
        second, _plans(_match("2026-11-08|日001", KICK)), db,
        now=LATER, clock=lambda: LATER, cache_dir=cache,
    )
    assert report["refreshed"] == 1
    assert len(second.calls) == 1
    row, obs = _rank(db, "2026-11-08|日001")
    assert (row["rank_home"], row["rank_away"]) == (1, 4)
    assert row["as_of_rank"] == ps.iso(LATER)
    assert obs["as_of"] == ps.iso(LATER)


def test_manual_rank_is_not_replaced(tmp_path):
    db = _db(tmp_path)
    _add_match(
        db, "2026-11-08|日001", KICK,
        rank_home=9, rank_away=8, source_rank="manual_seed", as_of_rank=ps.iso(BEFORE),
    )
    client = FakeClient({42: (200, _table())})
    ps.capture_for_plans(
        client, _plans(_match("2026-11-08|日001", KICK)), db,
        now=LATER, clock=lambda: LATER, cache_dir=tmp_path / "cache",
    )
    assert client.calls == []
    row, obs = _rank(db, "2026-11-08|日001")
    assert (row["rank_home"], row["rank_away"], row["source_rank"]) == (9, 8, "manual_seed")
    assert obs is None


def test_finished_match_is_not_filled_from_current_table(tmp_path):
    db = _db(tmp_path)
    old_kick = datetime(2026, 6, 1, 20, 0, tzinfo=TZ)
    _add_match(db, "2026-06-01|一001", old_kick)
    _add_match(db, "2026-11-08|日001", KICK)
    client = FakeClient({42: (200, _table())})
    ps.capture_for_plans(
        client,
        _plans(_match("2026-06-01|一001", old_kick), _match("2026-11-08|日001", KICK)),
        db,
        now=BEFORE,
        clock=lambda: BEFORE,
        cache_dir=tmp_path / "cache",
    )
    assert len(client.calls) == 1
    old, old_obs = _rank(db, "2026-06-01|一001")
    assert old["rank_home"] is None and old["source_rank"] is None and old_obs is None
    fresh, _ = _rank(db, "2026-11-08|日001")
    assert (fresh["rank_home"], fresh["rank_away"]) == (2, 5)


def test_refuses_database_outside_replica(tmp_path):
    db = tmp_path / "data" / "app.db"
    db.parent.mkdir()
    db.write_bytes(b"")
    report = ps.capture_for_plans(
        FakeClient({42: (200, _table())}),
        _plans(_match("2026-11-08|日001", KICK)),
        db,
        now=BEFORE,
        clock=lambda: BEFORE,
        cache_dir=tmp_path / "cache",
    )
    assert report["status"] == "skipped"
    assert report["reason"] == "db_refused"
    assert db.read_bytes() == b""


def test_history_probe_does_not_write_rank_columns():
    text = Path(__file__).resolve().parents[1].joinpath("backfill", "af_step2.py").read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))
    assert "rank_home" not in code
    assert "rank_away" not in code
    assert "stats_obs" not in code
    assert "INSERT" not in code.upper()
    assert "standings_unl_2026.json" in code


def test_plan_keeps_league_and_team_ids(tmp_path, monkeypatch):
    import live_capture as lc

    for name in ("D_PLAN", "D_STATE", "D_LOG", "D_CSL"):
        monkeypatch.setattr(lc, name, tmp_path / name.lower())
    kick = datetime(2026, 11, 8, 19, 30, tzinfo=lc.TZ)
    plan = lc.build_plan("2026-11-08", {"data": [{
        "id": 7,
        "league": {"id": 42, "name": "测试联赛"},
        "teams": {"home": {"id": 101, "name": "主队甲"}, "away": {"id": 202, "name": "客队乙"}},
        "kickoff_ts": int(kick.timestamp()),
        "status": "scheduled",
        "lottery": {"jingcailottery": {"number": "周日001", "odds": None}},
    }]})
    match = plan["matches"][0]
    assert match["league_id"] == 42
    assert match["home_id"] == 101
    assert match["away_id"] == 202


def test_tick_runs_prematch_standings(tmp_path, monkeypatch):
    import live_capture as lc

    for name in ("D_ALL", "D_PIN", "D_CSL", "D_PLAN", "D_STAGE", "D_STATE", "D_LOG", "D_SUM", "D_BAK"):
        monkeypatch.setattr(lc, name, tmp_path / name.lower())
    monkeypatch.setattr(lc, "LIVE", tmp_path)
    seen = {}

    def fake_capture(cl, plans, db, **kwargs):
        seen["plans"] = plans
        seen["db"] = db
        return {"status": "ok", "eligible": 0, "written": 0, "refreshed": 0,
                "unavailable": 0, "leagues_fetched": 0, "leagues_cached": 0,
                "skipped_after_kickoff": 0, "errors": 0, "reason": None}

    monkeypatch.setattr(ps, "capture_for_plans", fake_capture)
    monkeypatch.setattr(lc, "ensure_plan", lambda *args, **kwargs: {
        "jingcai_date": "2026-11-08", "matches": [], "targets": [], "n_targets": 0,
    })
    monkeypatch.setattr(lc, "load_plan", lambda day: None)
    monkeypatch.setattr(lc, "due_targets", lambda plans, now: ([], []))
    monkeypatch.setattr(lc, "touch_heartbeat", lambda **kwargs: {"ts": "x", "phase": kwargs.get("phase")})
    monkeypatch.setattr(lc, "health", lambda *args, **kwargs: None)
    marks = []
    monkeypatch.setattr(lc, "_stage_marks_write", lambda stage, **kwargs: marks.append((stage, kwargs.get("status"))))

    class Client:
        calls = 0
        remaining = 40

    lc.tick(Client())
    assert seen["plans"]
    assert "live.prematch_standings" in [stage for stage, _ in marks]
    assert ("live.prematch_standings", "ok") in marks
    assert "live.tick" in [stage for stage, _ in marks]
