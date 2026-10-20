#!/usr/bin/env python3
"""把雷速体育写法升成常用名。只改映射表和名称包。

默认干跑，不写文件，也不写数据库。加上 --apply 才执行。

输入可以是 CSV 或 JSON 数组。每一行至少要有雷速常用名，并指出实体：
- 直接给出 kind（team 或 competition）和 entity_id；
- 或给出 match_id / 开赛时间加主客队名，再由 side 指定主队、客队或赛事。

匹配时开赛时间使用前后十五分钟。对不上、对上多场、身份编号仍空、性别或青年队标记不一致时跳过，不猜挂。
写入内容只包括 name_zh_canonical、promotion_source=leisutiyu、pending_confirm=false，
以及把旧常用名保留为别名。名称包 teams.csv 或 competitions.csv 同步改同一行。
不修改 matches.home_team、matches.away_team、matches.competition_name。

用法：
  python api/scripts/promote_leisuti_names.py --input names.csv --db data/app.db
  python api/scripts/promote_leisuti_names.py --input names.csv --db data/app.db --apply --audit audit.json
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_ROOT = ROOT / "api"
sys.path.insert(0, str(API_ROOT))

from app.alias_pool import detect_ambiguity, norm_key  # noqa: E402

_BEIJING = timezone(timedelta(hours=8))
_WINDOW = timedelta(minutes=15)
_PROMOTION = "leisutiyu"
_LOCKED = frozenset({"human_finalize", "user_correct"})
_LEISU = frozenset({"leisutiyu", "leisu", "雷速", "雷速体育"})


def _markers_conflict(left: str | None, right: str | None) -> bool:
    return detect_ambiguity(left) != detect_ambiguity(right)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
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


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _ensure_mapping_columns(conn: sqlite3.Connection, table: str) -> None:
    columns = _columns(conn, table)
    if "promotion_source" not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN promotion_source TEXT")
    if "promotion" not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN promotion TEXT")
    if "pending_confirm" not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN pending_confirm TEXT")


def load_rows(path: Path) -> list[dict]:
    """读取 CSV 或 JSON。缺常用名的行留到后面逐条跳过，以免整批无法审计。"""
    if not path.is_file():
        raise SystemExit(f"找不到输入：{path}")
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise SystemExit("JSON 输入必须是数组。")
        return [item for item in payload if isinstance(item, dict)]
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _fetch_dicts(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    cursor = conn.execute(sql, params)
    names = [item[0] for item in cursor.description or ()]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _team_names(conn: sqlite3.Connection, team_id: int | None) -> set[str]:
    if team_id is None:
        return set()
    names: set[str] = set()
    row = conn.execute("SELECT name_zh_canonical FROM teams WHERE id = ?", (team_id,)).fetchone()
    if row and row[0]:
        names.add(norm_key(row[0]))
    if _table_exists(conn, "team_aliases"):
        for alias in conn.execute("SELECT alias FROM team_aliases WHERE team_id = ?", (team_id,)):
            if alias[0]:
                names.add(norm_key(alias[0]))
    return names


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    found = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return found is not None


def _name_hits(conn: sqlite3.Connection, match: dict, side: str, raw_name: str | None) -> bool:
    if not raw_name:
        return False
    key = norm_key(raw_name)
    if not key:
        return False
    column = "home_team" if side == "home" else "away_team"
    id_column = "home_team_id" if side == "home" else "away_team_id"
    if norm_key(match.get(column)) == key:
        return True
    return key in _team_names(conn, match.get(id_column))


def _score_ok(conn: sqlite3.Connection, match_id: int, home_goals, away_goals) -> bool | None:
    if home_goals in (None, "") or away_goals in (None, ""):
        return None
    if not _table_exists(conn, "results"):
        return None
    row = conn.execute(
        "SELECT home_goals, away_goals FROM results WHERE match_id = ?",
        (match_id,),
    ).fetchone()
    if row is None or row[0] is None or row[1] is None:
        return None
    try:
        return (int(row[0]), int(row[1])) == (int(home_goals), int(away_goals))
    except (TypeError, ValueError):
        return None


def find_match(conn: sqlite3.Connection, row: dict) -> tuple[dict | None, str | None]:
    """返回唯一场次，或跳过原因。"""
    match_id = row.get("match_id")
    kickoff = _parse_dt(row.get("kickoff_at"))
    columns = _columns(conn, "matches")
    if "id" not in columns:
        return None, "no_matches"
    if match_id not in (None, ""):
        try:
            numeric_id = int(match_id)
        except (TypeError, ValueError):
            return None, "bad_match_id"
        found = _fetch_dicts(conn, "SELECT * FROM matches WHERE id = ?", (numeric_id,))
        if not found:
            return None, "no_match"
        chosen = found[0]
        remote = _parse_dt(chosen.get("kickoff_at"))
        if kickoff is not None and remote is not None and abs(remote - kickoff) > _WINDOW:
            return None, "kickoff_mismatch"
        return chosen, None
    if kickoff is None:
        return None, "missing_match"
    day = kickoff.astimezone(_BEIJING).date()
    dates = [(day + timedelta(days=offset)).isoformat() for offset in (-1, 0, 1)]
    clause = " OR ".join("kickoff_at LIKE ?" for _ in dates)
    candidates = _fetch_dicts(
        conn,
        f"SELECT * FROM matches WHERE kickoff_at IS NOT NULL AND ({clause})",
        tuple(f"{item}%" for item in dates),
    )
    accepted = []
    for match in candidates:
        remote = _parse_dt(match.get("kickoff_at"))
        if remote is None or abs(remote - kickoff) > _WINDOW:
            continue
        if not _name_hits(conn, match, "home", row.get("home")):
            continue
        if not _name_hits(conn, match, "away", row.get("away")):
            continue
        agreed = _score_ok(conn, int(match["id"]), row.get("home_goals"), row.get("away_goals"))
        if agreed is False:
            continue
        accepted.append(match)
    if len(accepted) == 1:
        return accepted[0], None
    if len(accepted) > 1:
        return None, "ambiguous_match"
    return None, "no_match"


def resolve_target(conn: sqlite3.Connection, row: dict) -> tuple[str, int] | tuple[None, str]:
    """解析到 team 或 competition 的身份编号。失败时第二个值是跳过原因。"""
    kind = str(row.get("kind") or "team").strip() or "team"
    if kind not in ("team", "competition"):
        return None, "bad_kind"
    entity = row.get("entity_id")
    if entity not in (None, ""):
        try:
            return kind, int(entity)
        except (TypeError, ValueError):
            return None, "bad_entity_id"
    side = str(row.get("side") or "").strip()
    if kind == "competition":
        side = side or "competition"
    match, reason = find_match(conn, row)
    if match is None:
        return None, reason or "no_match"
    if side in ("competition", "league"):
        league_id = match.get("league_id")
        if league_id in (None, ""):
            return None, "unresolved_id"
        return "competition", int(league_id)
    if side not in ("home", "away"):
        return None, "missing_side"
    column = "home_team_id" if side == "home" else "away_team_id"
    entity_id = match.get(column)
    if entity_id in (None, ""):
        return None, "unresolved_id"
    return "team", int(entity_id)


def _alias_owner(conn: sqlite3.Connection, alias_table: str, id_column: str, alias: str) -> int | None:
    if not _table_exists(conn, alias_table):
        return None
    row = conn.execute(
        f"SELECT {id_column} FROM {alias_table} WHERE alias = ?",
        (alias,),
    ).fetchone()
    if row is None:
        return None
    return int(row[0])


def _read_entity(conn: sqlite3.Connection, table: str, entity_id: int) -> dict | None:
    columns = _columns(conn, table)
    if "name_zh_canonical" not in columns:
        return None
    selected = ["id", "name_zh_canonical"]
    for name in ("promotion_source", "promotion", "pending_confirm"):
        if name in columns:
            selected.append(name)
    rows = _fetch_dicts(
        conn,
        f"SELECT {', '.join(selected)} FROM {table} WHERE id = ?",
        (entity_id,),
    )
    return rows[0] if rows else None


def plan_one(conn: sqlite3.Connection, row: dict, batch: str) -> dict:
    """只计划，不写库。"""
    preferred = str(row.get("preferred_name") or row.get("leisuti_name") or "").strip()
    item = {
        "kind": row.get("kind") or "team",
        "preferred_name": preferred,
        "entity_id": None,
        "old": None,
        "new": preferred,
        "action": "skip",
        "reason": None,
        "batch": batch,
        "match_text_rewritten": False,
    }
    if not preferred:
        item["reason"] = "missing_preferred"
        return item
    kind, target = resolve_target(conn, row)
    if kind is None:
        item["reason"] = target
        return item
    item["kind"] = kind
    item["entity_id"] = target
    table = "teams" if kind == "team" else "leagues"
    alias_table = "team_aliases" if kind == "team" else "league_aliases"
    id_column = "team_id" if kind == "team" else "league_id"
    current = _read_entity(conn, table, target)
    if current is None:
        item["reason"] = "missing_entity"
        return item
    old = str(current.get("name_zh_canonical") or "")
    item["old"] = old
    promo = str(current.get("promotion_source") or current.get("promotion") or "").strip()
    if _markers_conflict(preferred, old):
        item["reason"] = "marker_conflict"
        return item
    if promo in _LOCKED and old != preferred:
        item["reason"] = "human_locked"
        return item
    owner = _alias_owner(conn, alias_table, id_column, preferred)
    if owner is not None and owner != target:
        item["reason"] = "alias_conflict"
        return item
    other = conn.execute(
        f"SELECT id FROM {table} WHERE name_zh_canonical = ? AND id != ?",
        (preferred, target),
    ).fetchone()
    if other is not None:
        item["reason"] = "canonical_conflict"
        return item
    if old == preferred and promo in _LEISU and str(current.get("pending_confirm") or "").lower() != "true":
        item["action"] = "already_same"
        item["reason"] = "already_leisuti"
        return item
    item["action"] = "promote"
    item["alias_kept"] = old if old and old != preferred else None
    item["alias_source"] = f"leisutiyu:{batch}"
    item["rollback"] = {
        "name_zh_canonical": old,
        "promotion_source": current.get("promotion_source"),
        "promotion": current.get("promotion"),
        "pending_confirm": current.get("pending_confirm"),
    }
    return item


def apply_db(conn: sqlite3.Connection, item: dict) -> None:
    """按计划改映射表。调用方负责事务。不改比赛文本列。"""
    kind = item["kind"]
    table = "teams" if kind == "team" else "leagues"
    alias_table = "team_aliases" if kind == "team" else "league_aliases"
    id_column = "team_id" if kind == "team" else "league_id"
    _ensure_mapping_columns(conn, table)
    conn.execute(
        f"""
        UPDATE {table}
           SET name_zh_canonical = ?, promotion_source = ?, promotion = ?, pending_confirm = ?
         WHERE id = ?
        """,
        (item["new"], _PROMOTION, _PROMOTION, "false", item["entity_id"]),
    )
    old = item.get("alias_kept")
    if old:
        owner = _alias_owner(conn, alias_table, id_column, old)
        if owner is None:
            conn.execute(
                f"INSERT INTO {alias_table} (alias, {id_column}, source) VALUES (?, ?, ?)",
                (old, item["entity_id"], item["alias_source"]),
            )
            item["alias_inserted"] = old
        elif owner != item["entity_id"]:
            item["alias_insert_skipped"] = "old_name_alias_conflict"


def _pack_path(kind: str, teams_csv: Path, competitions_csv: Path) -> Path:
    return teams_csv if kind == "team" else competitions_csv


def update_pack_text(text: str, item: dict) -> tuple[str, str]:
    """改名称包里对应的一行。多行冲突时不改，并返回原因。"""
    lines = text.splitlines(keepends=True)
    if not lines:
        lines = ["canonical,aliases,note,pending_confirm,promotion\n"]
    header = lines[0]
    newline = "\n" if header.endswith("\n") else ""
    body = []
    hits = []
    for index, line in enumerate(lines[1:], start=1):
        raw = line.rstrip("\r\n")
        if not raw:
            body.append((index, None))
            continue
        parts = next(csv.reader([raw]))
        body.append((index, parts))
        if len(parts) < 1:
            continue
        canonical = parts[0].strip()
        aliases = [part.strip() for part in (parts[1].split("|") if len(parts) > 1 else []) if part.strip()]
        names = {canonical, *aliases}
        if item["new"] in names or (item.get("old") and item["old"] in names):
            hits.append(index)
    if len(hits) > 1:
        return text, "pack_ambiguous"
    old = item.get("old") or ""
    new = item["new"]
    if len(hits) == 1:
        index = hits[0]
        parts = next(part for line_index, part in body if line_index == index and part is not None)
        while len(parts) < 5:
            parts.append("")
        aliases = [part.strip() for part in parts[1].split("|") if part.strip()]
        aliases = [name for name in aliases if name not in (new, old)]
        if old and old != new:
            aliases.append(old)
        parts[0] = new
        parts[1] = "|".join(dict.fromkeys(aliases))
        parts[3] = "false"
        parts[4] = _PROMOTION
        rendered = _render_csv(parts)
        ending = "\n" if lines[index].endswith("\n") else ""
        lines[index] = rendered + ending
        return "".join(lines), "updated"
    aliases = old if old and old != new else ""
    rendered = _render_csv([new, aliases, f"leisutiyu:{item.get('batch') or ''}", "false", _PROMOTION])
    if lines and not lines[-1].endswith("\n"):
        lines[-1] = lines[-1] + "\n"
    lines.append(rendered + newline)
    return "".join(lines), "appended"


def _render_csv(parts: list[str]) -> str:
    from io import StringIO

    buffer = StringIO()
    writer = csv.writer(buffer, lineterminator="")
    writer.writerow(parts)
    return buffer.getvalue()


def promote(rows: list[dict], conn: sqlite3.Connection | None, *, apply: bool, batch: str,
            teams_csv: Path, competitions_csv: Path) -> dict:
    """干跑或写入。没有数据库连接时，只有带 entity_id 的行也无法读旧名，全部跳过。"""
    planned = []
    if conn is None:
        for row in rows:
            planned.append({
                "action": "skip",
                "reason": "missing_db",
                "preferred_name": row.get("preferred_name") or row.get("leisuti_name"),
                "match_text_rewritten": False,
            })
    else:
        for row in rows:
            planned.append(plan_one(conn, row, batch))
    pack_notes = []
    if apply and conn is not None:
        texts = {
            "team": teams_csv.read_text(encoding="utf-8") if teams_csv.is_file() else "",
            "competition": competitions_csv.read_text(encoding="utf-8") if competitions_csv.is_file() else "",
        }
        changed: set[str] = set()
        for item in planned:
            if item["action"] != "promote":
                continue
            apply_db(conn, item)
            kind = item["kind"]
            updated, note = update_pack_text(texts[kind], item)
            texts[kind] = updated
            changed.add(kind)
            item["pack"] = note
            pack_notes.append({"entity_id": item["entity_id"], "kind": kind, "pack": note})
        if "team" in changed:
            teams_csv.parent.mkdir(parents=True, exist_ok=True)
            teams_csv.write_text(texts["team"], encoding="utf-8")
        if "competition" in changed:
            competitions_csv.parent.mkdir(parents=True, exist_ok=True)
            competitions_csv.write_text(texts["competition"], encoding="utf-8")
        if changed:
            conn.commit()
    return {
        "mode": "apply" if apply else "dry-run",
        "batch": batch,
        "promotion_source": _PROMOTION,
        "match_text_rewritten": False,
        "pack_notes": pack_notes,
        "items": planned,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="雷速升格：默认干跑，--apply 只改映射表和名称包。")
    parser.add_argument("--input", type=Path, required=True, help="CSV 或 JSON 数组。")
    parser.add_argument("--db", type=Path, default=None, help="SQLite 数据库。不改比赛文本列。")
    parser.add_argument("--apply", action="store_true", help="写入映射表和名称包。省略时只干跑。")
    parser.add_argument("--audit", type=Path, default=None, help="审计 JSON 的写出路径。省略时只打印。")
    parser.add_argument("--batch", default=None, help="别名来源批次。默认 leisuti-日期。")
    parser.add_argument("--teams-csv", type=Path, default=API_ROOT / "resources" / "name-standard" / "teams.csv")
    parser.add_argument(
        "--competitions-csv",
        type=Path,
        default=API_ROOT / "resources" / "name-standard" / "competitions.csv",
    )
    args = parser.parse_args(argv)
    batch = args.batch or f"leisuti-{datetime.now(_BEIJING):%Y%m%d}"
    rows = load_rows(args.input)
    conn = None
    if args.db is not None:
        if not args.db.is_file():
            raise SystemExit(f"找不到数据库：{args.db}")
        conn = sqlite3.connect(args.db)
        if args.apply:
            conn.execute("BEGIN")
    try:
        report = promote(
            rows,
            conn,
            apply=args.apply,
            batch=batch,
            teams_csv=args.teams_csv,
            competitions_csv=args.competitions_csv,
        )
    finally:
        if conn is not None:
            conn.close()
    report["db"] = str(args.db) if args.db else None
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.audit is not None:
        args.audit.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
