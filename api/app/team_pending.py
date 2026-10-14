"""球队「待确认」标记（用户规则 2026-10-14：认不出的球队按来源原名写，前端在队名旁加标记，悬停显示「待确认」）。

2026-10-14 分析师改规则（按名称判断，主队、客队各自判断）：
- 队名（包括常用名和来源原名）经规范化后，能在下面三张表里找到，返回 false；
  1. 定稿表 name-standard-2026-10-14/teams.csv（canonical 列和 aliases 列，别名用竖线分隔）；
  2. 提议表 dc_train/team_aliases_proposed.csv（alias 列和 team_canonical 列）；
  3. 种子表：名称导入包 name-standard-2026-10-14/import/import_rows.csv 里 table 为 teams 或 team_aliases 的行
     （name_zh_canonical 列和 alias 列）。赛事种子表 competition_alias_seed.csv 只有赛事名，不含球队，所以不读。
- 三张表都找不到，或者定稿表、种子表里这支队标了 pending_confirm=true，返回 true。
- 任何一张表文件缺失或读不出来，全部返回 true，并记一条警告日志，不抛异常。
- 不再用球队编号是否为空来判断：现在大多数场次还没有导入球队编号，按编号判断会让绝大多数场次都标星。
  名称导入正式库以后，再改回按球队编号判断。
表文件按路径和修改时间缓存，文件改动后自动重读。路径可以用环境变量覆盖。
"""
from __future__ import annotations

import csv
import logging
import os
import re
import sqlite3
import unicodedata
from pathlib import Path

log = logging.getLogger(__name__)

_RESEARCH = Path("/workspace/odds-data/research")
DEFAULT_PATHS = {
    "final": _RESEARCH / "name-standard-2026-10-14" / "teams.csv",
    "proposed": _RESEARCH / "dc_train" / "team_aliases_proposed.csv",
    "seed": _RESEARCH / "name-standard-2026-10-14" / "import" / "import_rows.csv",
}
ENV_PATHS = {"final": "APP_TEAM_FINAL_FILE", "proposed": "APP_TEAM_PROPOSED_FILE", "seed": "APP_TEAM_SEED_FILE"}

_cache: dict[tuple, "NameIndex"] = {}


def norm(name) -> str:
    """去首尾空格、全角转半角（NFKC）、连续空白合并为一个空格、拉丁字母不分大小写。"""
    if name is None:
        return ""
    s = unicodedata.normalize("NFKC", str(name)).strip()
    return re.sub(r"\s+", " ", s).casefold()


def _true(v) -> bool:
    return norm(v) in ("true", "1", "yes")


class NameIndex:
    def __init__(self, known: set[str], pending: set[str], missing: list[str]):
        self.known, self.pending, self.missing = known, pending, missing

    def reason(self, *names) -> str | None:
        """返回 None 表示不待确认；否则返回原因：table_missing、final_pending_confirm、not_found。"""
        if self.missing:
            return "table_missing"
        ns = {norm(n) for n in names if norm(n)}
        if ns & self.pending:
            return "final_pending_confirm"
        if ns & self.known:
            return None
        return "not_found"

    def is_pending(self, *names) -> bool:
        return self.reason(*names) is not None


def _paths(paths: dict | None) -> dict[str, Path]:
    out = {}
    for k, d in DEFAULT_PATHS.items():
        v = (paths or {}).get(k) or os.environ.get(ENV_PATHS[k], "").strip()
        out[k] = Path(v) if v else d
    return out


def _rows(p: Path):
    with open(p, encoding="utf-8-sig", newline="") as f:
        yield from csv.DictReader(f)


def load_index(paths: dict | None = None) -> NameIndex:
    ps = _paths(paths)
    key = []
    for k, p in ps.items():
        try:
            key.append((k, str(p), p.stat().st_mtime_ns))
        except OSError:
            key.append((k, str(p), None))
    key = tuple(key)
    if key in _cache:
        return _cache[key]
    known: set[str] = set()
    pending: set[str] = set()
    missing: list[str] = []
    for k, p in ps.items():
        try:
            for r in _rows(p):
                if k == "final":
                    names = [r.get("canonical")] + (r.get("aliases") or "").split("|")
                    flag = _true(r.get("pending_confirm"))
                elif k == "proposed":
                    names, flag = [r.get("alias"), r.get("team_canonical")], False
                else:
                    if r.get("table") not in ("teams", "team_aliases"):
                        continue
                    names = [r.get("name_zh_canonical"), r.get("alias")]
                    flag = _true(r.get("pending_confirm"))
                ns = {norm(n) for n in names if norm(n)}
                (pending if flag else known).update(ns)
        except (OSError, csv.Error, UnicodeDecodeError) as e:
            log.warning("球队名称表读取失败，全部球队按待确认处理：%s（%s）", p, e)
            missing.append(k)
    idx = NameIndex(known - pending, pending, missing)
    _cache.clear()
    _cache[key] = idx
    return idx


def load_pending(conn: sqlite3.Connection | None = None, paths: dict | None = None) -> NameIndex:
    """兼容旧调用：conn 不再使用（不按库里的球队编号判断）。"""
    return load_index(paths)


def is_pending(team_id, canonical, pending: NameIndex | None, display=None) -> bool:
    """team_id 保留参数位置但不参与判断；名称导入正式库以后再改回按球队编号判断。"""
    idx = pending if isinstance(pending, NameIndex) else load_index()
    return idx.is_pending(canonical, display)


def reason(team_id, canonical, pending: NameIndex | None, display=None) -> str | None:
    idx = pending if isinstance(pending, NameIndex) else load_index()
    return idx.reason(canonical, display)
