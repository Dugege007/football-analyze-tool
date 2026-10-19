"""build_jc_match_rows.py：副本库路径必须来自路径配置 _V2D3_DB，不得再硬编码归档库字面量。

FORMAL_MERGED 除 MA_API_ROOT/data 外，也认正式库 APP_DB_PATH 同目录的 flag。
"""
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_jc_match_rows.py"
WATCH = Path(__file__).resolve().parents[2] / "ops" / "ensure_jc_match_rows_watch.sh"
ARCHIVE_SUFFIX = ("data", "v2d3", "app.db")
_JC = None

_GUARD_SNIPPET = (
    "try:\n"
    "    print('ALLOW ' + str(m.guard(m.PROD_DB)))\n"
    "except SystemExit as e:\n"
    "    print('REFUSE ' + str(e))\n"
)


def _base_env(tmp_path, env):
    base = {k: v for k, v in os.environ.items()
            if k not in ("V2D3_DB_PATH", "APP_DB_PATH", "FORMAL_MERGED", "MA_API_ROOT",
                         "DUAL_WRITE_ODDS_ASIAN", "JC_MATCH_ROWS_HEARTBEAT_STALE_SEC")}
    base.update({"FAT_DISABLE_DOTENV": "1", "DUAL_WRITE_ODDS_ASIAN": "0", "MA_API_ROOT": str(tmp_path)})
    base.update(env)
    return base


def _invoke(tmp_path, env, snippet):
    code = (
        "import importlib.util\n"
        f"s=importlib.util.spec_from_file_location('m',{str(SCRIPT)!r})\n"
        "m=importlib.util.module_from_spec(s)\n"
        "s.loader.exec_module(m)\n"
        "print(int(bool(m._FORMAL_MERGED)))\n"
        "print(m.PROD_DB)\n"
        "print(m.REPLICA_DB)\n"
        "print(m._V2D3_DB)\n"
        "print(m._APP_DB.resolve())\n"
        + snippet
    )
    return subprocess.run([sys.executable, "-c", code], env=_base_env(tmp_path, env),
                          capture_output=True, text=True, timeout=60)


def _load(tmp_path, env):
    """在子进程里按给定环境导入脚本，打印 REPLICA_DB / _V2D3_DB / PROD_DB / _APP_DB。"""
    r = _invoke(tmp_path, env, "")
    assert r.returncode == 0, r.stderr
    lines = [x for x in r.stdout.split("\n") if x != ""]
    # 0 formal, 1 prod, 2 replica, 3 v2d3 cfg, 4 app resolved
    return [Path(lines[2]), Path(lines[3]), Path(lines[1]), Path(lines[4])]


def _load_full(tmp_path, env):
    r = _invoke(tmp_path, env, "")
    assert r.returncode == 0, r.stderr
    lines = [x for x in r.stdout.split("\n") if x != ""]
    merged = lines[0] == "1"
    return Path(lines[2]), Path(lines[3]), Path(lines[1]), Path(lines[4]), merged


def _jc():
    """进程内导入，只用于不依赖库路径的纯函数。"""
    global _JC
    if _JC is None:
        spec = importlib.util.spec_from_file_location("build_jc_match_rows_watch", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _JC = mod
    return _JC


def test_default_replica_equals_config(tmp_path):
    rep, cfg, _, _ = _load(tmp_path, {})
    assert rep == cfg
    assert rep == tmp_path / "data" / "v2d3" / "app.db"  # 未合并默认值


def test_formal_merged_env_points_to_formal_db(tmp_path):
    rep, cfg, prod, app = _load(tmp_path, {"FORMAL_MERGED": "1"})
    assert rep == cfg == app == prod
    assert rep.parts[-3:] != ARCHIVE_SUFFIX


def test_formal_merged_flag_file(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "FORMAL_MERGED.flag").write_text("")
    rep, _, _, app = _load(tmp_path, {})
    assert rep == app and rep == tmp_path / "data" / "app.db"


def test_v2d3_env_override_wins(tmp_path):
    custom = tmp_path / "custom" / "x.db"
    rep, cfg, _, _ = _load(tmp_path, {"V2D3_DB_PATH": str(custom), "FORMAL_MERGED": "1"})
    assert rep == cfg == custom


def test_app_db_env_override_moves_prod_and_replica(tmp_path):
    custom = tmp_path / "elsewhere" / "formal.db"
    rep, _, prod, _ = _load(tmp_path, {"FORMAL_MERGED": "1", "APP_DB_PATH": str(custom)})
    assert rep == prod == custom


def test_no_archive_literal_in_source():
    src = SCRIPT.read_text(encoding="utf-8")
    assert "REPLICA_DB = _V2D3_DB" in src
    assert 'REPLICA_DB = ROOT / "data" / "v2d3"' not in src


def test_formal_merged_true_when_only_app_db_parent_has_flag(tmp_path):
    """没有 MA_API_ROOT/data 布局时，只要正式库同目录有 flag 就算已合并。"""
    assert not (tmp_path / "data").exists()
    formal = tmp_path / "formal-data"
    formal.mkdir()
    (formal / "FORMAL_MERGED.flag").write_text("")
    app = formal / "app.db"
    rep, cfg, prod, resolved, merged = _load_full(tmp_path, {"APP_DB_PATH": str(app)})
    assert merged is True
    assert rep == cfg == prod == resolved == app.resolve()
    assert not (tmp_path / "data" / "FORMAL_MERGED.flag").exists()


def test_formal_merged_false_when_neither_flag(tmp_path):
    formal = tmp_path / "formal-data"
    formal.mkdir()
    app = formal / "app.db"
    rep, cfg, prod, _, merged = _load_full(tmp_path, {"APP_DB_PATH": str(app)})
    assert merged is False
    assert rep == cfg == tmp_path / "data" / "v2d3" / "app.db"
    assert prod == app.resolve()


def test_guard_allows_formal_write_when_flag_beside_app_db(tmp_path):
    formal = tmp_path / "formal-data"
    formal.mkdir()
    (formal / "FORMAL_MERGED.flag").write_text("")
    app = formal / "app.db"
    r = _invoke(tmp_path, {"APP_DB_PATH": str(app)}, _GUARD_SNIPPET)
    assert r.returncode == 0, r.stderr
    lines = [x for x in r.stdout.split("\n") if x != ""]
    assert lines[0] == "1"
    assert lines[5].startswith("ALLOW ")
    assert Path(lines[5].split(" ", 1)[1]) == Path(lines[1]) == app.resolve()


def test_guard_refuses_formal_write_when_flag_absent(tmp_path):
    formal = tmp_path / "formal-data"
    formal.mkdir()
    app = formal / "app.db"
    r = _invoke(tmp_path, {"APP_DB_PATH": str(app)}, _GUARD_SNIPPET)
    assert r.returncode == 0, r.stderr
    lines = [x for x in r.stdout.split("\n") if x != ""]
    assert lines[0] == "0"
    assert lines[5].startswith("REFUSE ")
    assert "只允许 v2d3" in lines[5]


def test_run_and_run_error_logs_include_db_and_formal_merged():
    src = SCRIPT.read_text(encoding="utf-8")
    assert 'log("run", mode=rep["mode"], db=str(db), formal_merged=bool(_FORMAL_MERGED)' in src
    assert 'log("run_error", error=str(e)[:500], db=str(REPLICA_DB), formal_merged=bool(_FORMAL_MERGED))' in src


def test_watch_decision_restarts_only_when_held_and_stale():
    m = _jc()
    assert m.watch_decision(True, 10, 1200) == "healthy"
    assert m.watch_decision(True, 1200, 1200) == "healthy"
    assert m.watch_decision(True, 1201, 1200) == "restart"
    assert m.watch_decision(True, None, 1200) == "restart"
    assert m.watch_decision(False, 1, 1200) == "start"
    assert m.watch_decision(False, None, 1200) == "start"


def test_heartbeat_age_missing_and_stale(tmp_path):
    m = _jc()
    assert m.heartbeat_age_sec(tmp_path / "nope.json") is None
    now = datetime(2026, 10, 19, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    path = tmp_path / "heartbeat.json"
    path.write_text(json.dumps({"ts": "2026-10-19T11:00:00+08:00"}) + "\n", encoding="utf-8")
    assert m.heartbeat_age_sec(path, now) == 3600


def test_watch_script_calls_ensure_watch_and_does_not_enable_live_capture_create():
    text = WATCH.read_text(encoding="utf-8")
    assert "ensure-watch" in text
    assert "LIVE_CAPTURE_CREATE_MATCHES=1" not in text
    assert "LIVE_CAPTURE_CREATE_MATCHES" in text  # 文档说明不要靠这个开关补场
