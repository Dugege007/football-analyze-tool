"""赛事「待确认」标记。

待定池尚未合入。本模块因此不读待定池表，不写数据库，也不改表结构。
接口在返回比赛时计算布尔字段 league_pending_confirm，规则与队名待确认字段的放置方式一致，计算口径按合入前的编号规则：
有赛事来源名，并且赛事编号为空，返回 true。
没有赛事来源名，返回 false。
赛事编号已经填写，返回 false。
仓库里的赛事编号列是 matches.league_id。没有 competition_id 列。
赛事来源名是 matches.competition_name。数据表接口把它放在与 league_canonical 同级的 league 字段。
"""
from __future__ import annotations


def _source_present(name) -> bool:
    """来源名去掉首尾空白后仍有内容，才算有赛事来源名。"""
    if name is None:
        return False
    return str(name).strip() != ""


def _id_empty(league_id) -> bool:
    """赛事编号为空：缺失、None，或去掉空白后的空字符串。数字 0 不算空。"""
    if league_id is None:
        return True
    if isinstance(league_id, str) and league_id.strip() == "":
        return True
    return False


def is_pending(league_id, source_name) -> bool:
    """有赛事来源名且赛事编号为空时返回 True，其余情况返回 False。"""
    if not _source_present(source_name):
        return False
    return _id_empty(league_id)


def from_row(row) -> bool:
    """从比赛行读取 league_id 与 competition_name 后计算。缺列视为编号为空、来源名为空。"""
    if hasattr(row, "keys"):
        keys = row.keys()
        league_id = row["league_id"] if "league_id" in keys else None
        source_name = row["competition_name"] if "competition_name" in keys else None
    elif isinstance(row, dict):
        league_id = row.get("league_id")
        source_name = row.get("competition_name")
    else:
        league_id = getattr(row, "league_id", None)
        source_name = getattr(row, "competition_name", None)
    return is_pending(league_id, source_name)
