"""赛事「待确认」标记。

与球队同一套常用名规则（2026-10-18）：常用名已经确定则不标星，尚未确定才标星。
不再用「有来源名且 league_id 为空」作为标星条件。来源名已经对应到定稿常用名或正式库别名时，
即使赛事编号还没填，也不标星。没有来源名时不标星。

全量赛事名册读入之后，名册和正式库里都没有的来源名标为未映射。
名称表里标了 pending_confirm 的，即使已经写了赛事编号，仍然标星。
"""
from __future__ import annotations

from app import team_pending as tp


def _source_present(name) -> bool:
    """来源名去掉首尾空白后仍有内容，才算有赛事来源名。"""
    return bool(tp.norm(name))


def _id_empty(league_id) -> bool:
    """赛事编号为空：缺失、None，或去掉空白后的空字符串。数字 0 不算空。"""
    if league_id is None:
        return True
    if isinstance(league_id, str) and league_id.strip() == "":
        return True
    return False


def is_pending(league_id, source_name, index: tp.NameIndex | None = None, canonical=None) -> bool:
    """常用名尚未确定时返回 True。

    index 缺省时使用包内赛事名册，并在调用方已经并入正式库时沿用那份索引。
    """
    idx = index if isinstance(index, tp.NameIndex) else tp.load_league_index()
    mapped = not _id_empty(league_id)
    return idx.is_pending(source_name, canonical, mapped=mapped)


def from_row(row, index: tp.NameIndex | None = None) -> bool:
    """从比赛行读取 league_id、competition_name，以及可选的 league_canonical。缺列按空处理。"""
    if hasattr(row, "keys"):
        keys = row.keys()
        league_id = row["league_id"] if "league_id" in keys else None
        source_name = row["competition_name"] if "competition_name" in keys else None
        canonical = row["league_canonical"] if "league_canonical" in keys else None
    elif isinstance(row, dict):
        league_id = row.get("league_id")
        source_name = row.get("competition_name")
        canonical = row.get("league_canonical")
    else:
        league_id = getattr(row, "league_id", None)
        source_name = getattr(row, "competition_name", None)
        canonical = getattr(row, "league_canonical", None)
    return is_pending(league_id, source_name, index, canonical)
