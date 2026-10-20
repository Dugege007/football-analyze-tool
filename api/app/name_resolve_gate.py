"""新名称入库闸（七步）。

权威是 docs/schema/name-system-design-v1.md 第 6 章。步骤顺序与
docs/schema/v2_0-name-resolve-group-rules.md 一致，自动化边界以总方案为准。

七步分别是：规范化、精确命中、同场多证据、赛事侧、外部取证、歧义闸、落库。
第 1、2、6 步全自动。第 3、4 步只在证据够时自动并入，并且只加别名，不改常用名。
第 5 步不访问网络。生产守护进程、竞彩入库和 BSD 对照都不得把本模块当成爬虫。
证据不足时把 needs_external_evidence 和线索写入待定池，由人工智能或人工稍后填写。
第 7 步执行已经通过闸的动作：并入只加别名；调用方明确允许时才新建组，默认显示为新名并标待确认。

本模块拒绝：猜挂；仅凭弱模糊（编辑距离、前后缀、子串）并组；男女队或青年队自动合并；
把 BSD、竞彩或五美元原文升成常用名；create=True 把新组写成已升格。
BSD 来源即使 create=True 也不新建实体。

待定池出现记录仍不保存比分。比分只在判断当时用来拒绝错误对场，或作为同场证据的一项。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from app.alias_pool import detect_ambiguity, norm_key
from app.alias_pool import _parse_source_ref

_BEIJING = timezone(timedelta(hours=8))
_WINDOW = timedelta(minutes=15)

REASON_EXTERNAL = "needs_external_evidence"
CRITERION_EXACT = "known_alias_exact"
CRITERION_SAME_MATCH = "same_match_multi_evidence"
CRITERION_LEAGUE = "league_side_mapped"
CONFIDENCE_SAME_MATCH = "same_match_multi_evidence"
CONFIDENCE_LEAGUE = "league_side_context"
CONFIDENCE_FALLBACK = "level2_equal_after_cleaning"

_SCORE_KEYS = frozenset(
    {
        "score",
        "score_home",
        "score_away",
        "home_goals",
        "away_goals",
        "home_score",
        "away_score",
    }
)
_CLUE_KEYS = (
    "kickoff_at",
    "opponent_name",
    "opponent_mapped",
    "opponent_id",
    "competition_name",
    "jingcai_date",
    "slot",
    "match_id",
    "home_team_id",
    "away_team_id",
    "league_id",
    "region",
    "country",
)


def _is_bsd_source(source: str | None) -> bool:
    """BSD 与 bzzoiro 只允许别名或待定，不允许借新建组成已升格实体。"""
    text = (source or "").casefold()
    return "bsd" in text or "bzzoiro" in text


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_BEIJING)
    return parsed


def _slot(context: dict, source_ref: str) -> str | None:
    raw = str(context.get("slot") or "").strip().lower()
    if raw in ("home", "home_team"):
        return "home"
    if raw in ("away", "away_team"):
        return "away"
    parsed = _parse_source_ref(source_ref) if source_ref else None
    if parsed is None:
        return None
    name = parsed[1]
    if name in ("home_team", "home"):
        return "home"
    if name in ("away_team", "away"):
        return "away"
    return None


def _clues(context: dict | None, source_ref: str) -> dict:
    """线索可写入待定池。比分键一律丢掉。"""
    source = context or {}
    clues: dict[str, Any] = {}
    for key in _CLUE_KEYS:
        if key not in source or source[key] in (None, ""):
            continue
        value = source[key]
        if key == "opponent_mapped":
            clues[key] = bool(value)
            continue
        if key.endswith("_id") or key == "match_id":
            number = _int_or_none(value)
            if number is not None:
                clues[key] = number
            continue
        text = str(value).strip()
        if text:
            clues[key] = text
    if source_ref:
        clues["source_ref"] = source_ref
    return clues


def _has_external_clues(context: dict | None, source_ref: str) -> bool:
    """有开赛、对手或场次位置时才记外部取证。只有赛事名不算。"""
    source = context or {}
    if _parse_dt(source.get("kickoff_at")) is not None:
        return True
    if _int_or_none(source.get("opponent_id")) is not None:
        return True
    if str(source.get("opponent_name") or "").strip():
        return True
    if _int_or_none(source.get("match_id")) is not None:
        return True
    if source_ref and _parse_source_ref(source_ref) is not None:
        return True
    return False


def _score_pair(context: dict | None) -> tuple[int, int] | None:
    source = context or {}
    home = source.get("home_goals", source.get("score_home", source.get("home_score")))
    away = source.get("away_goals", source.get("score_away", source.get("away_score")))
    if home in (None, "") or away in (None, ""):
        return None
    try:
        return int(home), int(away)
    except (TypeError, ValueError):
        return None


def _markers_conflict(left: str | None, right: str | None) -> bool:
    return detect_ambiguity(left) != detect_ambiguity(right)


def _decision(
    *,
    step: int,
    action: str,
    message: str,
    reason: str | None = None,
    standard_id: int | None = None,
    standard_name: str | None = None,
    clues: dict | None = None,
    criterion: str | None = None,
    confidence: str | None = None,
) -> dict:
    return {
        "step": step,
        "action": action,
        "reason": reason,
        "standard_id": standard_id,
        "standard_name": standard_name,
        "clues": dict(clues or {}),
        "message": message,
        "criterion": criterion,
        "confidence": confidence,
    }


def _exact_hits(conn: sqlite3.Connection, kind: str, normalized: str) -> list[dict]:
    """规范化键与常用名或别名全等的实体。不做宽松键，也不做前后缀。"""
    if kind == "team":
        standard_table, alias_table, id_column = "teams", "team_aliases", "team_id"
    else:
        standard_table, alias_table, id_column = "leagues", "league_aliases", "league_id"
    found: dict[int, dict] = {}
    if _table_exists(conn, standard_table):
        for row in conn.execute(f"SELECT id, name_zh_canonical FROM {standard_table}"):
            name = "" if row[1] is None else str(row[1])
            if norm_key(name) == normalized:
                found[int(row[0])] = {"standard_id": int(row[0]), "standard_name": name}
    if _table_exists(conn, alias_table):
        for row in conn.execute(f"SELECT {id_column}, alias FROM {alias_table}"):
            alias = "" if row[1] is None else str(row[1])
            if norm_key(alias) != normalized:
                continue
            standard_id = int(row[0])
            if standard_id in found:
                continue
            label = ""
            if _table_exists(conn, standard_table):
                owner = conn.execute(
                    f"SELECT name_zh_canonical FROM {standard_table} WHERE id = ?",
                    (standard_id,),
                ).fetchone()
                if owner is not None and owner[0] is not None:
                    label = str(owner[0])
            found[standard_id] = {"standard_id": standard_id, "standard_name": label or alias}
    return list(found.values())


def _canonical_name(conn: sqlite3.Connection, table: str, entity_id: int) -> str:
    if not _table_exists(conn, table):
        return ""
    row = conn.execute(
        f"SELECT name_zh_canonical FROM {table} WHERE id = ?",
        (entity_id,),
    ).fetchone()
    if row is None or row[0] is None:
        return ""
    return str(row[0])


def _score_agrees(conn: sqlite3.Connection, match_id: int, score: tuple[int, int]) -> bool | None:
    """True 一致，False 不一致，None 表示库里没有可核对的比分。"""
    if not _table_exists(conn, "results"):
        return None
    columns = _columns(conn, "results")
    if not {"match_id", "home_goals", "away_goals"}.issubset(columns):
        return None
    row = conn.execute(
        "SELECT home_goals, away_goals FROM results WHERE match_id = ?",
        (match_id,),
    ).fetchone()
    if row is None or row[0] is None or row[1] is None:
        return None
    try:
        return (int(row[0]), int(row[1])) == score
    except (TypeError, ValueError):
        return None


def _fetch_dicts(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    """按列名读行。不改连接的 row_factory，避免影响调用方。"""
    cursor = conn.execute(sql, params)
    names = [item[0] for item in cursor.description or ()]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _matches_near(conn: sqlite3.Connection, kickoff: datetime, columns: set[str]) -> list[dict]:
    day = kickoff.astimezone(_BEIJING).date()
    dates = [(day + timedelta(days=offset)).isoformat() for offset in (-1, 0, 1)]
    selected = ["id", "home_team_id", "away_team_id", "kickoff_at"]
    if "league_id" in columns:
        selected.append("league_id")
    clause = " OR ".join("kickoff_at LIKE ?" for _ in dates)
    return _fetch_dicts(
        conn,
        f"SELECT {', '.join(selected)} FROM matches WHERE kickoff_at IS NOT NULL AND ({clause})",
        tuple(f"{item}%" for item in dates),
    )


def _same_match_team(
    conn: sqlite3.Connection,
    raw_name: str,
    context: dict,
    source_ref: str,
) -> dict | None:
    """同场多证据。只并入该侧已经填写的球队编号。证据不足时返回 None。"""
    kickoff = _parse_dt(context.get("kickoff_at"))
    if kickoff is None or not _table_exists(conn, "matches"):
        return None
    columns = _columns(conn, "matches")
    if not {"id", "home_team_id", "away_team_id", "kickoff_at"}.issubset(columns):
        return None
    slot = _slot(context, source_ref)
    if slot is None:
        return None
    slot_column = "home_team_id" if slot == "home" else "away_team_id"
    other_column = "away_team_id" if slot == "home" else "home_team_id"
    match_id = _int_or_none(context.get("match_id"))
    parsed = _parse_source_ref(source_ref) if source_ref else None
    if match_id is None and parsed is not None:
        match_id = parsed[0]

    if match_id is not None:
        rows = _fetch_dicts(conn, "SELECT * FROM matches WHERE id = ?", (match_id,))
    else:
        rows = _matches_near(conn, kickoff, columns)
    opponent_id = _int_or_none(context.get("opponent_id"))
    opponent_flag = bool(context.get("opponent_mapped")) or opponent_id is not None
    score = _score_pair(context)
    accepted: list[tuple[int, str]] = []
    for row in rows:
        remote = _parse_dt(row["kickoff_at"])
        if remote is None or abs(remote - kickoff) > _WINDOW:
            continue
        team_id = _int_or_none(row[slot_column])
        other_id = _int_or_none(row[other_column])
        if team_id is None or other_id is None:
            continue
        if opponent_id is not None and other_id != opponent_id:
            continue
        if not opponent_flag:
            # 没有对手证据时，时间或比分都不得单独定队。
            continue
        if score is not None:
            agreed = _score_agrees(conn, int(row["id"]), score)
            if agreed is False:
                continue
        accepted.append((team_id, _canonical_name(conn, "teams", team_id)))
    unique = {item[0]: item[1] for item in accepted}
    if len(unique) != 1:
        if len(unique) > 1:
            return _decision(
                step=6,
                action="blocked",
                reason="multi_candidate",
                message="同场窗口里有一支以上的球队，不能猜挂。",
                clues=_clues(context, source_ref),
            )
        return None
    team_id, standard_name = next(iter(unique.items()))
    if _markers_conflict(raw_name, standard_name):
        marker = detect_ambiguity(raw_name) or detect_ambiguity(standard_name) or "same_name_other_team"
        return _decision(
            step=6,
            action="blocked",
            reason=marker,
            standard_id=team_id,
            standard_name=standard_name,
            message="同场证据指向已有球队，但性别、年龄段或二队标记不一致，不能并入。",
            clues=_clues(context, source_ref),
        )
    return _decision(
        step=3,
        action="merge_alias",
        reason=None,
        standard_id=team_id,
        standard_name=standard_name,
        criterion=CRITERION_SAME_MATCH,
        confidence=CONFIDENCE_SAME_MATCH,
        message=(
            f"同场多证据成立：对手已映射，开赛时间在前后十五分钟内，"
            f"新名只作为「{standard_name}」的别名，不改常用名。"
        ),
        clues=_clues(context, source_ref),
    )


def _league_side(conn: sqlite3.Connection, raw_name: str, context: dict, source_ref: str) -> dict | None:
    """主客都已映射且 league_id 已定时，赛事字符串只加别名。"""
    home_id = _int_or_none(context.get("home_team_id"))
    away_id = _int_or_none(context.get("away_team_id"))
    league_id = _int_or_none(context.get("league_id"))
    if league_id is None or home_id is None or away_id is None:
        match_id = _int_or_none(context.get("match_id"))
        parsed = _parse_source_ref(source_ref) if source_ref else None
        if match_id is None and parsed is not None and parsed[1] in ("competition_name", "competition"):
            match_id = parsed[0]
        columns = _columns(conn, "matches")
        if (
            match_id is not None
            and _table_exists(conn, "matches")
            and {"home_team_id", "away_team_id", "league_id"}.issubset(columns)
        ):
            row = conn.execute(
                "SELECT home_team_id, away_team_id, league_id FROM matches WHERE id = ?",
                (match_id,),
            ).fetchone()
            if row is not None:
                home_id = home_id if home_id is not None else _int_or_none(row[0])
                away_id = away_id if away_id is not None else _int_or_none(row[1])
                league_id = league_id if league_id is not None else _int_or_none(row[2])
    if home_id is None or away_id is None or league_id is None:
        return None
    standard_name = _canonical_name(conn, "leagues", league_id)
    if not standard_name:
        return None
    if _markers_conflict(raw_name, standard_name):
        marker = detect_ambiguity(raw_name) or detect_ambiguity(standard_name) or "same_name_other_team"
        return _decision(
            step=6,
            action="blocked",
            reason=marker,
            standard_id=league_id,
            standard_name=standard_name,
            message="赛事字符串与已定赛事的歧义标记不一致，不能并入。",
            clues=_clues(context, source_ref),
        )
    return _decision(
        step=4,
        action="merge_alias",
        standard_id=league_id,
        standard_name=standard_name,
        criterion=CRITERION_LEAGUE,
        confidence=CONFIDENCE_LEAGUE,
        message=f"主客已映射且赛事编号已定，新名只作为「{standard_name}」的别名，不改常用名。",
        clues=_clues(context, source_ref),
    )


def evaluate(
    conn: sqlite3.Connection,
    kind: str,
    raw_name: Any,
    context: dict | None = None,
    *,
    source: str = "",
    source_ref: str = "",
) -> dict:
    """按七步给出决定。本函数不写库，也不访问网络。

    action 取值：skipped、merge_alias、blocked、pool、create_group。
    merge_alias 只表示可以加别名。create_group 表示对不上已有组；调用方若不允许新建，应改记待定池。
    source 目前不参与升格。BSD 是否允许新建由 admit 决定。
    """
    _ = source
    if kind not in ("team", "competition"):
        raise ValueError("入库闸的类别只允许 team 或 competition。")
    observed = "" if raw_name is None else str(raw_name)
    normalized = norm_key(observed)
    clues = _clues(context, source_ref)
    if not normalized:
        return _decision(step=1, action="skipped", message="名称为空，闸没有给出实体。", clues=clues)
    context = context or {}
    hits = _exact_hits(conn, kind, normalized)
    if len(hits) > 1:
        return _decision(
            step=6,
            action="blocked",
            reason="multi_candidate",
            message="同一规范化键指向一个以上的实体，不能猜挂。",
            clues=clues,
        )
    if len(hits) == 1:
        target = hits[0]
        if _markers_conflict(observed, target["standard_name"]):
            marker = detect_ambiguity(observed) or detect_ambiguity(target["standard_name"])
            return _decision(
                step=6,
                action="blocked",
                reason=marker,
                standard_id=target["standard_id"],
                standard_name=target["standard_name"],
                message="精确命中的实体与新名歧义标记不一致，不能并入。",
                clues=clues,
            )
        return _decision(
            step=2,
            action="merge_alias",
            standard_id=target["standard_id"],
            standard_name=target["standard_name"],
            criterion=CRITERION_EXACT,
            confidence="level1_known_alias",
            message=f"精确命中「{target['standard_name']}」。只加别名，不改常用名。",
            clues=clues,
        )
    if kind == "team":
        matched = _same_match_team(conn, observed, context, source_ref)
        if matched is not None:
            return matched
    else:
        matched = _league_side(conn, observed, context, source_ref)
        if matched is not None:
            return matched
    if detect_ambiguity(observed):
        # 名称本身带歧义标记，又没有安全命中：可以新建自己的组，但不得并进别的实体。
        # 没有允许新建时，调用方应送入待定池而不是猜挂。
        if _has_external_clues(context, source_ref):
            return _decision(
                step=5,
                action="pool",
                reason=REASON_EXTERNAL,
                message="名称带有性别、年龄段或二队标记，又没有安全命中。需要外部取证，先进入待定池，不自动并组。",
                clues=clues,
            )
    if _has_external_clues(context, source_ref):
        return _decision(
            step=5,
            action="pool",
            reason=REASON_EXTERNAL,
            message="精确命中和同场证据都不够。已记录 needs_external_evidence，不抓取网页，也不静默合并。",
            clues=clues,
        )
    return _decision(
        step=7,
        action="create_group",
        message="没有命中已有组。若调用方允许新建，默认显示为这条新名并标待确认；否则只进入待定池。",
        clues=clues,
    )


def _confidence_supported(conn: sqlite3.Connection, value: str) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'alias_mapping_audit'"
    ).fetchone()
    if row is None or row[0] is None:
        return False
    return value in str(row[0])


def _stored_confidence(conn: sqlite3.Connection, preferred: str) -> str:
    """旧库的审计表检查约束可能还没有新取值。判据代码仍写在 criterion 里。"""
    if _confidence_supported(conn, preferred):
        return preferred
    return CONFIDENCE_FALLBACK


def augment_observe_decision(
    conn: sqlite3.Connection,
    kind: str,
    raw_name: str,
    normalized: str,
    raw_context: dict | None,
    cleaned: dict,
    decision: dict,
    *,
    source_ref: str = "",
) -> dict:
    """在待定池精确命中之后补第 3、4、5 步。

    已经自动映射，或已经因歧义、撤销被拦住时，不改原决定。
    前后缀候选仍然优先保留；只有同场证据真正指向该侧已有编号时才升级为加别名。
    不写库。调用方沿用待定池原有的别名写入。
    """
    _ = (normalized, cleaned)
    if decision.get("action") == "auto" or decision.get("manual_reason"):
        return decision
    verdict = evaluate(conn, kind, raw_name, raw_context, source_ref=source_ref)
    if verdict["action"] == "merge_alias" and verdict["step"] in (3, 4):
        confidence = _stored_confidence(conn, str(verdict["confidence"]))
        return {
            "action": "auto",
            "criterion": verdict["criterion"],
            "confidence": confidence,
            "standard_id": verdict["standard_id"],
            "standard_name": verdict["standard_name"],
            "detail": verdict["message"],
            "status": "confirmed",
            "manual_reason": None,
            "candidate_id": verdict["standard_id"],
            "candidate_name": verdict["standard_name"],
            "candidate_reason": None,
            "candidate_basis": None,
            "extras": verdict["clues"],
        }
    if verdict["action"] == "blocked" and not decision.get("candidate_id"):
        copied = dict(decision)
        copied["status"] = "ambiguous"
        copied["manual_reason"] = verdict["reason"]
        copied["candidate_id"] = verdict["standard_id"]
        copied["candidate_name"] = verdict["standard_name"]
        copied["candidate_reason"] = verdict["message"]
        copied["extras"] = verdict["clues"]
        return copied
    if verdict["step"] == 5 and verdict["reason"] == REASON_EXTERNAL and not decision.get("candidate_id"):
        copied = dict(decision)
        copied["manual_reason"] = REASON_EXTERNAL
        copied["candidate_reason"] = verdict["message"]
        copied["extras"] = verdict["clues"]
        if not copied.get("status"):
            copied["status"] = "pending"
        return copied
    return decision


def _create_pending_group(conn: sqlite3.Connection, kind: str, raw_name: str, source: str) -> int | None:
    """新建未升格的组。不把 promotion 写成雷速、人工或旧仓高置信。"""
    import sys
    from pathlib import Path

    scripts = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import import_lib

    if kind == "team":
        return import_lib.resolve_or_create_team(conn, raw_name, source=source, create=True)
    return import_lib.resolve_or_create_league(conn, raw_name, source=source, create=True)


def admit(
    conn: sqlite3.Connection,
    kind: str,
    raw_name: Any,
    source: str,
    source_ref: str,
    context: dict | None = None,
    *,
    allow_create: bool = False,
) -> dict:
    """执行入库闸。默认不新建组，只观察进待定池或给已有组加别名。

    allow_create 为真时，对不上已有组才新建，并保持待确认。
    BSD 来源忽略 allow_create，避免静默建成实体。
    本函数不修改比赛文本列，也不把新字符串写成已升格常用名。
    """
    from app import alias_pool

    verdict = evaluate(conn, kind, raw_name, context, source=source, source_ref=source_ref)
    if verdict["action"] == "skipped":
        return {**verdict, "alias_inserted": False, "match_text_rewritten": False}
    if verdict["action"] == "create_group" and allow_create and not _is_bsd_source(source):
        created = _create_pending_group(conn, kind, "" if raw_name is None else str(raw_name), source)
        return {
            **verdict,
            "action": "create_group" if created is not None else "blocked",
            "standard_id": created,
            "reason": None if created is not None else "create_refused",
            "message": verdict["message"] if created is not None else "新建被拒绝，没有写成已升格实体。",
            "alias_inserted": created is not None,
            "match_text_rewritten": False,
        }
    try:
        alias_pool._require_pool_tables(conn)
    except RuntimeError:
        alias_pool.ensure_schema(conn)
    observed = alias_pool.pool_observe(conn, kind, raw_name, source, source_ref, context)
    observed["gate_step"] = verdict["step"]
    observed["gate_action"] = verdict["action"]
    observed["match_text_rewritten"] = False
    return observed


def observe_open_slots(conn: sqlite3.Connection, match_ids: list[int], *, source: str) -> dict:
    """把仍然没有身份编号的主队、客队、赛事字符串送入闸。

    只观察或给已有组加别名。不新建已升格实体，不改比赛文本列，不把比分写入待定池。
    单条失败不影响其余名称，也不应推翻调用方已经提交的赛果。
    """
    observed: list[dict] = []
    errors: list[dict] = []
    if not match_ids or not _table_exists(conn, "matches"):
        return {"observed": observed, "errors": errors, "match_text_rewritten": False}
    columns = _columns(conn, "matches")
    wanted = [
        name
        for name in (
            "id",
            "home_team",
            "away_team",
            "competition_name",
            "home_team_id",
            "away_team_id",
            "league_id",
            "kickoff_at",
        )
        if name in columns
    ]
    if "id" not in wanted:
        return {"observed": observed, "errors": errors, "match_text_rewritten": False}
    for match_id in match_ids:
        row = conn.execute(
            f"SELECT {', '.join(wanted)} FROM matches WHERE id = ?",
            (int(match_id),),
        ).fetchone()
        if row is None:
            continue
        record = {wanted[index]: row[index] for index in range(len(wanted))}
        home_id = _int_or_none(record.get("home_team_id"))
        away_id = _int_or_none(record.get("away_team_id"))
        league_id = _int_or_none(record.get("league_id"))
        jobs: list[tuple[str, str, str, dict]] = []
        base = {
            "kickoff_at": record.get("kickoff_at"),
            "competition_name": record.get("competition_name"),
            "match_id": int(match_id),
            "home_team_id": home_id,
            "away_team_id": away_id,
            "league_id": league_id,
        }
        if home_id is None and str(record.get("home_team") or "").strip():
            ctx = {
                **base,
                "slot": "home",
                "opponent_name": record.get("away_team"),
                "opponent_id": away_id,
                "opponent_mapped": away_id is not None,
            }
            jobs.append(("team", str(record["home_team"]), f"matches.id={match_id}#home_team", ctx))
        if away_id is None and str(record.get("away_team") or "").strip():
            ctx = {
                **base,
                "slot": "away",
                "opponent_name": record.get("home_team"),
                "opponent_id": home_id,
                "opponent_mapped": home_id is not None,
            }
            jobs.append(("team", str(record["away_team"]), f"matches.id={match_id}#away_team", ctx))
        if league_id is None and str(record.get("competition_name") or "").strip():
            jobs.append(
                (
                    "competition",
                    str(record["competition_name"]),
                    f"matches.id={match_id}#competition_name",
                    base,
                )
            )
        for kind, name, source_ref, ctx in jobs:
            try:
                observed.append(
                    admit(conn, kind, name, source, source_ref, ctx, allow_create=False)
                )
            except (Exception, SystemExit) as exc:
                errors.append({"match_id": int(match_id), "name": name, "error": str(exc)})
    return {"observed": observed, "errors": errors, "match_text_rewritten": False}


def preview_names(conn: sqlite3.Connection, items: list[dict], *, source: str = "") -> list[dict]:
    """干跑预览。不写库。items 含 kind、raw_name，以及可选的 source_ref、context。"""
    preview: list[dict] = []
    for item in items:
        verdict = evaluate(
            conn,
            str(item.get("kind") or "team"),
            item.get("raw_name"),
            item.get("context") if isinstance(item.get("context"), dict) else None,
            source=source,
            source_ref=str(item.get("source_ref") or ""),
        )
        preview.append(
            {
                "raw_name": item.get("raw_name"),
                "kind": item.get("kind"),
                "step": verdict["step"],
                "action": verdict["action"],
                "reason": verdict["reason"],
                "standard_id": verdict["standard_id"],
                "message": verdict["message"],
            }
        )
    return preview
