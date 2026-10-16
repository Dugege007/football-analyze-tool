"""build_jc_match_rows.py：副本库路径必须来自路径配置 _V2D3_DB，不得再硬编码归档库字面量。"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_jc_match_rows.py"
ARCHIVE_SUFFIX = ("data", "v2d3", "app.db")


def _load(tmp_path, env):
    """在子进程里按给定环境导入脚本，打印 REPLICA_DB / _V2D3_DB / PROD_DB / _APP_DB。"""
    code = (
        "import importlib.util,sys;"
        f"s=importlib.util.spec_from_file_location('m',{str(SCRIPT)!r});m=importlib.util.module_from_spec(s);"
        "s.loader.exec_module(m);"
        "print(m.REPLICA_DB);print(m._V2D3_DB);print(m.PROD_DB);print(m._APP_DB.resolve())"
    )
    base = {k: v for k, v in os.environ.items()
            if k not in ("V2D3_DB_PATH", "APP_DB_PATH", "FORMAL_MERGED", "MA_API_ROOT")}
    base.update({"FAT_DISABLE_DOTENV": "1", "MA_API_ROOT": str(tmp_path)})
    base.update(env)
    r = subprocess.run([sys.executable, "-c", code], env=base, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return [Path(x) for x in r.stdout.split("\n")[:4]]


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
