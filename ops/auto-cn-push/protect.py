"""历史齐套未推的覆盖选择，以及回执 / ready 门闩。

齐套与否只使用 check_close_complete.evaluate 的结果，本模块不重新定义
平博 rule/close，也不把澳门盘当作齐套。
"""
from __future__ import annotations

from datetime import date, timedelta


def parse_day(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def norm_hash(value) -> str:
    return str(value or "").strip().lower()


def contiguous_boundary(pushed: set[str]) -> str | None:
    """从最早已推日连续向后，停在第一个没有 pushed 标记的日期之前。

    不能用最大已推日：中间缺口会被跳过。
    """
    return _walk_boundary(pushed)


def resolved_boundary(pushed: set[str], checked_empty: set[str]) -> str | None:
    """已推或已核对无场的连续前缀。缺盘日不在这里，所以不会把后面的日子挡在扫描之外。"""
    return _walk_boundary(set(pushed) | set(checked_empty))


def _walk_boundary(days: set[str]) -> str | None:
    parsed = [item for item in (parse_day(day) for day in days) if item is not None]
    if not parsed:
        return None
    present = set(parsed)
    day = min(present)
    last: date | None = None
    while day in present:
        last = day
        day += timedelta(days=1)
    return last.isoformat() if last else None


def scan_days(boundary: str | None, today: date, lookback_days: int) -> list[str]:
    """已推边界的次日到今天；尚无标记时只回看 lookback_days。"""
    if lookback_days < 0:
        raise ValueError("lookback_days must be >= 0")
    if boundary:
        start = date.fromisoformat(boundary) + timedelta(days=1)
    else:
        start = today - timedelta(days=lookback_days)
    if start > today:
        return []
    out: list[str] = []
    day = start
    while day <= today:
        out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def _evidence(row: dict) -> dict:
    return {
        "jc_day": row.get("jc_day"),
        "complete": bool(row.get("complete")),
        "reason": row.get("reason"),
        "n_matches": row.get("n_matches"),
        "n_matches_in_db": row.get("n_matches_in_db"),
        "pin_close_ok": row.get("pin_close_ok"),
        "missing_match_uids": list(row.get("missing_match_uids") or []),
        "excluded_dropped_from_csl": list(row.get("excluded_dropped_from_csl") or []),
    }


def _is_no_matches(row: dict) -> bool:
    if row.get("reason") == "no_matches":
        return True
    return row.get("n_matches") == 0 and not row.get("complete")


def select_coverage(
    reports: list[dict],
    *,
    pushed: set[str],
    today: date,
    boundary: str | None,
) -> dict:
    """选择本次整库包可以声明覆盖的竞彩日。

    过去未齐套的日子记入缺口账本，不标已推，也不挡住更晚的齐套日。
    无场次日不标已推。今天未齐套不标。未来日期不声明。
    已经推过的日子只跳过，不用其中的最大日期当边界。
    整库里含有缺口日的行，也不表示那个缺口日齐套。
    """
    claimed: list[str] = []
    claimed_evidence: list[dict] = []
    no_matches: list[str] = []
    gap_days: list[dict] = []
    in_progress: list[dict] = []
    ignored_pushed: list[str] = []
    future: list[dict] = []

    ordered = sorted(reports, key=lambda row: str(row.get("jc_day") or ""))
    for row in ordered:
        day = str(row.get("jc_day") or "")
        current = parse_day(day)
        if current is None:
            continue
        if day in pushed:
            ignored_pushed.append(day)
            continue
        if current > today:
            future.append(_evidence(row))
            continue
        if _is_no_matches(row):
            no_matches.append(day)
            continue
        if not row.get("complete"):
            item = _evidence(row)
            if current == today:
                in_progress.append(item)
            else:
                gap_days.append(item)
            continue
        claimed.append(day)
        claimed_evidence.append(_evidence(row))

    scan_from = ordered[0]["jc_day"] if ordered else None
    scan_through = today.isoformat()
    return {
        "boundary": boundary,
        "scan_from": scan_from,
        "scan_through": scan_through,
        "claimed_jc_days": claimed,
        "claimed_evidence": claimed_evidence,
        "no_matches": no_matches,
        "gap_days": gap_days,
        "blocking_gap": gap_days[0] if gap_days else None,
        "withheld_after_gap": [],
        "in_progress": in_progress,
        "already_pushed_ignored": ignored_pushed,
        "future_not_claimed": future,
        "note": "整库快照可以含缺口日的行；缺口日不在 claimed_jc_days 里，不会标成已推",
    }


def health_ok(confirm: dict | None) -> bool:
    if not confirm or confirm.get("health_ok") is not True:
        return False
    body = str(confirm.get("health_body") or "")
    return '"ok":true' in body.replace(" ", "") or '"ok": true' in body


def remote_reuse_ok(pending: dict, receipt: dict, confirm: dict | None) -> tuple[bool, str]:
    """同 db、不同 gz 时，只接受另纸写下的远端库哈希。不改旧回执。"""
    if not confirm:
        return False, "needs_remote_confirm"
    if not health_ok(confirm):
        return False, "remote_health_failed"
    pend_db = norm_hash(pending.get("db_sha256"))
    pend_gz = norm_hash(pending.get("gz_sha256"))
    receipt_db = norm_hash(receipt.get("db_sha256"))
    receipt_gz = norm_hash(receipt.get("gz_sha256"))
    if norm_hash(confirm.get("remote_db_sha256")) != pend_db or receipt_db != pend_db:
        return False, "remote_db_mismatch"
    if not receipt_gz or receipt_gz == pend_gz:
        return False, "not_a_gz_difference"
    if norm_hash(confirm.get("receipt_gz_sha256")) != receipt_gz:
        return False, "receipt_gz_not_cited"
    if norm_hash(confirm.get("pending_gz_sha256")) != pend_gz:
        return False, "confirm_gz_mismatch"
    if confirm.get("rewrote_receipt_gz"):
        return False, "forged_receipt"
    return True, "remote_db_reused"


def delivery_decision(
    pending: dict | None,
    ready: dict | None,
    receipts: list[dict],
    package: dict | None = None,
    remote_confirm: dict | None = None,
) -> dict:
    """根据 pending、投放 ready 和各机器 LAST_PUSHED 决定能否推、能否标记。

    任一回执的 db/gz 与 pending 一致即可标记，另一台机器的旧回执不触发重推。
    ready 或包与 pending 不一致时拒绝，避免把旧 ready / 旧包投出去。
    """
    result = {
        "allow_push": False,
        "allow_mark": False,
        "push_again": False,
        "needs_push": False,
        "matched_receipt": None,
        "stale_receipts": [],
        "reason": "",
    }
    if not pending:
        result["reason"] = "no_pending"
        return result
    pend_db = norm_hash(pending.get("db_sha256"))
    pend_gz = norm_hash(pending.get("gz_sha256"))
    if not pend_db or not pend_gz:
        result["reason"] = "pending_hash_missing"
        return result
    package = package or pending
    pkg_db = norm_hash(package.get("db_sha256"))
    pkg_gz = norm_hash(package.get("gz_sha256"))
    if pkg_db != pend_db or pkg_gz != pend_gz:
        result["reason"] = "package_hash_mismatch"
        return result

    def _ready_ok(item: dict | None) -> bool:
        if not item:
            return False
        return norm_hash(item.get("db_sha256")) == pend_db and norm_hash(item.get("gz_sha256")) == pend_gz

    ready_ok = _ready_ok(ready)
    ready_mismatch = ready is not None and not ready_ok
    matched = None
    stale: list[dict] = []
    db_only: list[dict] = []
    for rec in receipts:
        source = str(rec.get("source") or rec.get("name") or "receipt")
        db = norm_hash(rec.get("db_sha256"))
        gz = norm_hash(rec.get("gz_sha256"))
        if db == pend_db and gz == pend_gz:
            if matched is None:
                matched = source
            continue
        if db == pend_db and gz and gz != pend_gz:
            db_only.append(rec)
            continue
        if db or gz:
            stale.append({"source": source, "db_sha256": db, "gz_sha256": gz})
    result["stale_receipts"] = stale
    if db_only and not matched:
        reuse_ok, reuse_reason = remote_reuse_ok(pending, db_only[0], remote_confirm)
        result["receipt_gz_sha256"] = norm_hash(db_only[0].get("gz_sha256"))
        if reuse_ok:
            result["allow_mark"] = True
            result["allow_push"] = False
            result["push_again"] = False
            result["needs_push"] = False
            result["matched_receipt"] = str(db_only[0].get("source") or "receipt")
            result["reason"] = "remote_db_reused"
            result["rewrote_receipt_gz"] = False
            return result
        result["reason"] = reuse_reason
        result["needs_remote_confirm"] = reuse_reason == "needs_remote_confirm"
        result["allow_push"] = False
        result["allow_mark"] = False
        result["push_again"] = False
        return result
    if matched:
        # 已有一台机器的回执与 pending 一致。旧 ready 不能再投，也不需要重推。
        result["allow_mark"] = True
        result["allow_push"] = False
        result["push_again"] = False
        result["needs_push"] = False
        result["matched_receipt"] = matched
        result["reason"] = "receipt_match"
        if ready_mismatch:
            result["ready_ignored"] = "old_ready"
        return result
    if ready_mismatch:
        result["reason"] = "old_ready"
        result["allow_push"] = False
        result["push_again"] = False
        return result
    if ready_ok:
        result["allow_push"] = True
        result["allow_mark"] = False
        result["push_again"] = False
        result["needs_push"] = True
        result["reason"] = "await_receipt"
        return result
    result["reason"] = "receipt_mismatch" if receipts else "no_receipt"
    result["push_again"] = False
    result["needs_push"] = False
    return result


def acquire_push_claim(existing: dict | None, machine: str, pending: dict, now_epoch: float, ttl_sec: int = 3600) -> dict:
    """同一 pending 同时只允许一台机器推。另一台持有未过期声明时拒绝。"""
    pend_db = norm_hash(pending.get("db_sha256"))
    pend_gz = norm_hash(pending.get("gz_sha256"))
    if existing and norm_hash(existing.get("db_sha256")) == pend_db and norm_hash(existing.get("gz_sha256")) == pend_gz:
        age = now_epoch - float(existing.get("claimed_epoch") or 0)
        holder = str(existing.get("machine") or "")
        if holder and holder != machine and age < ttl_sec:
            return {"ok": False, "reason": "other_machine_holds_claim", "holder": holder}
    return {
        "ok": True,
        "reason": "claimed",
        "claim": {
            "machine": machine,
            "db_sha256": pend_db,
            "gz_sha256": pend_gz,
            "claimed_epoch": now_epoch,
        },
    }


def windows_install_allowed(*, task_status: str | None, lock_held: bool) -> tuple[bool, str]:
    """禁用计划任务不等于等待正在运行的实例结束。仍在跑或已有安装锁时拒绝换包。"""
    if lock_held:
        return False, "install_lock_held"
    if (task_status or "").lower() == "running":
        return False, "task_still_running"
    return True, "ok"
