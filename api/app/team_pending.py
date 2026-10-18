"""球队与赛事「待确认」标记。

规则全文见 docs/schema/v2_0-name-canonical-and-star-rules.md（用户 2026-10-18）。
队名和赛事名共用本索引。

已经升格的常用名不标星。升格来源只有三种：雷速体育写法、人工明确定稿、旧分析仓库里的高置信名称。
提议表没有升格或待确认标记时，只把尚不在常用名里的名称记为待确认，不得把已经升格的常用名重新写入待确认。
只是归并到临时名、名称表标了待确认、别名来源以 _pending_confirm 结尾、或待定池仍开放的，继续标星。
男子与女子、一线队与青年队等相近名称不能借用另一个实体的常用名把星号消掉。
身份编号不是升格。外部研究表缺失时只记警告，不得把全部名称标成待确认。

球队包内表是已升格子集，不是全量名册。外部定稿成功读入后，它才是球队全量名册，未收录的队名标为未映射。
赛事包内表是当前已升格的赛事名册，未收录的赛事名标为尚未升格。
正式库只读 teams / team_aliases、leagues / league_aliases，以及已经建好的 alias_pool_entry。表不存在时跳过。不写库。
"""
from __future__ import annotations

import csv
import logging
import os
import re
import sqlite3
import unicodedata
from pathlib import Path

log = logging.getLogger(__name__)

_API_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE = _API_ROOT / "resources" / "name-standard"
_RESEARCH = Path("/workspace/odds-data/research")

PACKAGED_TEAMS = _PACKAGE / "teams.csv"
PACKAGED_COMPETITIONS = _PACKAGE / "competitions.csv"

# 外部研究目录只作可选补充。文件不在时不得把全部名称标成待确认。
EXTERNAL_TEAM_PATHS = {
    "final": _RESEARCH / "name-standard-2026-10-14" / "teams.csv",
    "proposed": _RESEARCH / "dc_train" / "team_aliases_proposed.csv",
    "seed": _RESEARCH / "name-standard-2026-10-14" / "import" / "import_rows.csv",
}
EXTERNAL_LEAGUE_FINAL = _RESEARCH / "name-standard-2026-10-14" / "competitions.csv"

# 与既有调用、环境变量名保持一致。DEFAULT_PATHS 仍指向外部研究目录，供显式覆盖；
# 缺省加载会先读包内表，再尝试这些路径。
DEFAULT_PATHS = dict(EXTERNAL_TEAM_PATHS)
ENV_PATHS = {
    "final": "APP_TEAM_FINAL_FILE",
    "proposed": "APP_TEAM_PROPOSED_FILE",
    "seed": "APP_TEAM_SEED_FILE",
}
ENV_LEAGUE_FINAL = "APP_LEAGUE_FINAL_FILE"

_TEAM_SEED_TABLES = frozenset(("teams", "team_aliases"))
_LEAGUE_SEED_TABLES = frozenset(("leagues", "league_aliases"))

# 升格后的常用名。雷速体育优先，其次是人工定稿和旧分析仓库的高置信名称。
_ESTABLISHED_PROMOTION = frozenset((
    "leisutiyu",
    "leisu",
    "雷速",
    "雷速体育",
    "human_finalize",
    "legacy_high_confidence",
    "established",
))
# 已归并但还没升格的临时名。前端必须继续标星。
_TEMPORARY_PROMOTION = frozenset(("temporary", "provisional", "临时"))

_team_cache: dict[tuple, "NameIndex"] = {}
_league_cache: dict[tuple, "NameIndex"] = {}
_overlay_cache: dict[str, object] = {}


def norm(name) -> str:
    """去首尾空格、全角转半角（NFKC）、连续空白合并为一个空格、拉丁字母不分大小写。"""
    if name is None:
        return ""
    s = unicodedata.normalize("NFKC", str(name)).strip()
    return re.sub(r"\s+", " ", s).casefold()


def _true(v) -> bool:
    return norm(v) in ("true", "1", "yes")


class NameIndex:
    def __init__(self, known: set[str], pending: set[str], missing: list[str],
                 authoritative: bool = False, pool: set[str] | None = None):
        self.known = set(known)
        self.pending = set(pending)
        self.missing = list(missing)
        # True 表示全量名册已经读入：名册里没有的名称才是未映射。
        self.authoritative = bool(authoritative)
        self.pool = set(pool or ())
        self.league: NameIndex | None = None

    def reason(self, *names, mapped: bool = False) -> str | None:
        """返回 None 表示不标星。

        原因：final_pending_confirm（临时名、待确认标记或未升格来源）、
        ambiguous_not_promoted（相近名称可能是另一个实体，不能并进已有常用名）、
        alias_pool_open（待定池仍开放）、not_found（全量名册里没有）。
        表文件缺失不再返回 table_missing，也不再因此把全部名称标成待确认。
        mapped 保留给旧调用，身份编号不是常用名升格，不参与判断。
        """
        _ = mapped
        ns = {norm(n) for n in names if norm(n)}
        if ns & self.pending:
            return "final_pending_confirm"
        # 先看尚未单独升格的歧义名称，避免「切尔西女」因为同时传来「切尔西」而消掉星号。
        for item in ns:
            if item not in self.known and _ambiguous(item):
                return "ambiguous_not_promoted"
        if ns & self.known:
            return None
        if not ns:
            return None
        if ns & self.pool:
            return "alias_pool_open"
        if self.authoritative:
            return "not_found"
        return None

    def is_pending(self, *names, mapped: bool = False) -> bool:
        return self.reason(*names, mapped=mapped) is not None


def _rows(p: Path):
    with open(p, encoding="utf-8-sig", newline="") as f:
        yield from csv.DictReader(f)


def _is_temporary_row(row: dict) -> bool:
    """这一行是临时名或待确认时返回 True。没有 promotion 列且未标待确认时，视为已经升格。"""
    if _true(row.get("pending_confirm")):
        return True
    promotion = norm(row.get("promotion"))
    if not promotion:
        return False
    if promotion in _ESTABLISHED_PROMOTION:
        return False
    if promotion in _TEMPORARY_PROMOTION:
        return True
    # 无法识别的升格标记不能当成常用名。
    return True


def _ambiguous(name: str) -> bool:
    """男子/女子、一线队/青年队、已点名的同名实体。延迟导入，避免和待定池模块循环依赖。"""
    from app.alias_pool import detect_ambiguity

    return detect_ambiguity(name) is not None


def _apply(known: set[str], pending: set[str], names, flag: bool) -> None:
    ns = {norm(n) for n in names if norm(n)}
    if flag:
        pending.update(ns)
    else:
        # 已升格名称要从 pending 里拿掉，否则提议表临时名会盖掉包内/正式库常用名。
        known.update(ns)
        pending.difference_update(ns)


def _consume(path: Path, role: str, known: set[str], pending: set[str],
             seed_tables: frozenset[str] | None, *, warn: bool) -> bool:
    """读入一张名称表。成功返回 True。失败时记警告并返回 False，不抛异常。"""
    try:
        rows = list(_rows(path))
    except (OSError, csv.Error, UnicodeDecodeError) as e:
        if warn:
            log.warning("名称表读取失败，已跳过，不把全部名称标成待确认：%s（%s）", path, e)
        return False
    for r in rows:
        if role == "final":
            names = [r.get("canonical")] + (r.get("aliases") or "").split("|")
            flag = _is_temporary_row(r)
        elif role == "proposed":
            # 提议表只是归并建议。没有写明已经升格时，仅对「尚不在 known」的名称记临时名。
            # 已在定稿/包内升格的常用名不得被提议表重新打成待确认（用户规则：已有常用名不加星）。
            names = [r.get("alias"), r.get("team_canonical")]
            marked = str(r.get("promotion") or "").strip() or str(r.get("pending_confirm") or "").strip()
            if marked:
                flag = _is_temporary_row(r)
            else:
                ns = {norm(n) for n in names if norm(n)}
                pending.update(n for n in ns if n not in known)
                continue
        else:
            if seed_tables is not None and r.get("table") not in seed_tables:
                continue
            names = [r.get("name_zh_canonical"), r.get("alias")]
            flag = _is_temporary_row(r)
        _apply(known, pending, names, flag)
    return True


def _stat_key(label: str, path: Path | None) -> tuple:
    if path is None:
        return (label, "", None)
    try:
        return (label, str(path), path.stat().st_mtime_ns)
    except OSError:
        return (label, str(path), None)


def _env_path(env_name: str, default: Path) -> tuple[Path, bool]:
    raw = os.environ.get(env_name, "").strip()
    if raw:
        return Path(raw), True
    return default, False


def _finish(known: set[str], pending: set[str], missing: list[str], authoritative: bool) -> NameIndex:
    # 明确标了待确认的名称，即使另一张表把它写成了常用名，仍然待确认。
    return NameIndex(known - pending, pending, missing, authoritative=authoritative)


def _load_explicit(paths: dict, seed_tables: frozenset[str]) -> NameIndex:
    known: set[str] = set()
    pending: set[str] = set()
    missing: list[str] = []
    final_ok = False
    for role in ("final", "proposed", "seed"):
        raw = paths.get(role)
        if not raw:
            continue
        ok = _consume(Path(raw), role, known, pending, seed_tables if role == "seed" else None, warn=True)
        if role == "final" and ok:
            final_ok = True
        if not ok:
            missing.append(role)
    return _finish(known, pending, missing, final_ok)


def _load_default_teams() -> NameIndex:
    known: set[str] = set()
    pending: set[str] = set()
    missing: list[str] = []
    if not _consume(PACKAGED_TEAMS, "final", known, pending, None, warn=True):
        missing.append("packaged")
    final_path, final_set = _env_path(ENV_PATHS["final"], EXTERNAL_TEAM_PATHS["final"])
    # 包内表不是全量名册。只有外部定稿真正读入，未收录的队名才标成未映射。
    # 默认研究路径本来就不在部署机上时，不记「读取失败」，只说明未收录的队名不标星。
    final_present = final_set or final_path.exists()
    if _consume(final_path, "final", known, pending, None, warn=final_present):
        authoritative = True
    else:
        authoritative = False
        missing.append("final")
        if not final_present:
            log.warning("外部球队定稿不存在（%s），已使用包内名称表；未收录的队名不标成待确认", final_path)
    for role, tables in (("proposed", None), ("seed", _TEAM_SEED_TABLES)):
        path, env_set = _env_path(ENV_PATHS[role], EXTERNAL_TEAM_PATHS[role])
        warn = env_set or path.exists()
        if not _consume(path, role, known, pending, tables, warn=warn):
            if warn:
                missing.append(role)
    return _finish(known, pending, missing, authoritative)


def _load_default_leagues() -> NameIndex:
    known: set[str] = set()
    pending: set[str] = set()
    missing: list[str] = []
    authoritative = _consume(PACKAGED_COMPETITIONS, "final", known, pending, None, warn=True)
    if not authoritative:
        missing.append("packaged")
        log.warning("包内赛事名称表缺失（%s），未收录的赛事名不标成待确认", PACKAGED_COMPETITIONS)
    final_path, final_set = _env_path(ENV_LEAGUE_FINAL, EXTERNAL_LEAGUE_FINAL)
    if final_set or final_path.exists():
        if _consume(final_path, "final", known, pending, None, warn=True):
            authoritative = True
        else:
            missing.append("final")
    return _finish(known, pending, missing, authoritative)


def _team_cache_key(paths: dict | None) -> tuple:
    if paths is not None:
        return tuple(_stat_key(role, Path(paths[role]) if paths.get(role) else None) for role in ("final", "proposed", "seed"))
    parts = [_stat_key("packaged", PACKAGED_TEAMS)]
    for role in ("final", "proposed", "seed"):
        path, _env_set = _env_path(ENV_PATHS[role], EXTERNAL_TEAM_PATHS[role])
        parts.append(_stat_key(role, path))
    return tuple(parts)


def _league_cache_key(paths: dict | None) -> tuple:
    if paths is not None:
        return ("explicit",) + tuple(
            _stat_key(role, Path(paths[role]) if paths.get(role) else None) for role in ("final", "proposed", "seed")
        )
    path, _env_set = _env_path(ENV_LEAGUE_FINAL, EXTERNAL_LEAGUE_FINAL)
    return ("default", _stat_key("packaged", PACKAGED_COMPETITIONS), _stat_key("final", path))


def load_index(paths: dict | None = None) -> NameIndex:
    """加载球队名称索引。传入 paths 时只读调用方给出的表，成功读入定稿表后视为全量名册。"""
    key = _team_cache_key(paths)
    cached = _team_cache.get(key)
    if cached is not None:
        return cached
    idx = _load_explicit(paths, _TEAM_SEED_TABLES) if paths is not None else _load_default_teams()
    _team_cache.clear()
    _team_cache[key] = idx
    _overlay_cache.clear()
    return idx


def load_league_index(paths: dict | None = None) -> NameIndex:
    """加载赛事名称索引。缺省使用包内定稿；包内表读入成功后，未收录的赛事名标为未映射。"""
    key = _league_cache_key(paths)
    cached = _league_cache.get(key)
    if cached is not None:
        return cached
    if paths is not None:
        idx = _load_explicit(paths, _LEAGUE_SEED_TABLES)
    else:
        idx = _load_default_leagues()
    _league_cache.clear()
    _league_cache[key] = idx
    _overlay_cache.clear()
    return idx


def _clone(idx: NameIndex) -> NameIndex:
    return NameIndex(set(idx.known), set(idx.pending), list(idx.missing),
                     authoritative=idx.authoritative, pool=set(idx.pool))


def _table_names(conn: sqlite3.Connection) -> set[str]:
    try:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.Error as e:
        log.warning("读取正式库表名失败，名称待确认不改按全员处理：%s", e)
        return set()


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error as e:
        log.warning("读取表结构失败，已跳过 %s：%s", table, e)
        return set()


def _overlay_identity(conn: sqlite3.Connection, idx: NameIndex, kind: str) -> None:
    """把正式库里的规范名和别名并进索引。表不存在或读失败时保持原索引，不标成全员待确认。"""
    tables = _table_names(conn)
    if kind == "team":
        standard, aliases, pool_kind = "teams", "team_aliases", "team"
    else:
        standard, aliases, pool_kind = "leagues", "league_aliases", "competition"
    if standard in tables:
        cols = _columns(conn, standard)
        if "name_zh_canonical" in cols:
            pending_col = "pending_confirm" in cols
            sql = (
                f"SELECT name_zh_canonical, pending_confirm FROM {standard}"
                if pending_col
                else f"SELECT name_zh_canonical, NULL AS pending_confirm FROM {standard}"
            )
            try:
                for name, flag in conn.execute(sql):
                    _apply(idx.known, idx.pending, [name], _true(flag))
            except sqlite3.Error as e:
                log.warning("读取%s失败，不把全部名称标成待确认：%s", standard, e)
    if aliases in tables:
        cols = _columns(conn, aliases)
        if "alias" in cols:
            has_source = "source" in cols
            sql = (
                f"SELECT alias, source FROM {aliases}"
                if has_source
                else f"SELECT alias, NULL AS source FROM {aliases}"
            )
            try:
                for alias, source in conn.execute(sql):
                    flagged = str(source or "").endswith("_pending_confirm")
                    _apply(idx.known, idx.pending, [alias], flagged)
            except sqlite3.Error as e:
                log.warning("读取%s失败，不把全部名称标成待确认：%s", aliases, e)
    if "alias_pool_entry" in tables:
        try:
            rows = conn.execute(
                "SELECT norm_key, raw_name FROM alias_pool_entry "
                "WHERE kind = ? AND status IN ('pending', 'ambiguous')",
                (pool_kind,),
            )
            for norm_key, raw_name in rows:
                idx.pool.update(n for n in (norm(norm_key), norm(raw_name)) if n)
        except sqlite3.Error as e:
            log.warning("读取待定池失败，不把全部名称标成待确认：%s", e)
    idx.known -= idx.pending


def _changes(conn: sqlite3.Connection | None):
    if conn is None:
        return None
    try:
        return int(conn.total_changes)
    except sqlite3.Error:
        return None


def load_pending(conn: sqlite3.Connection | None = None, paths: dict | None = None) -> NameIndex:
    """球队索引，并在 league 属性上附带赛事索引。conn 用来并入正式库别名和待定池，不再按编号空否判断。"""
    teams = load_index(paths)
    leagues = load_league_index()
    token = (id(teams), id(leagues), id(conn) if conn is not None else None, _changes(conn))
    if _overlay_cache.get("token") == token:
        cached = _overlay_cache.get("value")
        if isinstance(cached, NameIndex):
            return cached
    out = _clone(teams)
    league = _clone(leagues)
    if conn is not None:
        _overlay_identity(conn, out, "team")
        _overlay_identity(conn, league, "competition")
    out.league = league
    _overlay_cache["token"] = token
    _overlay_cache["value"] = out
    return out


def is_pending(team_id, canonical, pending: NameIndex | None, display=None) -> bool:
    """team_id 保留参数位置但不参与判断。名称导入情况由索引决定，编号为空不再导致标星。"""
    idx = pending if isinstance(pending, NameIndex) else load_index()
    return idx.is_pending(canonical, display)


def reason(team_id, canonical, pending: NameIndex | None, display=None) -> str | None:
    idx = pending if isinstance(pending, NameIndex) else load_index()
    return idx.reason(canonical, display)
