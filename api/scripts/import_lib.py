"""Shared helpers for seeding / monthly JSON import into match-analysis SQLite."""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path
from typing import Any, TextIO


def match_uid(match: dict) -> str:
    date = match["date"]
    # Infer scope: explicit scope wins; else jc present → jingcai
    scope = match.get("scope")
    if not scope:
        scope = "jingcai" if match.get("jc") else "extra"
    if scope == "jingcai":
        jc = match.get("jc") or {}
        jc_id = jc.get("id")
        if not jc_id:
            raise ValueError(f"jingcai match missing jc.id: {match}")
        return f"{date}|{jc_id}"
    home = match["teams"]["home"]
    away = match["teams"]["away"]
    return f"{date}|extra|{home}|{away}"


def infer_scope(match: dict) -> str:
    scope = match.get("scope")
    if scope in ("jingcai", "extra"):
        return scope
    return "jingcai" if match.get("jc") else "extra"


def parse_jc_no(jc: dict | None) -> int | None:
    if not jc:
        return None
    no = jc.get("no")
    if no is None:
        return None
    try:
        return int(str(no))
    except ValueError:
        digits = "".join(c for c in str(no) if c.isdigit())
        return int(digits) if digits else None


def asian_phase_to_row(phase_val):
    """Return (handicap, home_water, away_water) from JSON phase value."""
    if phase_val is None:
        return None
    if isinstance(phase_val, (int, float)):
        return (float(phase_val), None, None)
    if isinstance(phase_val, dict):
        # Skip all-null phase dicts (seen in william for some monthly rows)
        if all(phase_val.get(k) is None for k in ("handicap", "home_water", "away_water")):
            return None
        return (
            float(phase_val["handicap"]) if phase_val.get("handicap") is not None else None,
            float(phase_val["home_water"]) if phase_val.get("home_water") is not None else None,
            float(phase_val["away_water"]) if phase_val.get("away_water") is not None else None,
        )
    raise TypeError(f"unsupported asian phase value: {phase_val!r}")


TIER_WATER_BOOKS = ("crown", "william")


def _ensure_v1_7_columns(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(odds_asian)")}
    if "water_src" not in cols:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from app.db import SCHEMA_DIR, apply_sql_idempotent
        apply_sql_idempotent(conn, SCHEMA_DIR / "v1_7_water_tier_midpoint.sql")


def asian_water_fields(book: str, phase_val: Any, book_val: Any, home_water: Any, away_water: Any):
    """v1.7：决定入库水位与来源标记。

    - macau：不动（通常无水位）→ water_src=NULL（有水位则 actual）。
    - crown/william：旧手工 JSON 存的是档位 t → 中点水位 w=0.70+0.05t，water_src=tier_midpoint，
      t≤0 / t≥10 → water_censored=1；原始档位进 extras_json。
      若 phase dict 或 book dict 显式带 "water_src": "actual"（API 来的真实水位）→ 原样存，water_src=actual。
    - 其它书：有水位 → actual。
    Returns (home_water, away_water, water_src, water_censored, extras_json)。
    """
    if home_water is None and away_water is None:
        return None, None, None, None, None
    hint = None
    if isinstance(phase_val, dict):
        hint = phase_val.get("water_src")
    if hint is None and isinstance(book_val, dict):
        hint = book_val.get("water_src")
    if book in TIER_WATER_BOOKS and hint != "actual":
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from app.water import convert_tier_pair
        cv = convert_tier_pair(home_water, away_water)
        return (cv["home_water"], cv["away_water"], cv["water_src"], cv["water_censored"],
                json.dumps(cv["extras"], ensure_ascii=False))
    return home_water, away_water, "actual", 0, None


def _json_or_none(val: Any) -> str | None:
    if val is None:
        return None
    return json.dumps(val, ensure_ascii=False)


def _has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    return any(r[1] == col for r in conn.execute(f"PRAGMA table_info({table})"))


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def normalize_name(name: str | None) -> str | None:
    """轻量规范化：NFKC + trim + 折叠空白；不改译名。"""
    if name is None:
        return None
    n = unicodedata.normalize("NFKC", str(name)).strip()
    n = re.sub(r"\s+", " ", n)
    return n or None


def _lookup_team_id(conn: sqlite3.Connection, key: str) -> int | None:
    row = conn.execute(
        "SELECT team_id FROM team_aliases WHERE alias = ?", (key,)
    ).fetchone()
    if row:
        return int(row["team_id"] if isinstance(row, sqlite3.Row) else row[0])
    row = conn.execute(
        "SELECT id FROM teams WHERE name_zh_canonical = ?", (key,)
    ).fetchone()
    if row:
        return int(row["id"] if isinstance(row, sqlite3.Row) else row[0])
    return None


def _lookup_league_id(conn: sqlite3.Connection, key: str) -> int | None:
    row = conn.execute(
        "SELECT league_id FROM league_aliases WHERE alias = ?", (key,)
    ).fetchone()
    if row:
        return int(row["league_id"] if isinstance(row, sqlite3.Row) else row[0])
    row = conn.execute(
        "SELECT id FROM leagues WHERE name_zh_canonical = ?", (key,)
    ).fetchone()
    if row:
        return int(row["id"] if isinstance(row, sqlite3.Row) else row[0])
    return None


# 单个竞彩日里，有名称但 home_team_id / away_team_id / league_id 为空的槽位占比超过该值即告警。
# 查询正文见 docs/schema/v2_0_entity_id_null_rate.sql。
NULL_ENTITY_ID_RATE_ALERT = 0.05

_ENTITY_SLOTS = (
    ("home_team", "home_team_id", "team"),
    ("away_team", "away_team_id", "team"),
    ("competition_name", "league_id", "competition"),
)


class EntityIdGateError(RuntimeError):
    """一批写入会留下成片空身份编号，或导入批次号全空。"""


def lookup_entity_id(conn: sqlite3.Connection, kind: str, name: str | None) -> int | None:
    """只查不建。kind 为 team 或 competition。

    先按规范化名、再按原文查别名表和规范名。不插入别名，不新建球队或赛事。
    """
    if kind not in ("team", "competition"):
        raise ValueError("kind 只能是 team 或 competition")
    norm = normalize_name(name)
    if not norm:
        return None
    table = "teams" if kind == "team" else "leagues"
    alias_table = "team_aliases" if kind == "team" else "league_aliases"
    if not _has_table(conn, table) or not _has_table(conn, alias_table):
        return None
    raw = (str(name).strip() if name is not None else "") or None
    lookup = _lookup_team_id if kind == "team" else _lookup_league_id
    for key in dict.fromkeys([norm, raw] if raw and raw != norm else [norm]):
        found = lookup(conn, key)
        if found is not None:
            return found
    return None


def _as_mapping(row: Any) -> dict:
    if isinstance(row, dict):
        return row
    if isinstance(row, sqlite3.Row):
        return {key: row[key] for key in row.keys()}
    raise TypeError("实体编号门禁需要 dict 或 sqlite3.Row")


def assess_entity_id_batch(rows: list[Any]) -> dict:
    """按竞彩日统计有名称但身份编号为空的槽位。"""
    by_date: dict[str, dict[str, float | int]] = {}
    named = nulls = 0
    batch_ids: list[Any] = []
    for raw in rows:
        row = _as_mapping(raw)
        date = row.get("jingcai_date") or ""
        bucket = by_date.setdefault(date, {"named_slots": 0, "null_slots": 0, "rate": 0.0})
        for name_col, id_col, _kind in _ENTITY_SLOTS:
            if not normalize_name(row.get(name_col)):
                continue
            bucket["named_slots"] = int(bucket["named_slots"]) + 1
            named += 1
            if row.get(id_col) is None:
                bucket["null_slots"] = int(bucket["null_slots"]) + 1
                nulls += 1
        batch_ids.append(row.get("import_batch_id"))
    for bucket in by_date.values():
        slots = int(bucket["named_slots"])
        bucket["rate"] = (int(bucket["null_slots"]) / slots) if slots else 0.0
    return {
        "by_date": by_date,
        "named_slots": named,
        "null_slots": nulls,
        "rate": (nulls / named) if named else 0.0,
        "all_import_batch_id_null": bool(rows) and all(value is None for value in batch_ids),
        "rows": len(rows),
    }


def enforce_entity_id_gate(
    rows: list[Any],
    *,
    label: str,
    require_import_batch_id: bool = False,
    fail_on_mass_null: bool = False,
    stream: TextIO | None = None,
) -> dict:
    """空编号占比超过 5% 时向 stderr 告警。

    导入批次若 import_batch_id 全空，或有名称的槽位全部为空，则拒绝静默提交。
    竞彩场次构建对尚未进入别名表的名称只告警：场次行仍要留下，未匹配清单本来就允许空编号。
    """
    out = stream or sys.stderr
    stats = assess_entity_id_batch(rows)
    alerts: list[str] = []
    for date, info in stats["by_date"].items():
        if int(info["named_slots"]) and float(info["rate"]) > NULL_ENTITY_ID_RATE_ALERT:
            shown = date or "（无日期）"
            msg = (
                f"WARN entity-id: {label} 竞彩日 {shown} "
                f"有名称但身份编号为空 {int(info['null_slots'])}/{int(info['named_slots'])} "
                f"（{float(info['rate']):.1%}），超过 {NULL_ENTITY_ID_RATE_ALERT:.0%}。"
                "规范名会缺。回填脚本：api/scripts/backfill_match_entity_ids.py。"
            )
            print(msg, file=out)
            alerts.append(msg)
    if require_import_batch_id and rows and stats["all_import_batch_id_null"]:
        raise EntityIdGateError(
            f"REFUSE: {label} 本批 {len(rows)} 场 matches.import_batch_id 全为空，拒绝静默写入。"
        )
    if (
        fail_on_mass_null
        and int(stats["named_slots"]) >= 3
        and int(stats["null_slots"]) == int(stats["named_slots"])
    ):
        raise EntityIdGateError(
            f"REFUSE: {label} 本批有名称的身份槽全部为空"
            f"（{int(stats['null_slots'])}/{int(stats['named_slots'])}），"
            "拒绝静默写入空的 home_team_id/away_team_id/league_id。"
        )
    stats["alerts"] = alerts
    return stats


def _slot_value(row: Any, key: str, index: int) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (IndexError, KeyError, TypeError):
        return row[index]


def fill_null_entity_ids(
    conn: sqlite3.Connection,
    match_id: int,
    *,
    home_team: str | None,
    away_team: str | None,
    competition_name: str | None,
    jingcai_date: str | None = None,
    label: str = "ingest",
    fail_on_mass_null: bool = False,
    require_import_batch_id: bool = False,
) -> dict:
    """写入前的身份解析：create=False，只填仍为空的槽，然后做空编号门禁。"""
    names = {
        "home_team": home_team,
        "away_team": away_team,
        "competition_name": competition_name,
    }
    if not _has_column(conn, "matches", "home_team_id"):
        msg = (
            f"WARN entity-id: {label} matches 缺少 home_team_id/away_team_id/league_id，"
            "本批无法写入身份编号。"
        )
        print(msg, file=sys.stderr)
        return {"alerts": [msg], "skipped": "no_columns"}
    current = conn.execute(
        "SELECT home_team_id, away_team_id, league_id FROM matches WHERE id = ?",
        (match_id,),
    ).fetchone()
    if current is None:
        raise EntityIdGateError(f"REFUSE: {label} 找不到 matches.id={match_id}，无法做身份编号门禁。")
    resolved: dict[str, int | None] = {}
    for index, (name_col, id_col, kind) in enumerate(_ENTITY_SLOTS):
        existing = _slot_value(current, id_col, index)
        if existing is not None:
            resolved[id_col] = int(existing)
            continue
        found = lookup_entity_id(conn, kind, names[name_col])
        resolved[id_col] = found
        if found is None:
            continue
        sql = f"UPDATE matches SET {id_col} = ?"
        if _has_column(conn, "matches", "updated_at"):
            sql += ", updated_at = datetime('now')"
        sql += f" WHERE id = ? AND {id_col} IS NULL"
        conn.execute(sql, (found, match_id))
    gate_row = {
        **names,
        **resolved,
        "jingcai_date": jingcai_date,
        "import_batch_id": None,
    }
    return enforce_entity_id_gate(
        [gate_row],
        label=label,
        require_import_batch_id=require_import_batch_id,
        fail_on_mass_null=fail_on_mass_null,
    )


def assert_batch_entity_ids(
    conn: sqlite3.Connection,
    batch_id: int,
    expected_rows: int,
    *,
    label: str,
) -> dict:
    """月度导入和演示种子写完一批后调用：批次号全空或身份编号全空则拒绝提交。"""
    if expected_rows <= 0:
        return {"rows": 0, "alerts": [], "named_slots": 0, "null_slots": 0, "rate": 0.0}
    if not _has_column(conn, "matches", "import_batch_id"):
        raise EntityIdGateError(
            f"REFUSE: {label} matches 没有 import_batch_id，无法确认本批已挂上批次。"
        )
    if not _has_column(conn, "matches", "home_team_id"):
        raise EntityIdGateError(
            f"REFUSE: {label} matches 没有身份编号列，拒绝整批空编号入库。"
        )
    rows = conn.execute(
        """
        SELECT jingcai_date, home_team, away_team, competition_name,
               home_team_id, away_team_id, league_id, import_batch_id
        FROM matches
        WHERE import_batch_id = ?
        """,
        (batch_id,),
    ).fetchall()
    if not rows:
        raise EntityIdGateError(
            f"REFUSE: {label} 计划写入 {expected_rows} 场，"
            f"但没有任何比赛挂上 import_batch_id={batch_id}。"
            "这表示本批 import_batch_id 全为空，拒绝静默提交。"
        )
    if len(rows) < expected_rows:
        msg = (
            f"WARN entity-id: {label} 计划写入 {expected_rows} 场，"
            f"挂上批次 {batch_id} 的只有 {len(rows)} 场。"
        )
        print(msg, file=sys.stderr)
    else:
        msg = ""
    stats = enforce_entity_id_gate(
        rows,
        label=label,
        require_import_batch_id=True,
        fail_on_mass_null=True,
    )
    if msg:
        stats["alerts"] = [msg, *stats["alerts"]]
    return stats


# 新建组尚未升格。别名 source 用这个后缀时，名册会标星；与 pending_confirm 列同时写。
_PENDING_ALIAS_SUFFIX = "_pending_confirm"


def _pending_alias_source(source: str) -> str:
    """新建且尚未升格的组别名来源。已经带后缀时不重复追加。"""
    text = (source or "import").strip() or "import"
    if text.endswith(_PENDING_ALIAS_SUFFIX):
        return text
    return f"{text}{_PENDING_ALIAS_SUFFIX}"


def _entity_canonical(conn: sqlite3.Connection, table: str, entity_id: int) -> str | None:
    row = conn.execute(
        f"SELECT name_zh_canonical FROM {table} WHERE id = ?",
        (entity_id,),
    ).fetchone()
    if row is None:
        return None
    value = row["name_zh_canonical"] if isinstance(row, sqlite3.Row) else row[0]
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _markers_conflict(left: str | None, right: str | None) -> bool:
    """歧义只问 alias_pool.detect_ambiguity。两侧原因代码不同则禁止并入。"""
    from app.alias_pool import detect_ambiguity

    return detect_ambiguity(left) != detect_ambiguity(right)


def _can_join_group(
    conn: sqlite3.Connection,
    table: str,
    entity_id: int,
    *names: str | None,
) -> bool:
    """精确命中之后仍要过同一道歧义闸。标记与目标常用名不一致时不并入。"""
    canonical = _entity_canonical(conn, table, entity_id)
    for name in names:
        if name and _markers_conflict(name, canonical):
            return False
    return True


def _name_already_promoted(kind: str, name: str) -> bool:
    """名册里已经升格的写法，建组时不再改成待确认。

    名册标了待确认，或不在已升格集合里，都按新组处理。
    """
    from app import team_pending as team_pending

    key = team_pending.norm(name)
    if not key:
        return False
    index = team_pending.load_index() if kind == "team" else team_pending.load_league_index()
    if key in index.pending:
        return False
    return key in index.known


def _insert_alias(
    conn: sqlite3.Connection,
    alias_table: str,
    id_column: str,
    alias: str,
    entity_id: int,
    source: str,
) -> None:
    conn.execute(
        f"INSERT INTO {alias_table} (alias, {id_column}, source) VALUES (?, ?, ?)",
        (alias, entity_id, source),
    )


def _remember_alias(
    conn: sqlite3.Connection,
    alias_table: str,
    id_column: str,
    alias: str | None,
    entity_id: int,
    source: str,
) -> None:
    """精确并入已有组时补别名。别名已被占用则跳过，不改指向。"""
    if not alias:
        return
    try:
        conn.execute(
            f"INSERT OR IGNORE INTO {alias_table} (alias, {id_column}, source) VALUES (?, ?, ?)",
            (alias, entity_id, source),
        )
    except sqlite3.IntegrityError:
        return


def _insert_new_group(
    conn: sqlite3.Connection,
    *,
    table: str,
    alias_table: str,
    id_column: str,
    kind: str,
    norm: str,
    raw: str | None,
    source: str,
) -> int | None:
    """新建一组。默认显示是这条新名。尚未升格时 pending_confirm 为真，别名来源带待确认后缀。

    不写入雷速、人工定稿或旧仓库高置信升格。列不存在时不改表结构，靠别名来源保持星号。
    别名被其他组占用时删掉刚插入的空组，返回空，不改挂到那一组上。
    """
    folded = (source or "").casefold()
    # BSD 只作别名或待定。不允许借 create=True 新建实体，更不允许建成已升格。
    if "bsd" in folded or "bzzoiro" in folded:
        return None
    promoted = _name_already_promoted(kind, norm)
    alias_source = source if promoted else _pending_alias_source(source)
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    fields = ["name_zh_canonical"]
    values: list[Any] = [norm]
    if "pending_confirm" in columns:
        fields.append("pending_confirm")
        values.append("false" if promoted else "true")
    # 尚未升格的新组只记临时。不写雷速、人工定稿或旧仓库高置信。
    # 名册里已经升格的写法不填 promotion：待确认列为假，按旧表兼容视为已升格。
    if "promotion" in columns and not promoted:
        fields.append("promotion")
        values.append("temporary")
    placeholders = ", ".join("?" for _ in fields)
    entity_id: int | None = None
    try:
        cursor = conn.execute(
            f"INSERT INTO {table} ({', '.join(fields)}) VALUES ({placeholders})",
            values,
        )
        entity_id = int(cursor.lastrowid)
        _insert_alias(conn, alias_table, id_column, norm, entity_id, alias_source)
        if raw and raw != norm:
            try:
                _insert_alias(conn, alias_table, id_column, raw, entity_id, alias_source)
            except sqlite3.IntegrityError:
                # 原文别名指向了别的组。组本身保留，不把原文强行并过去。
                pass
        return entity_id
    except sqlite3.IntegrityError:
        if entity_id is not None:
            conn.execute(f"DELETE FROM {alias_table} WHERE {id_column} = ?", (entity_id,))
            conn.execute(f"DELETE FROM {table} WHERE id = ?", (entity_id,))
        return None


def resolve_or_create_team(
    conn: sqlite3.Connection,
    name: str | None,
    *,
    source: str = "import",
    create: bool = True,
) -> int | None:
    """按别名或常用名精确解析球队。可选新建一组。

    新建时默认显示是这条新名。名册里还没有升格的，pending_confirm 为真，
    不得把这次插入当成常用名升格。歧义标记与目标常用名不一致时不并入。
    冲突或失败返回 None。
    """
    if not _has_table(conn, "teams") or not _has_table(conn, "team_aliases"):
        return None
    raw = (str(name).strip() if name is not None else "") or None
    norm = normalize_name(name)
    if not norm:
        return None
    team_id = lookup_entity_id(conn, "team", name)
    if team_id is not None:
        if not _can_join_group(conn, "teams", team_id, norm, raw):
            return None
        _remember_alias(conn, "team_aliases", "team_id", norm, team_id, source)
        if raw and raw != norm:
            _remember_alias(conn, "team_aliases", "team_id", raw, team_id, source)
        return team_id
    if not create:
        return None
    created = _insert_new_group(
        conn,
        table="teams",
        alias_table="team_aliases",
        id_column="team_id",
        kind="team",
        norm=norm,
        raw=raw,
        source=source,
    )
    if created is not None:
        return created
    existing = _lookup_team_id(conn, norm) or (raw and _lookup_team_id(conn, raw))
    if existing is not None and _can_join_group(conn, "teams", existing, norm, raw):
        return existing
    return None


def resolve_or_create_league(
    conn: sqlite3.Connection,
    name: str | None,
    *,
    source: str = "import",
    create: bool = True,
) -> int | None:
    """按别名或常用名精确解析赛事。可选新建一组。

    新建时默认显示是这条新名。名册里还没有升格的，pending_confirm 为真，
    不得把这次插入当成常用名升格。歧义标记与目标常用名不一致时不并入。
    冲突或失败返回 None。
    """
    if not _has_table(conn, "leagues") or not _has_table(conn, "league_aliases"):
        return None
    raw = (str(name).strip() if name is not None else "") or None
    norm = normalize_name(name)
    if not norm:
        return None
    league_id = lookup_entity_id(conn, "competition", name)
    if league_id is not None:
        if not _can_join_group(conn, "leagues", league_id, norm, raw):
            return None
        _remember_alias(conn, "league_aliases", "league_id", norm, league_id, source)
        if raw and raw != norm:
            _remember_alias(conn, "league_aliases", "league_id", raw, league_id, source)
        return league_id
    if not create:
        return None
    created = _insert_new_group(
        conn,
        table="leagues",
        alias_table="league_aliases",
        id_column="league_id",
        kind="competition",
        norm=norm,
        raw=raw,
        source=source,
    )
    if created is not None:
        return created
    existing = _lookup_league_id(conn, norm) or (raw and _lookup_league_id(conn, raw))
    if existing is not None and _can_join_group(conn, "leagues", existing, norm, raw):
        return existing
    return None


def sync_match_entity_ids(
    conn: sqlite3.Connection,
    match_id: int,
    *,
    home_team: str | None,
    away_team: str | None,
    competition_name: str | None,
    source: str = "import",
    create: bool = True,
) -> None:
    """写入 matches.home/away/league_id；解不出则保持 NULL，不中断导入。"""
    if not _has_column(conn, "matches", "home_team_id"):
        return
    home_id = resolve_or_create_team(conn, home_team, source=source, create=create)
    away_id = resolve_or_create_team(conn, away_team, source=source, create=create)
    league_id = resolve_or_create_league(
        conn, competition_name, source=source, create=create
    )
    conn.execute(
        """
        UPDATE matches
        SET home_team_id = ?, away_team_id = ?, league_id = ?,
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (home_id, away_id, league_id, match_id),
    )



def sync_kickoff_at(conn: sqlite3.Connection, match_id: int, match: dict) -> None:
    """v1.2：写 matches.kickoff_at / kickoff_minute_known（列不存在则跳过）。

    - JSON 带 kickoff_at → 原样写入，minute_known=1
    - 否则仅有 kickoff_hour → 合成整点（早场特殊带 0–11 记次日；
      11:31–11:59 需分钟才能排除，整点 11 全部 +1 日），minute_known=0；
      已有 minute_known=1 的精确值不覆盖
    """
    if not _has_column(conn, "matches", "kickoff_at"):
        return
    ka = match.get("kickoff_at")
    if ka:
        conn.execute(
            "UPDATE matches SET kickoff_at = ?, kickoff_minute_known = 1 WHERE id = ?",
            (ka, match_id),
        )
        return
    # 仅整点回退：0<=h<=11 → jingcai_date+1（对齐 is_early_kickoff_band hour-only）
    conn.execute(
        """
        UPDATE matches
        SET kickoff_at = (CASE WHEN kickoff_hour <= 11 THEN date(jingcai_date, '+1 day')
                               ELSE date(jingcai_date) END)
                         || 'T' || printf('%02d', kickoff_hour) || ':00:00+08:00',
            kickoff_minute_known = 0
        WHERE id = ? AND kickoff_hour BETWEEN 0 AND 23
          AND (kickoff_at IS NULL OR kickoff_minute_known = 0)
        """,
        (match_id,),
    )


def insert_match(conn: sqlite3.Connection, item: dict, batch_id: int, *, write_prediction: bool = True) -> int:
    """Upsert one match by match_uid: DELETE child rows then re-insert; UPDATE match row.

    Upsert strategy: ON CONFLICT(match_uid) DO UPDATE on matches, then DELETE+INSERT
    for child tables (results/stats/odds_*/meta/predictions). Re-import of same uid
    replaces content and reassigns import_batch_id to the new batch.
    Predictions: only written when write_prediction=True AND item has prediction;
    monthly import passes write_prediction=False and never invents directions.
    """
    match = item["match"]
    uid = match_uid(match)
    scope = infer_scope(match)
    # Normalize scope onto match for downstream
    match = {**match, "scope": scope}
    jc = match.get("jc")
    competition = match.get("competition") or {}
    teams = match["teams"]

    conn.execute(
        """
        INSERT INTO matches (
          match_uid, scope, jingcai_date, weekday, kickoff_hour,
          jc_id, jc_no, competition_name, competition_type, competition_stage,
          home_team, away_team, import_batch_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(match_uid) DO UPDATE SET
          scope=excluded.scope,
          jingcai_date=excluded.jingcai_date,
          weekday=excluded.weekday,
          kickoff_hour=excluded.kickoff_hour,
          jc_id=excluded.jc_id,
          jc_no=excluded.jc_no,
          competition_name=excluded.competition_name,
          competition_type=excluded.competition_type,
          competition_stage=excluded.competition_stage,
          home_team=excluded.home_team,
          away_team=excluded.away_team,
          import_batch_id=excluded.import_batch_id,
          updated_at=datetime('now')
        """,
        (
            uid,
            scope,
            match["date"],
            match.get("weekday"),
            match.get("kickoff_hour"),
            (jc or {}).get("id") if jc else None,
            parse_jc_no(jc),
            competition.get("name"),
            competition.get("type"),
            # stage may be int or str in monthly JSON
            str(competition["stage"]) if competition.get("stage") is not None else None,
            teams["home"],
            teams["away"],
            batch_id,
        ),
    )
    row = conn.execute("SELECT id FROM matches WHERE match_uid = ?", (uid,)).fetchone()
    match_id = int(row["id"])
    sync_kickoff_at(conn, match_id, match)
    # 身份编号至少做 create=False 查找。月度导入仍用 create=True（查不到才新建一组）。
    # 新建组写入这条新名，并标待确认；名册里已经升格的写法不重新打开星号。
    # 批次级门禁在 import_monthly_json / seed。
    sync_match_entity_ids(
        conn,
        match_id,
        home_team=teams.get("home"),
        away_team=teams.get("away"),
        competition_name=competition.get("name"),
        source="import",
        create=True,
    )

    result = item.get("result")
    conn.execute("DELETE FROM results WHERE match_id = ?", (match_id,))
    if result is not None:
        # Keep row even if all nulls (pre-match placeholders in 2607)
        conn.execute(
            """
            INSERT INTO results (match_id, home_goals, away_goals, total_goals, wdl)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                match_id,
                result.get("home_goals"),
                result.get("away_goals"),
                result.get("total_goals"),
                result.get("wdl"),
            ),
        )

    stats = item.get("stats") or {}
    recent = stats.get("recent") or {}
    h2h = stats.get("h2h")
    rank = stats.get("rank") or {}
    extras: dict[str, Any] = {}
    if "support_proxy_odds" in stats:
        extras["support_proxy_odds"] = stats.get("support_proxy_odds")
    home_streak = recent.get("home_streak_last6")
    weather = stats.get("weather")
    # weather may be string ("下雨") or object — always JSON-encode
    weather_json = _json_or_none(weather) if weather is not None else None

    conn.execute("DELETE FROM stats WHERE match_id = ?", (match_id,))
    conn.execute(
        """
        INSERT INTO stats (
          match_id, recent_json, home_streak_last6, h2h_json,
          rank_home, rank_away, popularity_diff, injury_json, weather_json, extras_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            match_id,
            _json_or_none(recent) if recent else None,
            float(home_streak) if home_streak is not None else None,
            _json_or_none(h2h) if h2h is not None else None,
            rank.get("home"),
            rank.get("away"),
            stats.get("popularity_diff"),
            _json_or_none(stats["injury"]) if stats.get("injury") is not None else None,
            weather_json,
            _json_or_none(extras) if extras else None,
        ),
    )

    odds = item.get("odds") or {}
    conn.execute("DELETE FROM odds_raw WHERE match_id = ?", (match_id,))
    conn.execute(
        "INSERT INTO odds_raw (match_id, odds_json) VALUES (?, ?)",
        (match_id, json.dumps(odds, ensure_ascii=False)),
    )

    _ensure_v1_7_columns(conn)
    conn.execute("DELETE FROM odds_asian WHERE match_id = ?", (match_id,))
    asian = odds.get("asian") or {}
    for book, phases in asian.items():
        if not isinstance(phases, dict):
            continue
        for phase, val in phases.items():
            if val is None or phase == "water_src":  # book 级来源标记，不是盘口阶段
                continue
            row_vals = asian_phase_to_row(val)
            if row_vals is None:
                continue
            handicap, home_water, away_water = row_vals
            hw, aw, wsrc, wcens, wextras = asian_water_fields(book, val, phases, home_water, away_water)
            conn.execute(
                """
                INSERT INTO odds_asian (match_id, book, phase, handicap, home_water, away_water,
                                        water_src, water_censored, extras_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (match_id, book, phase, handicap, hw, aw, wsrc, wcens, wextras),
            )

    conn.execute("DELETE FROM odds_euro_home WHERE match_id = ?", (match_id,))
    euro = odds.get("euro_home_win") or {}
    for book, phases in euro.items():
        if not isinstance(phases, dict):
            continue
        for phase, val in phases.items():
            if val is None:
                continue
            conn.execute(
                """
                INSERT INTO odds_euro_home (match_id, book, phase, home_win)
                VALUES (?, ?, ?, ?)
                """,
                (match_id, book, phase, float(val)),
            )

    conn.execute("DELETE FROM odds_jc_home WHERE match_id = ?", (match_id,))
    jc_home = odds.get("jc_home_win") or {}
    for phase, val in jc_home.items():
        if val is None:
            continue
        conn.execute(
            "INSERT INTO odds_jc_home (match_id, phase, home_win) VALUES (?, ?, ?)",
            (match_id, phase, float(val)),
        )

    conn.execute("DELETE FROM odds_jc_hhad WHERE match_id = ?", (match_id,))
    jc_hhad = odds.get("jc_hhad") or {}
    for phase, val in jc_hhad.items():
        if val is None:
            continue
        gl = val.get("goal_line")
        gl_num = float(gl) if gl is not None and gl != "" else None
        conn.execute(
            """
            INSERT INTO odds_jc_hhad (match_id, phase, goal_line, home_odds, draw_odds, away_odds)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                match_id,
                phase,
                gl_num,
                float(val["home"]) if val.get("home") is not None else None,
                float(val["draw"]) if val.get("draw") is not None else None,
                float(val["away"]) if val.get("away") is not None else None,
            ),
        )

    meta = item.get("meta") or {}
    extras_meta = {
        "kickoff_at": match.get("kickoff_at"),
        "ids": match.get("ids") or {},
        "schema_version": meta.get("schema_version"),
        "pipeline": meta.get("pipeline"),
        "note": meta.get("note"),
    }
    conn.execute("DELETE FROM match_meta WHERE match_id = ?", (match_id,))
    conn.execute(
        """
        INSERT INTO match_meta (match_id, source_file, month, extras_json)
        VALUES (?, ?, ?, ?)
        """,
        (
            match_id,
            meta.get("source_file"),
            meta.get("month"),
            json.dumps(extras_meta, ensure_ascii=False),
        ),
    )

    # Predictions: never invent. Monthly import sets write_prediction=False.
    if write_prediction:
        pred = item.get("prediction")
        # 0.3.19：旧 SHADOW_S2 / SHADOW_N4 已冻结，禁止再写
        from app.ledger_registry import assert_appendable
        assert_appendable((pred or {}).get("strategy") or "CFFXDJ_5_V3")
        # 只替换本条写入的策略；不误删其他方案（如影子 TEST_V3_MIRROR）的预测
        conn.execute(
            "DELETE FROM predictions WHERE match_id = ? AND strategy = ?",
            (match_id, (pred or {}).get("strategy") or "CFFXDJ_5_V3"),
        )
        if pred:
            from app.settle_book import DEFAULT_SETTLE_BOOK
            conn.execute(
                """
                INSERT INTO predictions (
                  match_id, strategy, direction, settle_book, rationale_json, confidence
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    match_id,
                    pred.get("strategy") or "CFFXDJ_5_V3",
                    pred["direction"],
                    pred.get("settle_book") or DEFAULT_SETTLE_BOOK,
                    json.dumps(pred.get("rationale") or [], ensure_ascii=False),
                    pred.get("confidence"),
                ),
            )
    # else: leave any existing predictions untouched on re-import of same uid
    # (monthly files have no prediction; do not DELETE)

    # 本场的 results 和亚盘都已经按导入内容重写。完场比分存在时覆盖重算机构赛果；
    # 比分被撤回时删掉这一场已经落库的机构赛果。只写当前连接，不提交，不打开双写，不计算竞彩赛果。
    from app.market_settle import refresh_after_results_written
    refresh_after_results_written(conn, [match_id])

    return match_id
