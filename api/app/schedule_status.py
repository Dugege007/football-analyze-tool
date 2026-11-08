"""赛程缺口：推迟、取消，或其他（不能确定）。

只根据已经拿到的证据分类。从竞彩列表消失本身不能证明比赛取消。
未公告且开赛时刻变动不超过 20 分钟的，只记开赛修订次数，不记推迟。
约 15 分钟、没有公告的开赛漂移落在这道门槛里面，和一006、五204 的口径相同。

推迟作废仍按澳门第 67/2018 号行政命令第十一条的 24 小时。本模块不改那条时限。
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

TZ = timezone(timedelta(hours=8))

# 未公告的小幅改时。超过这道门槛、同一竞彩编号的开赛时刻改动才记推迟。
DRIFT_NOT_POSTPONE = timedelta(minutes=20)

STATUS_POSTPONED = "postponed"
STATUS_CANCELLED = "cancelled"
STATUS_OTHER = "other"
STATUSES = (STATUS_POSTPONED, STATUS_CANCELLED, STATUS_OTHER)

EVIDENCE_DROPPED = "dropped_from_csl"
EVIDENCE_CANCELLED = "5df_status_cancelled"
EVIDENCE_POSTPONED = "5df_status_postponed"
EVIDENCE_RESCHEDULED = "kickoff_rescheduled"
OWN_EVIDENCE = frozenset({
    EVIDENCE_DROPPED, EVIDENCE_CANCELLED, EVIDENCE_POSTPONED, EVIDENCE_RESCHEDULED,
})

# 缺盘格子的原因代码。中文说明在 reason_codes，悬停再换成这场的 schedule_note。
CELL_REASON = {
    STATUS_POSTPONED: "schedule_postponed",
    STATUS_CANCELLED: "schedule_cancelled",
    STATUS_OTHER: "schedule_other",
}

_SOURCE_CANCELLED = frozenset({"cancelled", "canceled"})
_SOURCE_POSTPONED = frozenset({"postponed", "delayed"})
_QUOTE_KEYS = ("line", "home_water", "away_water", "home", "draw", "away", "over", "under")


def parse_cn(value: Any) -> Optional[datetime]:
    """解析开赛时刻并换算到北京时间。解析不了就返回空。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def calendar_date(value: Any) -> Optional[str]:
    """开赛时刻在北京时间下的日历日。"""
    dt = parse_cn(value)
    return dt.date().isoformat() if dt else None


def format_shanghai(value: Any) -> Optional[str]:
    """北京时间，精确到分钟，形如 2026-11-09 19:00。"""
    dt = parse_cn(value)
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d %H:%M")


def is_unannounced_drift(delta: Optional[timedelta]) -> bool:
    """变动不超过 20 分钟时，不当作推迟。"""
    if delta is None:
        return False
    return abs(delta) <= DRIFT_NOT_POSTPONE


def source_schedule_status(status: Optional[str]) -> Optional[str]:
    """只认来源写明的取消或推迟。其余状态不猜测。"""
    text = (status or "").strip().lower()
    if text in _SOURCE_CANCELLED:
        return STATUS_CANCELLED
    if text in _SOURCE_POSTPONED:
        return STATUS_POSTPONED
    return None


def classify_schedule(
    *,
    source_status: Optional[str],
    absent_from_list: bool,
    kickoff_changed: bool,
    delta: Optional[timedelta],
    placeholder_confirm: bool,
    previous_status: Optional[str],
    previous_evidence: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """返回 (schedule_status, evidence)。两者都是空表示当前没有赛程缺口。

    来源写明取消或推迟时用那个结论。开赛时刻改动超过 20 分钟、且不是 12:00 占位转真时记推迟。
    只知道这场从竞彩列表消失时记其他，不记取消。回到列表且没有新的推迟或取消证据时，清掉仅因消失而记下的其他。
    """
    src = source_schedule_status(source_status)
    if src == STATUS_CANCELLED:
        return STATUS_CANCELLED, EVIDENCE_CANCELLED
    if src == STATUS_POSTPONED:
        return STATUS_POSTPONED, EVIDENCE_POSTPONED
    if (kickoff_changed and not placeholder_confirm and delta is not None
            and not is_unannounced_drift(delta)):
        return STATUS_POSTPONED, EVIDENCE_RESCHEDULED
    if absent_from_list:
        if previous_status in (STATUS_POSTPONED, STATUS_CANCELLED) and previous_evidence in OWN_EVIDENCE:
            return previous_status, previous_evidence
        return STATUS_OTHER, EVIDENCE_DROPPED
    if previous_status == STATUS_OTHER and previous_evidence == EVIDENCE_DROPPED:
        return None, None
    if previous_status in STATUSES and previous_evidence in OWN_EVIDENCE:
        return previous_status, previous_evidence
    return None, None


def schedule_note_zh(
    status: Optional[str],
    kickoff_actual: Any = None,
    kickoff_original: Any = None,
) -> Optional[str]:
    """给开赛格和缺盘格共用的一句中文。没有新开赛时间时不编造时刻。"""
    if status == STATUS_POSTPONED:
        actual = format_shanghai(kickoff_actual)
        original = format_shanghai(kickoff_original)
        if actual and actual != original:
            return f"推迟至 {actual}（北京时间）"
        return "比赛推迟，新的开赛时间尚未确认"
    if status == STATUS_CANCELLED:
        return "比赛取消"
    if status == STATUS_OTHER:
        return "已从竞彩列表消失，不能确定是推迟、取消还是其他原因"
    return None


def _row_get(row: Any, key: str) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        keys = row.keys()
    except AttributeError:
        return None
    if key not in keys:
        return None
    return row[key]


def match_schedule_fields(row: Any) -> dict[str, Any]:
    """比赛行上给接口用的赛程缺口字段。库里没有这些列时全部为空。"""
    status = _row_get(row, "schedule_status")
    if status not in STATUSES:
        status = None
    evidence = _row_get(row, "schedule_status_evidence") if status else None
    actual = _row_get(row, "kickoff_actual")
    original = _row_get(row, "kickoff_original")
    return {
        "schedule_status": status,
        "schedule_status_evidence": evidence,
        "schedule_note": schedule_note_zh(status, actual, original),
        "listing_role": "ticket",
        "list_date": _row_get(row, "jingcai_date"),
        "list_seq": None,
    }


def _empty_quote(cell: dict) -> bool:
    return all(cell.get(k) is None for k in _QUOTE_KEYS)


def stamp_market_blocks(status: Optional[str], *blocks: Any) -> None:
    """已分类的赛程缺口上，没有赔率的格子改记这场的原因，不再看起来像普通漏采。

    格子里已经有盘口或赔率时不动。
    """
    code = CELL_REASON.get(status or "")
    if not code:
        return

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "cell_status" in node and node.get("cell_status") != "ok" and _empty_quote(node):
                node["cell_status"] = "unavailable"
                node["cell_status_reason"] = code
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for block in blocks:
        walk(block)


def overlay_schedule_notes(item: dict) -> None:
    """原因代码的通用中文换成这场的说明。有新开赛时间时，缺盘悬停也写推迟至该时刻。"""
    note = (item.get("match") or {}).get("schedule_note")
    if not note:
        return

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            reason = node.get("cell_status_reason")
            if isinstance(reason, str) and reason in CELL_REASON.values():
                node["cell_status_reason_zh"] = note
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for key in ("ah", "x1x2", "ou", "jc_1x2", "jc_hhad"):
        walk(item.get(key))


def cross_day_kickoff_date(match: dict) -> Optional[str]:
    """推迟后的开赛日历日晚于原定开赛日历日时，返回新的日历日。同一天的推迟不另列一行。"""
    if match.get("schedule_status") != STATUS_POSTPONED:
        return None
    actual = calendar_date(match.get("kickoff_actual") or match.get("kickoff_at"))
    original = calendar_date(match.get("kickoff_original"))
    if not actual or not original or actual <= original:
        return None
    return actual


def _kick_sort(item: dict) -> tuple:
    match = item.get("match") or {}
    dt = parse_cn(match.get("kickoff_actual") or match.get("kickoff_at"))
    stamp = dt.timestamp() if dt else 10**18
    jc_no = match.get("jc_no")
    try:
        number = int(jc_no) if jc_no is not None else 10**9
    except (TypeError, ValueError):
        number = 10**9
    return (stamp, number, str(item.get("match_id") or ""))


def assign_list_positions(items: list[dict]) -> None:
    """原竞彩日按编号排。推迟到另一天的回声行，插进那一天按开赛时间应在的位置，不改动该天原有场次的先后。"""
    buckets: dict[str, list[dict]] = {}
    for item in items:
        match = item.setdefault("match", {})
        role = match.get("listing_role") or "ticket"
        match["listing_role"] = role
        if role != "kickoff":
            match["list_date"] = match.get("jingcai_date")
        buckets.setdefault(match.get("list_date") or "", []).append(item)
    for group in buckets.values():
        tickets = [it for it in group if it["match"].get("listing_role") != "kickoff"]
        echoes = [it for it in group if it["match"].get("listing_role") == "kickoff"]
        # 原有场次按竞彩编号排，不按开赛时刻重排。
        tickets.sort(key=lambda it: (
            _kick_sort(it)[1],
            str((it.get("match") or {}).get("jc_id") or ""),
            str(it.get("match_id") or ""),
        ))
        for index, item in enumerate(tickets):
            item["match"]["list_seq"] = (index + 1) * 1000
        used = {it["match"]["list_seq"] for it in tickets}
        ordered = sorted(tickets, key=_kick_sort)
        for echo in sorted(echoes, key=_kick_sort):
            ek = _kick_sort(echo)
            prev = 0
            nxt = None
            for ticket in ordered:
                if _kick_sort(ticket) <= ek:
                    prev = int(ticket["match"]["list_seq"])
                else:
                    nxt = int(ticket["match"]["list_seq"])
                    break
            if nxt is None:
                seq = prev + 500
            elif prev == 0:
                seq = max(1, nxt // 2)
            else:
                seq = (prev + nxt) // 2
            while seq in used:
                seq += 1
            used.add(seq)
            echo["match"]["list_seq"] = seq


def expand_listings(items: list[dict], *, date_from: str, date_to: str) -> list[dict]:
    """竞彩日区间内保留原编号顺序的一行。开赛日历日更晚、且落在区间内时再列一行。"""
    out: list[dict] = []
    for item in items:
        match = item.get("match") or {}
        jingcai_date = match.get("jingcai_date") or ""
        if date_from <= jingcai_date <= date_to:
            ticket = item
            ticket.setdefault("match", {})
            ticket["match"]["listing_role"] = "ticket"
            ticket["match"]["list_date"] = jingcai_date
            ticket["row_id"] = item.get("match_id") or match.get("match_id")
            out.append(ticket)
        echo_day = cross_day_kickoff_date(match)
        if echo_day and date_from <= echo_day <= date_to:
            echo = copy.deepcopy(item)
            echo["match"]["listing_role"] = "kickoff"
            echo["match"]["list_date"] = echo_day
            base = str(item.get("match_id") or match.get("match_id") or "")
            echo["row_id"] = f"{base}#kickoff"
            echo["match_id"] = base
            out.append(echo)
    assign_list_positions(out)
    out.sort(key=lambda it: (
        (it.get("match") or {}).get("list_date") or "",
        int((it.get("match") or {}).get("list_seq") or 0),
        str(it.get("row_id") or ""),
    ))
    return out


def exempt_from_close_requirement(status: Optional[str]) -> bool:
    """推迟、取消、其他这三类已经分类的缺口，不算普通临盘空洞。"""
    return status in STATUSES


def jingcai_day_close_report(
    conn,
    jingcai_date: str,
    *,
    book: str = "pinnacle",
    market: str = "asian",
    channel: str = "rule",
    point: str = "close",
    scope: str = "jingcai",
) -> dict[str, Any]:
    """这一竞彩日的规则临盘是否还缺普通场次。

    已写入比赛行的推迟、取消、其他不让整天失败。没有赛程状态、又没有临盘快照的场次仍然算缺。
    只读，不写库。比赛行还没有 schedule_status 列时，全部按普通场次计。
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(matches)")}
    has_status = "schedule_status" in cols
    status_sql = "schedule_status" if has_status else "NULL AS schedule_status"
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    rows = conn.execute(
        f"SELECT id, match_uid, {status_sql} AS schedule_status FROM matches "
        "WHERE jingcai_date = ? AND scope = ? ORDER BY id",
        (jingcai_date, scope),
    ).fetchall()
    missing: list[dict[str, Any]] = []
    exempt: list[dict[str, Any]] = []
    for row in rows:
        status = row["schedule_status"] if has_status else None
        if status not in STATUSES:
            status = None
        has_close = False
        if "odds_snapshot" in names:
            found = conn.execute(
                "SELECT 1 FROM odds_snapshot WHERE match_id = ? AND book = ? AND market = ? "
                "AND channel = ? AND point = ? LIMIT 1",
                (row["id"], book, market, channel, point),
            ).fetchone()
            has_close = found is not None
        if has_close:
            continue
        entry = {"match_id": row["id"], "match_uid": row["match_uid"], "schedule_status": status}
        if exempt_from_close_requirement(status):
            exempt.append(entry)
        else:
            missing.append(entry)
    return {
        "jingcai_date": jingcai_date,
        "book": book,
        "market": market,
        "channel": channel,
        "point": point,
        "complete": not missing,
        "missing": missing,
        "exempt": exempt,
    }


def effective_kickoff_iso(entry: dict) -> str:
    """模拟按时间往前走时用的开赛时刻。有实际开赛就用实际开赛，否则用 kickoff_at。"""
    dt = parse_cn(entry.get("kickoff_actual") or entry.get("kickoff_at"))
    return dt.isoformat() if dt else ""


def effective_kickoff_day(entry: dict) -> str:
    """实际开赛的北京时间日历日。没有开赛时刻时退回竞彩日。"""
    day = calendar_date(entry.get("kickoff_actual") or entry.get("kickoff_at"))
    if day:
        return day
    return entry.get("jingcai_date") or "_undated"


def paper_walk_rules(rules: dict[str, Any]) -> dict[str, Any]:
    """按实际开赛时间走资金曲线。已发布结算仍用原来的竞彩日加编号顺序，不要改传入的规则对象。"""
    out = dict(rules)
    out["walk"] = "kickoff"
    return out
