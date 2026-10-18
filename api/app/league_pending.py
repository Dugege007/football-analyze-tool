"""赛事「待确认」标记。

与球队共用 docs/schema/v2_0-name-canonical-and-star-rules.md 的同一套机制。
已经升格的常用名不标星。临时名、待确认和尚未升格的名称继续标星。
league_id 为空或已填都不单独决定星号。没有来源名、也没有规范名时不标星。
"""
from __future__ import annotations

from app import team_pending as tp


def is_pending(league_id, source_name, index: tp.NameIndex | None = None, canonical=None) -> bool:
    """常用名尚未升格时返回 True。

    league_id 保留给调用方，不参与判断。index 缺省时使用包内赛事名册。
    """
    _ = league_id
    idx = index if isinstance(index, tp.NameIndex) else tp.load_league_index()
    return idx.is_pending(source_name, canonical)


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
