"""2026-10-13：shared_api_yield 只按名字读 .env，不把整份 .env 注入 os.environ。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
YIELD = ROOT / "app" / "shared_api_yield.py"
HIST = REPO / "scripts" / "backfill" / "5df-multibook-history-queue" / "hist_yield.py"


def _fake_repo(tmp_path: Path, env_text: str) -> Path:
    """临时仓库：api/app/shared_api_yield.py + portable_lock.py + hist_yield.py + 根 .env。"""
    app = tmp_path / "repo" / "api" / "app"
    app.mkdir(parents=True)
    for f in ("shared_api_yield.py", "portable_lock.py"):
        (app / f).write_text((ROOT / "app" / f).read_text(encoding="utf-8"), encoding="utf-8")
    hd = tmp_path / "repo" / "scripts" / "backfill" / "5df-multibook-history-queue"
    hd.mkdir(parents=True)
    (hd / "hist_yield.py").write_text(HIST.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "repo" / ".env").write_text(env_text, encoding="utf-8")
    return tmp_path / "repo"


def _run(repo: Path, code: str, extra_env: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("FAT_DISABLE_DOTENV", "ODDS_DATA_DIR", "DUAL_WRITE", "APP_DB_PATH",
                        "SHARED_API_YIELD_MAX_WAIT_SEC", "SHARED_API_YIELD_LEDGER", "SHARED_API_YIELD_LOG")}
    env.update(extra_env or {})
    out = subprocess.run([sys.executable, "-c", code], cwd=repo, env=env, capture_output=True, text=True,
                         check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


CODE = """
import json, os, sys
sys.path.insert(0, 'api/app'); sys.path.insert(0, 'scripts/backfill/5df-multibook-history-queue')
import shared_api_yield as Y, hist_yield as H
print(json.dumps({'ledger': str(Y.LEDGER), 'log': str(Y.LOG), 'cap': Y.DEFAULT_MAX_WAIT_SEC,
                  'live': str(H.LIVE),
                  'env': {k: os.environ.get(k) for k in ('ODDS_DATA_DIR', 'DUAL_WRITE', 'APP_DB_PATH',
                                                         'SHARED_API_YIELD_MAX_WAIT_SEC')}}))
"""


def test_dotenv_values_used_but_not_injected(tmp_path):
    odds = tmp_path / "odds"
    repo = _fake_repo(tmp_path, f"ODDS_DATA_DIR={odds}\nDUAL_WRITE=0\nAPP_DB_PATH=/x/app.db\n"
                                "SHARED_API_YIELD_MAX_WAIT_SEC=120\n")
    d = _run(repo, CODE)
    assert d["ledger"] == str(odds / "5dollar" / "backfill_yield_ledger.json")
    assert d["log"] == str(odds / "5dollar" / "backfill_yield.jsonl")
    assert d["cap"] == 120.0
    assert d["live"] == str(odds / "5dollar" / "live")  # hist_yield 也只读 .env
    assert d["env"] == {"ODDS_DATA_DIR": None, "DUAL_WRITE": None, "APP_DB_PATH": None,
                        "SHARED_API_YIELD_MAX_WAIT_SEC": None}


def test_process_env_wins_over_dotenv(tmp_path):
    repo = _fake_repo(tmp_path, f"ODDS_DATA_DIR={tmp_path / 'from_dotenv'}\n")
    d = _run(repo, CODE, {"ODDS_DATA_DIR": str(tmp_path / "from_env"), "SHARED_API_YIELD_MAX_WAIT_SEC": "45"})
    assert d["ledger"].startswith(str(tmp_path / "from_env"))
    assert d["live"] == str(tmp_path / "from_env" / "5dollar" / "live")
    assert d["cap"] == 45.0


def test_disable_dotenv_flag_respected(tmp_path):
    repo = _fake_repo(tmp_path, f"ODDS_DATA_DIR={tmp_path / 'from_dotenv'}\n")
    d = _run(repo, CODE, {"FAT_DISABLE_DOTENV": "1"})
    assert d["ledger"] == str(repo / "data" / "odds-data" / "5dollar" / "backfill_yield_ledger.json")
    assert d["live"] == str(repo / "data" / "odds-data" / "5dollar" / "live")
    assert d["cap"] == 300.0


def test_placeholder_values_ignored(tmp_path):
    repo = _fake_repo(tmp_path, "ODDS_DATA_DIR=YOUR_ODDS_DATA_DIR\n")
    d = _run(repo, CODE)
    assert d["ledger"] == str(repo / "data" / "odds-data" / "5dollar" / "backfill_yield_ledger.json")
    assert d["live"] == str(repo / "data" / "odds-data" / "5dollar" / "live")
