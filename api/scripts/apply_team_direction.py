#!/usr/bin/env python3
"""按对照表检查并改正球队常用名与别名的方向。

对照表是 api/resources/name-standard/team-direction-corrections.csv。
每一行给出一个常用名和一个别名。脚本用这张表检查包内名册、前端悬停表，
以及回填种子里写反的一对；给出数据库路径时，再检查 teams 与 team_aliases。

默认只检查，不写文件。加上 --apply 才替换写反的方向。
本脚本不修改 matches.home_team、matches.away_team、matches.competition_name。
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CORRECTIONS = REPO / "api" / "resources" / "name-standard" / "team-direction-corrections.csv"
TEAMS_CSV = REPO / "api" / "resources" / "name-standard" / "teams.csv"
TEAM_NAME_TS = REPO / "web" / "src" / "names" / "teamName.ts"
TUPLE_SEED = REPO / "scripts" / "backfill" / "5df-multibook-history-queue" / "map_alias90_fixtures.py"

_TS_ENTRY = re.compile(r'^(\s*)"([^"]+)"\s*:\s*\[([^\]]*)\](.*)$')
_TUPLE_ENTRY = re.compile(r'\("([^"]+)",\s*\[([^\]]*)\]\)')
_QUOTED = re.compile(r'"([^"]*)"')


@dataclass(frozen=True)
class Correction:
    canonical: str
    alias: str
    note: str


@dataclass
class FileResult:
    """一个文件的检查或替换结果。"""

    label: str
    changed: bool
    problems: list[str]
    notes: list[str]
    text: str | None = None


def load_corrections(path: Path) -> list[Correction]:
    """读取对照表。常用名或别名为空时直接失败，避免把空字符串写进名册。"""
    if not path.is_file():
        raise SystemExit(f"找不到对照表：{path}")
    rows: list[Correction] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("canonical,"):
            continue
        parts = line.split(",", 2)
        if len(parts) < 2:
            raise SystemExit(f"对照表有无法识别的行：{line}")
        canonical, alias = parts[0].strip(), parts[1].strip()
        note = parts[2].strip() if len(parts) > 2 else ""
        if not canonical or not alias or canonical == alias:
            raise SystemExit(f"对照表这一行的常用名和别名不成立：{line}")
        rows.append(Correction(canonical, alias, note))
    if not rows:
        raise SystemExit(f"对照表里没有可用的行：{path}")
    return rows


def _split_aliases(cell: str) -> list[str]:
    return [part.strip() for part in cell.split("|") if part.strip()]


def _join_aliases(names: list[str]) -> str:
    seen: list[str] = []
    for name in names:
        if name and name not in seen:
            seen.append(name)
    return "|".join(seen)


def _quoted_items(cell: str) -> list[str]:
    return _QUOTED.findall(cell)


def fix_teams_csv(text: str, corrections: list[Correction]) -> FileResult:
    """改正包内 teams.csv。只调换写反的常用名和别名，不新增整支球队。"""
    problems: list[str] = []
    notes: list[str] = []
    lines = text.splitlines(keepends=True)
    changed = False
    for item in corrections:
        hits = []
        for index, line in enumerate(lines):
            raw = line.rstrip("\r\n")
            if not raw or raw.startswith("canonical,"):
                continue
            parts = raw.split(",")
            if len(parts) < 2:
                continue
            name, aliases = parts[0].strip(), _split_aliases(parts[1])
            if name in (item.canonical, item.alias) or item.canonical in aliases or item.alias in aliases:
                hits.append((index, parts, name, aliases))
        canonical_rows = [hit for hit in hits if hit[2] == item.canonical]
        alias_rows = [hit for hit in hits if hit[2] == item.alias]
        if len(canonical_rows) > 1 or len(alias_rows) > 1:
            problems.append(f"名册里「{item.canonical}」与「{item.alias}」出现了多行，脚本没有合并。")
            continue
        if canonical_rows and alias_rows:
            problems.append(
                f"名册里「{item.canonical}」和「{item.alias}」各有一行，脚本不能判断该合并到哪一行。"
            )
            continue
        if canonical_rows:
            index, parts, _name, aliases = canonical_rows[0]
            if item.alias in aliases:
                notes.append(f"名册中「{item.canonical}」的别名已包含「{item.alias}」。")
                continue
            aliases.append(item.alias)
            parts[1] = _join_aliases(aliases)
            newline = "\n" if lines[index].endswith("\n") else ""
            lines[index] = ",".join(parts) + newline
            changed = True
            notes.append(f"已把「{item.alias}」补进「{item.canonical}」的别名。")
            continue
        if alias_rows:
            index, parts, _name, aliases = alias_rows[0]
            aliases = [item.alias if name == item.canonical else name for name in aliases]
            if item.alias not in aliases:
                aliases.append(item.alias)
            aliases = [name for name in aliases if name != item.canonical]
            parts[0] = item.canonical
            parts[1] = _join_aliases(aliases)
            newline = "\n" if lines[index].endswith("\n") else ""
            lines[index] = ",".join(parts) + newline
            changed = True
            notes.append(f"已把名册常用名从「{item.alias}」改为「{item.canonical}」，「{item.alias}」改为别名。")
            continue
        problems.append(f"名册里找不到「{item.canonical}」或「{item.alias}」。")
    return FileResult("包内球队名册", changed, problems, notes, "".join(lines))


def fix_team_name_ts(text: str, corrections: list[Correction]) -> FileResult:
    """改正 teamName.ts 里悬停表的键。键是页面常用名，数组里是别名。"""
    problems: list[str] = []
    notes: list[str] = []
    lines = text.splitlines(keepends=True)
    entries: list[tuple[int, str, str, list[str], str]] = []
    for index, line in enumerate(lines):
        matched = _TS_ENTRY.match(line.rstrip("\r\n"))
        if not matched:
            continue
        indent, key, inner, tail = matched.groups()
        entries.append((index, indent, key, _quoted_items(inner), tail))
    changed = False
    for item in corrections:
        keyed = [entry for entry in entries if entry[2] in (item.canonical, item.alias)]
        if len(keyed) > 1:
            problems.append(f"前端悬停表里「{item.canonical}」和「{item.alias}」同时成了键，脚本没有合并。")
            continue
        if not keyed:
            mentioned = [
                entry for entry in entries
                if item.canonical in entry[3] or item.alias in entry[3]
            ]
            if mentioned:
                problems.append(
                    f"前端悬停表没有以「{item.canonical}」为键，这一对出现在其他键的别名里。"
                )
            else:
                problems.append(f"前端悬停表里找不到「{item.canonical}」或「{item.alias}」。")
            continue
        index, indent, key, names, tail = keyed[0]
        if key == item.canonical and item.alias in names and item.canonical not in names:
            notes.append(f"前端悬停表已用「{item.canonical}」作键，别名含「{item.alias}」。")
            continue
        if key == item.alias and item.canonical in names:
            names = [item.alias if name == item.canonical else name for name in names]
            names = [name for name in names if name != item.canonical]
            if item.alias not in names:
                names.insert(0, item.alias)
            inner = ", ".join(f'"{name}"' for name in names)
            newline = "\n" if lines[index].endswith("\n") else ""
            lines[index] = f'{indent}"{item.canonical}": [{inner}]{tail}{newline}'
            changed = True
            notes.append(f"已把前端悬停表的键从「{item.alias}」改为「{item.canonical}」。")
            continue
        if key == item.canonical and item.alias not in names:
            names.append(item.alias)
            inner = ", ".join(f'"{name}"' for name in names)
            newline = "\n" if lines[index].endswith("\n") else ""
            lines[index] = f'{indent}"{item.canonical}": [{inner}]{tail}{newline}'
            changed = True
            notes.append(f"已把「{item.alias}」补进「{item.canonical}」的悬停别名。")
            continue
        problems.append(f"前端悬停表里「{key}」这一行无法按对照表改正。")
    return FileResult("前端悬停表", changed, problems, notes, "".join(lines))


def fix_tuple_seed(text: str, corrections: list[Correction]) -> FileResult:
    """改正回填种子里的 (常用名, [别名])。文件里没有这一对时只记说明，不算失败。"""
    problems: list[str] = []
    notes: list[str] = []
    lines = text.splitlines(keepends=True)
    changed = False
    for item in corrections:
        found = False
        for index, line in enumerate(lines):
            matched = _TUPLE_ENTRY.search(line)
            if not matched:
                continue
            key = matched.group(1)
            names = _quoted_items(matched.group(2))
            if key not in (item.canonical, item.alias) and item.canonical not in names and item.alias not in names:
                continue
            found = True
            if key == item.canonical and item.alias in names:
                notes.append(f"回填种子已把「{item.canonical}」当作常用名。")
                break
            if key == item.alias and item.canonical in names:
                names = [item.alias if name == item.canonical else name for name in names]
                names = [name for name in names if name != item.canonical]
                if item.alias not in names:
                    names.insert(0, item.alias)
                inner = ", ".join(f'"{name}"' for name in names)
                replacement = f'("{item.canonical}", [{inner}])'
                newline = "\n" if line.endswith("\n") else ""
                lines[index] = line[: matched.start()] + replacement + line[matched.end() :].rstrip("\r\n") + newline
                changed = True
                notes.append(f"已把回填种子的常用名从「{item.alias}」改为「{item.canonical}」。")
                break
            problems.append(f"回填种子里与「{item.canonical}」有关的一行无法按对照表改正。")
            break
        if not found:
            notes.append(f"回填种子里没有「{item.canonical}」这一对，未改这一对。")
    return FileResult("回填种子", changed, problems, notes, "".join(lines))


def fix_database(conn: sqlite3.Connection, corrections: list[Correction], apply: bool) -> FileResult:
    """改正 teams.name_zh_canonical 与 team_aliases.alias。不改比赛文本列。"""
    problems: list[str] = []
    notes: list[str] = []
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "teams" not in tables or "team_aliases" not in tables:
        problems.append("数据库里没有 teams 或 team_aliases，无法按对照表检查。")
        return FileResult("数据库", False, problems, notes)
    changed = False
    for item in corrections:
        teams = conn.execute(
            "SELECT id, name_zh_canonical FROM teams WHERE name_zh_canonical IN (?, ?)",
            (item.canonical, item.alias),
        ).fetchall()
        by_name = {row[1]: row[0] for row in teams}
        if item.canonical in by_name and item.alias in by_name:
            problems.append(
                f"数据库里「{item.canonical}」和「{item.alias}」是两支球队，脚本没有合并。"
            )
            continue
        if item.canonical in by_name:
            team_id = by_name[item.canonical]
            alias_row = conn.execute(
                "SELECT id, team_id FROM team_aliases WHERE alias = ?",
                (item.alias,),
            ).fetchone()
            if alias_row and alias_row[1] != team_id:
                problems.append(f"别名「{item.alias}」已经指向另一支球队，脚本没有改写。")
                continue
            if alias_row:
                notes.append(f"数据库中「{item.canonical}」已是常用名，别名「{item.alias}」已指向该队。")
                continue
            notes.append(f"将为「{item.canonical}」补上别名「{item.alias}」。")
            changed = True
            if apply:
                conn.execute(
                    "INSERT INTO team_aliases (alias, team_id, source) VALUES (?, ?, ?)",
                    (item.alias, team_id, "direction_correction"),
                )
            continue
        if item.alias not in by_name:
            notes.append(f"数据库里没有「{item.canonical}」或「{item.alias}」这支球队，未改库。")
            continue
        team_id = by_name[item.alias]
        blocking = conn.execute(
            "SELECT id, team_id FROM team_aliases WHERE alias = ?",
            (item.alias,),
        ).fetchone()
        if blocking and blocking[1] != team_id:
            problems.append(f"别名「{item.alias}」已经被其他球队占用，脚本没有改写常用名。")
            continue
        notes.append(f"将把数据库常用名从「{item.alias}」改为「{item.canonical}」，并把「{item.alias}」写成别名。")
        changed = True
        if not apply:
            continue
        common_alias = conn.execute(
            "SELECT id, team_id FROM team_aliases WHERE alias = ?",
            (item.canonical,),
        ).fetchone()
        if common_alias and common_alias[1] != team_id:
            problems.append(f"「{item.canonical}」已经是其他球队的别名，脚本没有改写。")
            continue
        if common_alias:
            conn.execute("DELETE FROM team_aliases WHERE id = ?", (common_alias[0],))
        conn.execute(
            "UPDATE teams SET name_zh_canonical = ? WHERE id = ?",
            (item.canonical, team_id),
        )
        if not blocking:
            conn.execute(
                "INSERT INTO team_aliases (alias, team_id, source) VALUES (?, ?, ?)",
                (item.alias, team_id, "direction_correction"),
            )
    return FileResult("数据库", changed, problems, notes)


def _print_result(result: FileResult) -> None:
    print(result.label)
    for line in result.notes:
        print(f"  {line}")
    for line in result.problems:
        print(f"  未通过：{line}")


def _write_if_needed(path: Path, result: FileResult, apply: bool) -> None:
    if apply and result.changed and result.text is not None and not result.problems:
        path.write_text(result.text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按对照表检查或替换球队常用名与别名的方向。")
    parser.add_argument("--apply", action="store_true", help="写回改正后的文件；给出数据库时同时写库。")
    parser.add_argument("--db", type=Path, default=None, help="要检查的 SQLite 数据库。不改比赛文本列。")
    parser.add_argument("--corrections", type=Path, default=CORRECTIONS)
    parser.add_argument("--teams-csv", type=Path, default=TEAMS_CSV)
    parser.add_argument("--team-name-ts", type=Path, default=TEAM_NAME_TS)
    parser.add_argument("--tuple-seed", type=Path, default=TUPLE_SEED)
    args = parser.parse_args(argv)

    corrections = load_corrections(args.corrections)
    results: list[FileResult] = []
    csv_result = fix_teams_csv(args.teams_csv.read_text(encoding="utf-8"), corrections)
    ts_result = fix_team_name_ts(args.team_name_ts.read_text(encoding="utf-8"), corrections)
    seed_result = fix_tuple_seed(args.tuple_seed.read_text(encoding="utf-8"), corrections)
    results.extend([csv_result, ts_result, seed_result])
    _write_if_needed(args.teams_csv, csv_result, args.apply)
    _write_if_needed(args.team_name_ts, ts_result, args.apply)
    _write_if_needed(args.tuple_seed, seed_result, args.apply)

    if args.db is not None:
        if not args.db.is_file():
            print(f"未通过：找不到数据库 {args.db}")
            return 1
        conn = sqlite3.connect(args.db)
        try:
            db_result = fix_database(conn, corrections, args.apply)
            if args.apply and not db_result.problems:
                conn.commit()
            else:
                conn.rollback()
        finally:
            conn.close()
        results.append(db_result)

    if not args.apply:
        for result in results:
            if result.changed:
                result.problems.append("方向仍与对照表不一致。本次只检查，没有写回。加上 --apply 才会替换。")
    for result in results:
        _print_result(result)
    if any(result.problems for result in results):
        print("对照表检查未通过。")
        return 1
    if args.apply and any(result.changed for result in results):
        print("已按对照表完成检查。需要替换的方向已经写回。比赛文本列没有修改。")
    elif args.apply:
        print("已按对照表完成检查。没有需要替换的方向。比赛文本列没有修改。")
    else:
        print("对照表检查通过。本次没有写文件。要替换时加上 --apply。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
