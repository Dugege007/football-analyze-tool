#!/usr/bin/env python3
"""把尚未入库的人气差值按临盘欧赔写入 stats.popularity_diff。

只补空值，不覆盖已有小数（含历史临盘结果）。
欧赔只读 odds_snapshot：market=euro_1x2、channel=rule、point=close，且胜平负三项齐全。
至少两家时对赔率做算术平均，否则按平博、威廉、澳门、皇冠、Bet365。
不读雷速，也不用只有主胜的 odds_euro_home。报价时间未知的 api_closing 不参与。

默认只打印将写入的场次。加 --apply 才更新数据库。数据表接口本身不写库，读时用同一套计算。

用法：
  python scripts/fill_popularity_diff.py --db data/app.db
  python scripts/fill_popularity_diff.py --db data/app.db --apply
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.popularity_diff import BOOKS, popularity_from_prices  # noqa: E402


def _is_api_closing(extras_json: str | None) -> bool:
    if not extras_json:
        return False
    try:
        extras = json.loads(extras_json)
    except (TypeError, ValueError):
        return False
    return isinstance(extras, dict) and extras.get("api_phase") == "closing"


def load_close_prices(conn: sqlite3.Connection) -> dict[int, dict[str, tuple[float, float, float, str]]]:
    """每场每家一条完整临盘。同一键多行时保留 recorded_at 较晚的一条。

    取值是 (主胜, 平, 客胜, recorded_at)。
    """
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "odds_snapshot" not in names:
        return {}
    found: dict[tuple[int, str], tuple[str, tuple[float, float, float]]] = {}
    for row in conn.execute(
        "SELECT match_id, book, price_home, price_draw, price_away, recorded_at, extras_json "
        "FROM odds_snapshot WHERE market='euro_1x2' AND channel='rule' AND point='close'"
    ):
        if row["book"] not in BOOKS or _is_api_closing(row["extras_json"]):
            continue
        if row["price_home"] is None or row["price_draw"] is None or row["price_away"] is None:
            continue
        key = (int(row["match_id"]), row["book"])
        recorded = row["recorded_at"] or ""
        prev = found.get(key)
        if prev is not None and prev[0] >= recorded:
            continue
        found[key] = (recorded, (float(row["price_home"]), float(row["price_draw"]), float(row["price_away"])))
    by_match: dict[int, dict[str, tuple[float, float, float, str]]] = {}
    for (match_id, book), (recorded, prices) in found.items():
        by_match.setdefault(match_id, {})[book] = (*prices, recorded)
    return by_match


def plan_updates(conn: sqlite3.Connection) -> list[tuple[int, float, str, str | None]]:
    """返回 (match_id, value, source, as_of)。只含 stats 行里人气差值仍为空的场次。"""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(stats)")}
    if "popularity_diff" not in cols:
        return []
    quotes = load_close_prices(conn)
    pending: list[tuple[int, float, str, str | None]] = []
    for row in conn.execute("SELECT match_id, popularity_diff FROM stats WHERE popularity_diff IS NULL"):
        match_quotes = quotes.get(int(row["match_id"]), {})
        prices = {book: quote[:3] for book, quote in match_quotes.items()}
        computed = popularity_from_prices(prices)
        if computed is None:
            continue
        recorded = [match_quotes[book][3] for book in computed["books"] if book in match_quotes and match_quotes[book][3]]
        as_of = max(recorded) if recorded else None
        pending.append((int(row["match_id"]), float(computed["value"]), computed["source"], as_of))
    return pending


def apply_updates(conn: sqlite3.Connection, pending: list[tuple[int, float, str, str | None]]) -> int:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(stats)")}
    wrote = 0
    for match_id, value, source, as_of in pending:
        sets = ["popularity_diff=?"]
        args: list[object] = [value]
        if "source_popularity" in cols:
            sets.append("source_popularity=?")
            args.append(source)
        if "as_of_popularity" in cols and as_of:
            sets.append("as_of_popularity=?")
            args.append(as_of)
        args.append(match_id)
        cur = conn.execute(
            f"UPDATE stats SET {', '.join(sets)} WHERE match_id=? AND popularity_diff IS NULL",
            args,
        )
        wrote += cur.rowcount
    conn.commit()
    return wrote


def main() -> None:
    parser = argparse.ArgumentParser(description="按临盘欧赔补写空的人气差值")
    parser.add_argument("--db", required=True, help="sqlite 路径")
    parser.add_argument("--apply", action="store_true", help="写入。缺省只打印")
    args = parser.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        pending = plan_updates(conn)
        print(f"可补 {len(pending)} 场")
        for match_id, value, source, _as_of in pending[:20]:
            print(f"  match_id={match_id} popularity_diff={value} source={source}")
        if len(pending) > 20:
            print(f"  … 其余 {len(pending) - 20} 场")
        if args.apply:
            wrote = apply_updates(conn, pending)
            print(f"已写入 {wrote} 场")
        else:
            print("未写入。确认后加 --apply")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
