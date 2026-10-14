"""球队「待确认」标记（用户规则 2026-10-14：认不出的球队按来源原名写，前端在队名旁加标记，悬停显示「待确认」）。

判断顺序（不改正式库结构，全部只读）：
1. teams 表若将来有 pending_confirm 列，以该列为准；
2. team_aliases.source 以 "_pending_confirm" 结尾的别名所属球队（导入包 build_import_file.py 给待确认球队打的来源标记）；
3. 环境变量 APP_TEAM_PENDING_FILE 指向的导入包 import_rows.csv 或定稿表 teams.csv 里 pending_confirm=true 的常用名；
4. 以上都没有 → false。
"""
from __future__ import annotations

import csv
import os
import sqlite3
from pathlib import Path

PENDING_SOURCE_SUFFIX = "_pending_confirm"
ENV_FILE = "APP_TEAM_PENDING_FILE"


def _names_from_file(path: str | None) -> set[str]:
    if not path:
        return set()
    p = Path(path)
    if not p.is_file():
        return set()
    out: set[str] = set()
    try:
        with open(p, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if (r.get("pending_confirm") or "").strip().lower() != "true":
                    continue
                if r.get("table") not in (None, "", "teams", "team_aliases"):
                    continue
                n = (r.get("name_zh_canonical") or r.get("canonical") or "").strip()
                if n:
                    out.add(n)
    except (OSError, csv.Error, UnicodeDecodeError):
        return set()
    return out


def load_pending(conn: sqlite3.Connection, file_path: str | None = None) -> tuple[set[int], set[str]]:
    """返回 (待确认的 team_id 集合, 待确认的常用名集合)。"""
    ids: set[int] = set()
    names = _names_from_file(file_path if file_path is not None else os.environ.get(ENV_FILE, "").strip())
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(teams)")}
    except sqlite3.Error:
        cols = set()
    try:
        if "pending_confirm" in cols:
            ids |= {int(r[0]) for r in conn.execute("SELECT id FROM teams WHERE pending_confirm IN (1, '1', 'true')")}
        ids |= {int(r[0]) for r in conn.execute(
            "SELECT DISTINCT team_id FROM team_aliases WHERE source LIKE ?", ("%" + PENDING_SOURCE_SUFFIX,))}
        if names:
            q = ",".join("?" * len(names))
            ids |= {int(r[0]) for r in conn.execute(f"SELECT id FROM teams WHERE name_zh_canonical IN ({q})", list(names))}
    except sqlite3.Error:
        pass
    return ids, names


def is_pending(team_id, canonical, pending: tuple[set[int], set[str]]) -> bool:
    ids, names = pending
    if team_id is not None:
        try:
            if int(team_id) in ids:
                return True
        except (TypeError, ValueError):
            pass
    return bool(canonical) and canonical in names
