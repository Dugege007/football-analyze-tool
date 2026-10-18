#!/usr/bin/env python3
"""回填 matches 上仍为空的 home_team_id / away_team_id / league_id。

只查不建（create=False 写死）。已有编号不覆盖。解析不到的名称在 --apply --write-pool
时交给 alias_pool.pool_observe（kind 为 team 或 competition）。不新建球队或赛事。

默认 dry-run，不写比赛行，也不写待定池。不要对正式库直接 --apply；本脚本不会默认打开正式库。

用法:
  python api/scripts/backfill_match_entity_ids.py --db PATH
  python api/scripts/backfill_match_entity_ids.py --db PATH --date-from 2026-10-01 --date-to 2026-10-18 --report /tmp/entity-id-backfill.md
  python api/scripts/backfill_match_entity_ids.py --db PATH --apply --only-null-slots --write-pool --report /tmp/entity-id-backfill.md
"""
from __future__ import annotations
# --- public repo: paths are env-overridable (see config.example.env) ---
import os as _rp_os
from pathlib import Path as _RpPath
_REPO_ROOT = _RpPath(__file__).resolve().parents[2]
def _rp_load_dotenv(path=_REPO_ROOT / '.env'):
    # 读仓库根 .env（KEY=VALUE；已存在的环境变量优先，不覆盖）；不打印任何值
    if _rp_os.environ.get('FAT_DISABLE_DOTENV') == '1':  # 测试时由 conftest 设置，避免本机 .env 干扰
        return
    try:
        for _ln in path.read_text(encoding='utf-8').splitlines():
            _ln = _ln.strip()
            if not _ln or _ln.startswith('#') or '=' not in _ln:
                continue
            _k, _v = _ln.split('=', 1)
            _k = _k.strip().removeprefix('export ').strip()
            _v = _v.strip().strip('"').strip("'")
            if _v.startswith('YOUR_'):  # config.example.env 占位值视为未填写
                continue
            if _k and _k not in _rp_os.environ:
                _rp_os.environ[_k] = _v
    except FileNotFoundError:
        pass
_rp_load_dotenv()
def _rp_env(name, default, base=None):
    v = _rp_os.environ.get(name, '').strip()
    p = _RpPath(v).expanduser() if v else default
    return p if p.is_absolute() else (base or _REPO_ROOT) / p
_MA_API_ROOT = _rp_env('MA_API_ROOT', _REPO_ROOT / 'api')
_ODDS_DATA_DIR = _rp_env('ODDS_DATA_DIR', _REPO_ROOT / 'data' / 'odds-data')
_APP_DB = _rp_env('APP_DB_PATH', _MA_API_ROOT / 'data' / 'app.db', _MA_API_ROOT)
_V2D3_DB = _rp_env('V2D3_DB_PATH', _MA_API_ROOT / 'data' / 'v2d3' / 'app.db', _MA_API_ROOT)
_BACKUP_DIR = _rp_env('BACKUP_DIR', _REPO_ROOT / 'backups' / 'football')
# --- end path config ---


import argparse
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from import_lib import lookup_entity_id  # noqa: E402

# 写死：只查不建。没有关闭开关，避免误走会新建实体的种子路径。
CREATE_ENTITIES = False
ONLY_NULL_SLOTS = True
POOL_SOURCE = "entity_id_backfill"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLOTS = (
    ("home_team", "home_team_id", "team", "home_team"),
    ("away_team", "away_team_id", "team", "away_team"),
    ("competition_name", "league_id", "competition", "competition_name"),
)


def _require_columns(conn: sqlite3.Connection) -> bool:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(matches)")}
    required = {
        "id",
        "jingcai_date",
        "home_team",
        "away_team",
        "competition_name",
        "home_team_id",
        "away_team_id",
        "league_id",
    }
    missing = sorted(required - cols)
    if missing:
        raise SystemExit(f"REFUSE: matches 缺少列 {', '.join(missing)}，无法回填身份编号。")
    return "updated_at" in cols


def _load_rows(conn: sqlite3.Connection, date_from: str | None, date_to: str | None) -> list[sqlite3.Row]:
    sql = """
        SELECT id, jingcai_date, home_team, away_team, competition_name,
               home_team_id, away_team_id, league_id
        FROM matches
        WHERE 1 = 1
    """
    params: list[str] = []
    if date_from:
        sql += " AND jingcai_date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND jingcai_date <= ?"
        params.append(date_to)
    sql += " ORDER BY jingcai_date, id"
    return list(conn.execute(sql, params))


def _cell(row: sqlite3.Row, key: str):
    return row[key]


def render_report(stats: dict) -> str:
    """把一次回填的计数写成短报告。"""
    filled = stats["written"] if stats["mode"] == "apply" else stats["planned"]
    lines = [
        "# 比赛身份编号回填",
        "",
        f"- 模式：{stats['mode']}",
        "- create：False（只查不建）",
        "- 只填空槽：是",
        f"- 日期：{stats['date_from'] or '（不限）'} .. {stats['date_to'] or '（不限）'}",
        f"- 扫描场次：{stats['scanned']}",
        (
            f"- 计划填充：主队 {stats['planned']['home_team_id']}，"
            f"客队 {stats['planned']['away_team_id']}，赛事 {stats['planned']['league_id']}"
        ),
        (
            f"- 实际写入：主队 {filled['home_team_id']}，"
            f"客队 {filled['away_team_id']}，赛事 {filled['league_id']}"
            if stats["mode"] == "apply"
            else "- 实际写入：无（dry-run）"
        ),
        f"- 已有编号未改：{stats['skipped_already_set']}",
        f"- 未解析：{len(stats['unresolved'])}",
        f"- 待定池写入：{stats['pool_written']}",
        "",
        "## 未解析名称",
        "",
    ]
    unresolved = stats["unresolved"][:50]
    if not unresolved:
        lines.append("- （无）")
    for item in unresolved:
        lines.append(
            f"- {item['kind']} `{item['name']}` matches.id={item['match_id']}#{item['source_slot']}"
        )
    if len(stats["unresolved"]) > len(unresolved):
        lines.append(f"- ……另有 {len(stats['unresolved']) - len(unresolved)} 条")
    if stats["mode"] != "apply":
        lines += ["", "dry-run 未写比赛行，也未写待定池。确认报告后再加 --apply。", ""]
    elif not stats["write_pool"]:
        lines += ["", "本次没有 --write-pool，未解析名称没有进入待定池。", ""]
    return "\n".join(lines)


def run_backfill(
    conn: sqlite3.Connection,
    *,
    apply: bool,
    date_from: str | None = None,
    date_to: str | None = None,
    write_pool: bool = False,
    report_path: Path | None = None,
) -> dict:
    """扫描空槽并按别名回填。apply 为假时不写库。"""
    if CREATE_ENTITIES or not ONLY_NULL_SLOTS:
        raise RuntimeError("回填只允许 create=False，并且只能填空槽。")
    has_updated = _require_columns(conn)
    if conn.row_factory is not sqlite3.Row:
        conn.row_factory = sqlite3.Row
    rows = _load_rows(conn, date_from, date_to)
    planned = {"home_team_id": 0, "away_team_id": 0, "league_id": 0}
    written = {"home_team_id": 0, "away_team_id": 0, "league_id": 0}
    skipped_already_set = 0
    unresolved: list[dict] = []
    fills: list[tuple[int, str, int]] = []
    pool_jobs: list[dict] = []

    for row in rows:
        match_id = int(_cell(row, "id"))
        for name_col, id_col, kind, source_slot in _SLOTS:
            if _cell(row, id_col) is not None:
                skipped_already_set += 1
                continue
            name = _cell(row, name_col)
            found = lookup_entity_id(conn, kind, name)
            if found is not None:
                planned[id_col] += 1
                fills.append((match_id, id_col, found))
                continue
            if name is None or not str(name).strip():
                continue
            item = {
                "match_id": match_id,
                "kind": kind,
                "name": str(name),
                "id_col": id_col,
                "source_slot": source_slot,
                "jingcai_date": _cell(row, "jingcai_date"),
                "competition_name": _cell(row, "competition_name"),
            }
            unresolved.append(item)
            pool_jobs.append(item)

    pool_written = 0
    if apply:
        # 建表语句会提交当前事务，所以放在回填写入之前。
        if write_pool and pool_jobs:
            from app import alias_pool

            alias_pool.ensure_schema(conn)
        if not conn.in_transaction:
            conn.execute("BEGIN")
        for match_id, id_col, found in fills:
            sql = f"UPDATE matches SET {id_col} = ?"
            if has_updated:
                sql += ", updated_at = datetime('now')"
            sql += f" WHERE id = ? AND {id_col} IS NULL"
            cur = conn.execute(sql, (found, match_id))
            if cur.rowcount == 1:
                written[id_col] += 1
        if write_pool and pool_jobs:
            for item in pool_jobs:
                context = {}
                if item["jingcai_date"]:
                    context["jingcai_date"] = item["jingcai_date"]
                if item["competition_name"]:
                    context["competition_name"] = item["competition_name"]
                observed = alias_pool.pool_observe(
                    conn,
                    item["kind"],
                    item["name"],
                    POOL_SOURCE,
                    f"matches.id={item['match_id']}#{item['source_slot']}",
                    context or None,
                )
                if observed.get("action") == "skipped":
                    continue
                pool_written += 1
                refreshed = conn.execute(
                    f"SELECT {item['id_col']} FROM matches WHERE id = ?",
                    (item["match_id"],),
                ).fetchone()
                if refreshed is not None and refreshed[0] is not None:
                    written[item["id_col"]] += 1
                    planned[item["id_col"]] += 1
                    unresolved = [
                        pending for pending in unresolved
                        if not (
                            pending["match_id"] == item["match_id"]
                            and pending["source_slot"] == item["source_slot"]
                        )
                    ]
        conn.commit()

    stats = {
        "mode": "apply" if apply else "dry-run",
        "create": False,
        "only_null_slots": True,
        "write_pool": bool(write_pool and apply),
        "date_from": date_from,
        "date_to": date_to,
        "scanned": len(rows),
        "planned": planned,
        "written": written,
        "skipped_already_set": skipped_already_set,
        "unresolved": unresolved,
        "pool_written": pool_written,
    }
    text = render_report(stats)
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(text + "\n", encoding="utf-8")
    stats["report_text"] = text
    return stats


def _check_date(value: str | None, flag: str) -> str | None:
    if value is None:
        return None
    if not _DATE.fullmatch(value):
        raise SystemExit(f"{flag} 需要 YYYY-MM-DD，收到 {value!r}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", required=True, type=Path, help="SQLite 路径。必须显式给出，脚本不会默认打开正式库。")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="只读预览，不写比赛行、不写待定池（默认）")
    mode.add_argument("--apply", action="store_true", help="把解析到的编号写入仍为空的槽位")
    parser.add_argument("--date-from", default=None, help="只处理 jingcai_date 大于等于该日的场次")
    parser.add_argument("--date-to", default=None, help="只处理 jingcai_date 小于等于该日的场次")
    parser.add_argument(
        "--write-pool",
        action="store_true",
        help="与 --apply 合用：解析不到的名称写入待定池（alias_pool.pool_observe）",
    )
    parser.add_argument(
        "--only-null-slots",
        action="store_true",
        help="只填空槽，不覆盖已有编号（唯一模式，写死为开启）",
    )
    parser.add_argument("--report", type=Path, default=None, help="把本次预览或回填写成 Markdown")
    args = parser.parse_args(argv)
    date_from = _check_date(args.date_from, "--date-from")
    date_to = _check_date(args.date_to, "--date-to")
    if date_from and date_to and date_from > date_to:
        raise SystemExit("--date-from 不能晚于 --date-to")
    path = args.db.expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"找不到数据库：{path}")
    apply = bool(args.apply)
    if apply:
        from app.formal_db import guard_write_target

        guard_write_target(path, writing=True)
        conn = sqlite3.connect(str(path))
    else:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        stats = run_backfill(
            conn,
            apply=apply,
            date_from=date_from,
            date_to=date_to,
            write_pool=bool(args.write_pool),
            report_path=args.report,
        )
    except Exception:
        if apply:
            conn.rollback()
        raise
    finally:
        conn.close()
    print(stats["report_text"])
    if args.report is not None:
        print(f"report → {args.report}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
