#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本库完赛场次对照到 BSD event_id，写入 bsd_event_map。

分析师拍板（2026-10-16，细则以 bsd-event-map-rules 为准）：

- 主源：BSD ``GET /api/v2/events/?date_from=&date_to=``。先按开赛日北京时间当天拉，
  当天没有候选时再扩到前后各 1 日；分页次数写入报告。
- 自动写入须同时满足：开赛差绝对值 ≤ 15 分钟；主客经 teams / team_aliases
  （及已有定稿常用名）归一且方向一致；候选恰好 1 场。完赛两侧都有比分时再要求比分一致。
- 0 候选、多候选、比分冲突、队名未解析：不写表，记入 unmatched.csv。
  原因只使用 ``no_candidate`` / ``multi_candidate`` / ``score_mismatch`` / ``team_unresolved``，
  另允许 ``event_id_conflict``（同一 event_id 被多场同时唯一命中，两场都不写）。
- 禁止编辑距离、去后缀、只对上一队等模糊猜测。已映射行不覆盖。
- ``note`` 自动写 ``kickoff15_alias``；``--manual-csv`` 显式补录写 ``manual``。
- 默认干跑，不写库。``--apply`` 才写 ``bsd_event_map``（及别名导入）。
- 写正式库沿用 ``FORMAL_DB_BACKUP_DONE=1`` 闸门，与其它一次性脚本一致。
- 密钥只读环境变量 ``BSD_BZZOIRO_API_KEY``，请求头 ``Authorization: Token <key>``。
  不打印、不落盘。

推荐顺序：先 ``--import-aliases-from-dc-train``（或子命令 ``import-aliases``）导入
dc_train 研究表里 high/med 中英别名，再干跑对照。低置信不自动入库。报告给出别名覆盖
与可映射数的前后对比。对照使用「导入之后」的别名层；干跑只在内存里叠加，不写库。

用法::

  python scripts/backfill/bsd_event_map_build.py --help
  python scripts/backfill/bsd_event_map_build.py --db api/data/app.db --events-json events.json
  python scripts/backfill/bsd_event_map_build.py --import-aliases-from-dc-train --aliases-csv research.csv
  python scripts/backfill/bsd_event_map_build.py import-aliases --aliases-csv research.csv
  python scripts/backfill/bsd_event_map_build.py --import-aliases-from-dc-train --apply   # 须先备份正式库
"""
from __future__ import annotations

import os as _rp_os
from pathlib import Path as _RpPath

_REPO_ROOT = _RpPath(__file__).resolve().parents[2]


def _rp_load_dotenv(path=_REPO_ROOT / ".env"):
    """读仓库根 .env。已存在的环境变量优先。测试可设 FAT_DISABLE_DOTENV=1。"""
    if _rp_os.environ.get("FAT_DISABLE_DOTENV") == "1":
        return
    try:
        for _ln in path.read_text(encoding="utf-8").splitlines():
            _ln = _ln.strip()
            if not _ln or _ln.startswith("#") or "=" not in _ln:
                continue
            _k, _v = _ln.split("=", 1)
            _k = _k.strip().removeprefix("export ").strip()
            _v = _v.strip().strip('"').strip("'")
            if _v.startswith("YOUR_"):
                continue
            if _k and _k not in _rp_os.environ:
                _rp_os.environ[_k] = _v
    except FileNotFoundError:
        pass


_rp_load_dotenv()


def _rp_env(name, default, base=None):
    v = _rp_os.environ.get(name, "").strip()
    p = _RpPath(v).expanduser() if v else default
    return p if p.is_absolute() else (base or _REPO_ROOT) / p


_MA_API_ROOT = _rp_env("MA_API_ROOT", _REPO_ROOT / "api")
_ODDS_DATA_DIR = _rp_env("ODDS_DATA_DIR", _REPO_ROOT / "data" / "odds-data")
_APP_DB = _rp_env("APP_DB_PATH", _MA_API_ROOT / "data" / "app.db", _MA_API_ROOT)

import argparse
import csv
import json
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# ----------------------------------------------------------------------------- 常量

BJ = timezone(timedelta(hours=8))
UTC = timezone.utc
BSD_BASE = "https://sports.bzzoiro.com/api/v2"
PAGE_LIMIT = 200
KICKOFF_TOLERANCE = timedelta(minutes=15)
NOTE_AUTO = "kickoff15_alias"
NOTE_MANUAL = "manual"
ALIAS_SOURCE_PREFIX = "dc_train_zh_en"
# 规则文规定的旁路目录。可用 --out-dir 或 BSD_EVENT_MAP_OUT_DIR 覆盖。
DEFAULT_OUT_DIR = _RpPath("/workspace/odds-data/bsd-bzzoiro/prematch-injury-weather")
MAP_TABLE = "bsd_event_map"
MAP_COLUMNS = ("match_id", "event_id", "mapped_at", "note")
# 报告里至少要分开的原因。event_id_conflict 是同一 event 被多场唯一命中，仍不写。
CORE_REASONS = ("no_candidate", "multi_candidate", "score_mismatch", "team_unresolved")
EXTRA_REASONS = ("event_id_conflict", "kickoff_missing", "manual_match_missing", "manual_conflict")
KEEP_CONFIDENCE = {"high", "med"}
BSD_ID_KEYS = ("bsd_team_id", "bsd_id", "provider_team_id")
NAME_KEYS = (
    "alias",
    "team_canonical",
    "name_zh_canonical",
    "model_source_name",
    "name_en",
    "english",
    "bsd_team_name",
)
# 整段名字只有这些俱乐部后缀时，不当作队名写入。
SUFFIX_ONLY = {"fc", "sc", "cf", "afc", "ac", "club"}
_CJK = re.compile(r"[\u4e00-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")
_BSD_ID_IN_SOURCE = re.compile(r"bsd_team_id=(\d+)")


# ----------------------------------------------------------------------------- 规范化（不做编辑距离）

def norm_display(name: str | None) -> str | None:
    """NFKC、去首尾空白、折叠连续空白。保留大小写，供入库展示。"""
    if name is None:
        return None
    n = unicodedata.normalize("NFKC", str(name)).strip()
    n = re.sub(r"\s+", " ", n)
    return n or None


def norm_key(name: str | None) -> str:
    """对照键：展示名再 casefold。相同键才算同一叫法，不去后缀、不改译名。"""
    n = norm_display(name)
    return n.casefold() if n else ""


def has_cjk(text: str | None) -> bool:
    return bool(text and _CJK.search(text))


def has_latin(text: str | None) -> bool:
    return bool(text and _LATIN.search(text))


def is_latin_name(text: str | None) -> bool:
    """纯拉丁队名（可含数字、空格、连字符）。中英混写的一段不拆开猜。"""
    n = norm_display(text)
    if not n or not has_latin(n) or has_cjk(n):
        return False
    folded = re.sub(r"[^a-z]", "", n.casefold())
    return bool(folded) and folded not in SUFFIX_ONLY


def is_cjk_name(text: str | None) -> bool:
    n = norm_display(text)
    return bool(n and has_cjk(n) and not has_latin(n))


def parse_dt(value) -> datetime | None:
    """解析开赛时刻。无时区的字符串按北京时间理解，再统一可与 BSD 的 UTC 比较。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BJ)
    return dt.astimezone(BJ)


def as_goals(value) -> int | None:
    """比分转整数。空、非整数视为缺失，不用 0 顶上。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if value.is_integer() and value >= 0:
            return int(value)
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d+", text):
        return int(text)
    return None


def pos_id(value) -> int | None:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return None
    return num if num > 0 else None


def now_bj_iso() -> str:
    return datetime.now(BJ).isoformat(timespec="seconds")


# ----------------------------------------------------------------------------- 队名索引

class TeamIndex:
    """别名 → team_id。同一规范化键指向不同队时标成歧义，解析返回 None。"""

    def __init__(self):
        self.by_key: dict[str, int] = {}
        self.ambiguous: set[str] = set()
        self.bsd_ids: dict[int, int] = {}
        self.bsd_ambiguous: set[int] = set()
        self.latin_by_team: dict[int, set[str]] = {}
        self.display_by_key: dict[str, str] = {}

    def clone(self) -> "TeamIndex":
        other = TeamIndex()
        other.by_key = dict(self.by_key)
        other.ambiguous = set(self.ambiguous)
        other.bsd_ids = dict(self.bsd_ids)
        other.bsd_ambiguous = set(self.bsd_ambiguous)
        other.latin_by_team = {k: set(v) for k, v in self.latin_by_team.items()}
        other.display_by_key = dict(self.display_by_key)
        return other

    def resolve(self, name: str | None) -> int | None:
        key = norm_key(name)
        if not key or key in self.ambiguous:
            return None
        return self.by_key.get(key)

    def resolve_bsd_side(self, name: str | None, bsd_team_id: int | None) -> int | None:
        """队名与已登记的 BSD team_id 都有且不一致时，不猜，返回 None。"""
        by_name = self.resolve(name) if name else None
        by_id = None
        if bsd_team_id and bsd_team_id not in self.bsd_ambiguous:
            by_id = self.bsd_ids.get(bsd_team_id)
        if by_name is not None and by_id is not None and by_name != by_id:
            return None
        if by_name is not None:
            return by_name
        return by_id

    def has_latin(self, team_id: int | None) -> bool:
        return bool(team_id and self.latin_by_team.get(team_id))

    def add(self, name: str | None, team_id: int, *, bsd_team_id: int | None = None) -> str:
        """返回 inserted / skip_existing / conflict。conflict 表示键已属于另一队。"""
        shown = norm_display(name)
        key = norm_key(shown)
        if not key or team_id is None:
            return "skip_empty"
        if bsd_team_id:
            self._add_bsd_id(bsd_team_id, team_id)
        if is_latin_name(shown):
            self.latin_by_team.setdefault(team_id, set()).add(key)
        if key in self.ambiguous:
            return "conflict"
        current = self.by_key.get(key)
        if current is None:
            self.by_key[key] = team_id
            self.display_by_key[key] = shown
            return "inserted"
        if current == team_id:
            return "skip_existing"
        self.ambiguous.add(key)
        self.by_key.pop(key, None)
        self.display_by_key.pop(key, None)
        return "conflict"

    def _add_bsd_id(self, bsd_team_id: int, team_id: int) -> None:
        if bsd_team_id in self.bsd_ambiguous:
            return
        current = self.bsd_ids.get(bsd_team_id)
        if current is None:
            self.bsd_ids[bsd_team_id] = team_id
        elif current != team_id:
            self.bsd_ambiguous.add(bsd_team_id)
            self.bsd_ids.pop(bsd_team_id, None)


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def load_team_index(conn: sqlite3.Connection) -> TeamIndex:
    """从 teams / team_aliases 建索引。source 里的 bsd_team_id= 只作精确 id，不参与猜名。"""
    index = TeamIndex()
    if not _table_exists(conn, "teams"):
        return index
    for row in conn.execute("SELECT id, name_zh_canonical FROM teams"):
        index.add(row[1], int(row[0]))
    if not _table_exists(conn, "team_aliases"):
        return index
    cols = _table_columns(conn, "team_aliases")
    source_col = "source" if "source" in cols else "NULL"
    for alias, team_id, source in conn.execute(
        f"SELECT alias, team_id, {source_col} FROM team_aliases"
    ):
        bsd_id = None
        matched = _BSD_ID_IN_SOURCE.search(source or "")
        if matched:
            bsd_id = int(matched.group(1))
        index.add(alias, int(team_id), bsd_team_id=bsd_id)
    return index


def load_final_names(path: Path, index: TeamIndex) -> dict:
    """定稿 teams.csv：canonical 能落到已有 team_id 时，把 aliases 列并进内存索引，不写库。"""
    added = skipped = 0
    with open(path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            canonical = row.get("canonical") or row.get("name_zh_canonical") or ""
            team_id = index.resolve(canonical)
            if team_id is None:
                skipped += 1
                continue
            names = [canonical, *(row.get("aliases") or "").split("|")]
            for name in names:
                if not norm_key(name):
                    continue
                result = index.add(name, team_id)
                if result == "inserted":
                    added += 1
    return {"file": str(path), "added": added, "skipped_unresolved_canonical": skipped}


# ----------------------------------------------------------------------------- dc_train 中英别名

def _first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.is_file():
            return path
    return None


def default_alias_paths() -> list[Path]:
    name = Path("research/dc_train/team_aliases_proposed.csv")
    return [_ODDS_DATA_DIR / name, Path("/workspace/odds-data") / name]


def default_final_paths() -> list[Path]:
    name = Path("research/name-standard-2026-10-14/teams.csv")
    return [_ODDS_DATA_DIR / name, Path("/workspace/odds-data") / name]


def _row_get(row: dict, *keys: str) -> str:
    lowered = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
    for key in keys:
        value = lowered.get(key.lower())
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return ""


def load_alias_rows(path: Path) -> list[dict]:
    """读 CSV，或读 sqlite 里带 alias 与 confidence 列的研究表。"""
    if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ]
            chosen = None
            for name in tables:
                cols = {c.lower() for c in _table_columns(conn, name)}
                if "alias" in cols and "confidence" in cols:
                    if name == "team_aliases_proposed":
                        chosen = name
                        break
                    chosen = chosen or name
            if chosen is None:
                raise SystemExit(f"{path} 里没有同时含 alias 与 confidence 的研究表。")
            rows = []
            for record in conn.execute(f"SELECT * FROM {chosen}"):
                rows.append({k: record[k] for k in record.keys()})
            return rows
        finally:
            conn.close()
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _confidence(row: dict) -> str:
    return _row_get(row, "confidence").casefold()


def _bsd_team_id(row: dict) -> int | None:
    return pos_id(_row_get(row, *BSD_ID_KEYS))


def _split_names(row: dict) -> tuple[list[str], list[str]]:
    cjk: list[str] = []
    latin: list[str] = []
    seen: set[str] = set()
    for key in NAME_KEYS:
        raw = _row_get(row, key)
        if not raw or raw in seen:
            continue
        seen.add(raw)
        if is_cjk_name(raw):
            cjk.append(norm_display(raw) or raw)
        elif is_latin_name(raw):
            latin.append(norm_display(raw) or raw)
    return cjk, latin


def _team_name(conn: sqlite3.Connection, team_id: int) -> str | None:
    if not _table_exists(conn, "teams"):
        return None
    row = conn.execute(
        "SELECT name_zh_canonical FROM teams WHERE id=?", (team_id,)
    ).fetchone()
    return row[0] if row else None


def resolve_alias_target(conn: sqlite3.Connection, index: TeamIndex, row: dict, cjk_names: list[str]):
    """确定英文别名要挂上的已有 team_id。对不上就不新建队。"""
    explicit = pos_id(_row_get(row, "team_id", "prod_team_id"))
    name_hits = []
    for name in cjk_names:
        hit = index.resolve(name)
        if hit is not None and hit not in name_hits:
            name_hits.append(hit)
    if explicit is not None:
        canonical = _team_name(conn, explicit)
        if canonical is None:
            explicit = None
        elif cjk_names and norm_key(canonical) not in {norm_key(n) for n in cjk_names}:
            if explicit not in name_hits:
                # 研究表里的编号和中文规范名不是同一队：不用这个编号。
                explicit = None
        if explicit is not None:
            return explicit, None
    if len(name_hits) == 1:
        return name_hits[0], None
    if len(name_hits) > 1:
        return None, "target_ambiguous"
    if _row_get(row, "team_id", "prod_team_id"):
        return None, "target_mismatch"
    return None, "target_unresolved"


def plan_alias_import(conn: sqlite3.Connection, index: TeamIndex, rows: list[dict], min_confidence: str) -> dict:
    """只计划 high/med 的中英别名。低置信、纯中文、无法落到已有队的行都不入库。不改传入的索引。"""
    keep = {"high"} if min_confidence == "high" else set(KEEP_CONFIDENCE)
    planned = []
    bsd_binds = []
    stats = {
        "rows_read": len(rows),
        "kept_high_med_zh_en": 0,
        "skipped_low": 0,
        "skipped_confidence": 0,
        "skipped_not_zh_en": 0,
        "planned_insert": 0,
        "skip_existing": 0,
        "conflicts": 0,
        "target_unresolved": 0,
        "target_ambiguous": 0,
        "target_mismatch": 0,
    }
    # 同一规范化键在本批里指向不同队：两笔都不插入。
    planned_owner: dict[str, int] = {}
    for row in rows:
        conf = _confidence(row)
        if conf == "low":
            stats["skipped_low"] += 1
            continue
        if conf not in keep:
            stats["skipped_confidence"] += 1
            continue
        cjk_names, latin_names = _split_names(row)
        # 有 team_id 且能核到已有队时，允许只有英文名（中文实体由编号表示）。
        if not latin_names or not (cjk_names or _row_get(row, "team_id", "prod_team_id")):
            stats["skipped_not_zh_en"] += 1
            continue
        stats["kept_high_med_zh_en"] += 1
        team_id, problem = resolve_alias_target(conn, index, row, cjk_names)
        if problem or team_id is None:
            stats[problem or "target_unresolved"] += 1
            continue
        bsd_id = _bsd_team_id(row)
        for latin in latin_names:
            key = norm_key(latin)
            if key in planned_owner:
                if planned_owner[key] != team_id:
                    stats["conflicts"] += 1
                    if planned_owner[key] != -1:
                        before_n = len(planned)
                        planned = [item for item in planned if norm_key(item["alias"]) != key]
                        stats["planned_insert"] -= before_n - len(planned)
                        planned_owner[key] = -1
                else:
                    stats["skip_existing"] += 1
                continue
            current = None if key in index.ambiguous else index.by_key.get(key)
            if key in index.ambiguous or (current is not None and current != team_id):
                stats["conflicts"] += 1
                planned_owner[key] = -1
                continue
            if current == team_id:
                stats["skip_existing"] += 1
                planned_owner[key] = team_id
                if bsd_id and index.bsd_ids.get(bsd_id) != team_id and bsd_id not in index.bsd_ambiguous:
                    bsd_binds.append({"bsd_team_id": bsd_id, "team_id": team_id})
                continue
            source = f"{ALIAS_SOURCE_PREFIX}|conf={conf}"
            if bsd_id:
                source += f"|bsd_team_id={bsd_id}"
            planned.append(
                {
                    "alias": norm_display(latin),
                    "team_id": team_id,
                    "source": source,
                    "confidence": conf,
                    "bsd_team_id": bsd_id,
                }
            )
            planned_owner[key] = team_id
            stats["planned_insert"] += 1
    return {"planned": planned, "bsd_binds": bsd_binds, "stats": stats}


def apply_alias_plan(index: TeamIndex, planned: list[dict], bsd_binds: list[dict] | None = None) -> TeamIndex:
    """在内存索引上叠上计划插入的别名，供干跑对照使用。不改原索引。"""
    updated = index.clone()
    for item in planned:
        updated.add(item["alias"], item["team_id"], bsd_team_id=item.get("bsd_team_id"))
    for bind in bsd_binds or []:
        updated._add_bsd_id(int(bind["bsd_team_id"]), int(bind["team_id"]))
    return updated


# ----------------------------------------------------------------------------- BSD 客户端

class BsdClient:
    """BSD v2 事件列表。密钥只放请求头，分页用 limit/offset，返回 (events, 页数)。"""

    def __init__(self, api_key: str, base: str = BSD_BASE, opener=None, sleep=time.sleep):
        if not api_key:
            raise SystemExit("缺少环境变量 BSD_BZZOIRO_API_KEY。")
        self.api_key = api_key
        self.base = base.rstrip("/")
        self.opener = opener or urllib.request.urlopen
        self.sleep = sleep

    def _request(self, params: dict) -> dict:
        query = urllib.parse.urlencode(params)
        url = f"{self.base}/events/?{query}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": "Token " + self.api_key,
                "Accept": "application/json",
                "User-Agent": "football-analyze-tool-bsd-event-map",
            },
        )
        try:
            with self.opener(req, timeout=40) as resp:
                body = resp.read()
                code = getattr(resp, "status", 200)
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode("utf-8", "replace")
            if exc.code in (401, 403):
                raise SystemExit(f"BSD 鉴权失败，HTTP {exc.code}。请检查环境变量 BSD_BZZOIRO_API_KEY。") from None
            raise SystemExit(f"BSD 事件接口 HTTP {exc.code}：{detail}") from None
        except urllib.error.URLError as exc:
            raise SystemExit(f"BSD 事件接口连接失败：{exc.reason}") from None
        if code == 429:
            raise TimeoutError("rate")
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit("BSD 事件接口返回的不是 JSON。") from exc
        if not isinstance(payload, dict):
            raise SystemExit("BSD 事件接口返回结构无法识别。")
        return payload

    def get_events(self, date_from: str, date_to: str) -> tuple[list[dict], int]:
        """拉满一个日期窗。页数按实际请求计。"""
        offset = 0
        pages = 0
        events: list[dict] = []
        seen_ids: set[int] = set()
        while pages < 500:
            params = {"date_from": date_from, "date_to": date_to, "limit": PAGE_LIMIT, "offset": offset}
            try:
                payload = self._request(params)
            except TimeoutError:
                self.sleep(2)
                payload = self._request(params)
            pages += 1
            batch = payload.get("results") or []
            if not isinstance(batch, list):
                raise SystemExit("BSD 事件接口的 results 不是列表。")
            fresh = 0
            for item in batch:
                if not isinstance(item, dict):
                    continue
                eid = pos_id(item.get("id") or item.get("event_id"))
                if eid is not None and eid in seen_ids:
                    continue
                if eid is not None:
                    seen_ids.add(eid)
                events.append(item)
                fresh += 1
            next_url = payload.get("next")
            if not batch or not next_url or len(batch) < PAGE_LIMIT or fresh == 0:
                break
            offset += PAGE_LIMIT
        return events, pages


class FileEventSource:
    """离线事件。按事件开赛时刻的北京时间日期分桶，供 --events-json 与单测。"""

    def __init__(self, by_date: dict[str, list[dict]]):
        self.by_date = by_date
        self.calls: list[tuple[str, str]] = []

    def get_events(self, date_from: str, date_to: str) -> tuple[list[dict], int]:
        self.calls.append((date_from, date_to))
        events: list[dict] = []
        cur = date.fromisoformat(date_from)
        end = date.fromisoformat(date_to)
        while cur <= end:
            events.extend(self.by_date.get(cur.isoformat(), []))
            cur += timedelta(days=1)
        return events, 1


def load_events_json(path: Path) -> FileEventSource:
    data = json.loads(path.read_text(encoding="utf-8"))
    by_date: dict[str, list[dict]] = {}
    if isinstance(data, dict) and "results" in data and isinstance(data["results"], list):
        data = data["results"]
    if isinstance(data, list):
        for item in data:
            kick = event_kickoff(item)
            if kick is None:
                continue
            by_date.setdefault(kick.date().isoformat(), []).append(item)
    elif isinstance(data, dict):
        for key, items in data.items():
            if key in {"count", "next", "previous"}:
                continue
            if isinstance(items, list):
                by_date[str(key)] = [x for x in items if isinstance(x, dict)]
    else:
        raise SystemExit("--events-json 须是事件数组，或 {日期: 事件数组}。")
    return FileEventSource(by_date)


def event_kickoff(event: dict) -> datetime | None:
    for key in ("event_date", "kickoff", "kickoff_at", "date"):
        if event.get(key):
            parsed = parse_dt(event.get(key))
            if parsed is not None:
                return parsed
    return None


def event_side(event: dict, which: str) -> tuple[str | None, int | None]:
    raw = event.get(f"{which}_team")
    name = None
    team_id = None
    if isinstance(raw, dict):
        name = raw.get("name") or raw.get("short_name") or raw.get("name_en")
        team_id = pos_id(raw.get("id"))
    elif raw is not None:
        name = str(raw)
    if team_id is None:
        team_id = pos_id(event.get(f"{which}_team_id"))
    return name, team_id


def event_id_of(event: dict) -> int | None:
    return pos_id(event.get("id") if event.get("id") is not None else event.get("event_id"))


def event_score(event: dict) -> tuple[int, int] | None:
    home = as_goals(event.get("home_score"))
    away = as_goals(event.get("away_score"))
    if home is None or away is None:
        return None
    return home, away


# ----------------------------------------------------------------------------- 对照

def local_team_id(match: dict, index: TeamIndex, side: str) -> int | None:
    """优先用场次上已写的 team_id；没有再按队名解析。解析不到就是未解析，不猜。"""
    stored = pos_id(match.get(f"{side}_team_id"))
    if stored is not None:
        return stored
    return index.resolve(match.get(f"{side}_team"))


def neighbor_dates(kickoff: datetime) -> list[date]:
    """15 分钟窗会跨过的其它北京时间日期。正午场次返回空。"""
    start = (kickoff - KICKOFF_TOLERANCE).date()
    end = (kickoff + KICKOFF_TOLERANCE).date()
    own = kickoff.date()
    return [d for d in (start, end) if d != own]


def within_kickoff(local: datetime, event: dict) -> bool:
    remote = event_kickoff(event)
    if remote is None:
        return False
    return abs(remote - local) <= KICKOFF_TOLERANCE


def candidate_events(match: dict, index: TeamIndex, events: list[dict], kickoff: datetime) -> list[dict]:
    """时间窗内、主客归一后方向一致的事件。对调不算命中。"""
    home_id = local_team_id(match, index, "home")
    away_id = local_team_id(match, index, "away")
    found = []
    seen = set()
    for event in events:
        eid = event_id_of(event)
        if eid is None or eid in seen:
            continue
        if not within_kickoff(kickoff, event):
            continue
        home_name, home_bsd = event_side(event, "home")
        away_name, away_bsd = event_side(event, "away")
        if index.resolve_bsd_side(home_name, home_bsd) != home_id:
            continue
        if index.resolve_bsd_side(away_name, away_bsd) != away_id:
            continue
        seen.add(eid)
        found.append(event)
    return found


def decide_one(match: dict, index: TeamIndex, pool: list[dict], kickoff: datetime | None) -> dict:
    """单场判定。不在这里处理 event_id 跨场冲突。"""
    base = {
        "match_id": match["id"],
        "home_team": match.get("home_team"),
        "away_team": match.get("away_team"),
        "kickoff_at": match.get("kickoff_used"),
        "kickoff_field": match.get("kickoff_field"),
        "home_goals": match.get("home_goals"),
        "away_goals": match.get("away_goals"),
    }
    home_id = local_team_id(match, index, "home")
    away_id = local_team_id(match, index, "away")
    if home_id is None or away_id is None:
        missing = []
        if home_id is None:
            missing.append("home")
        if away_id is None:
            missing.append("away")
        return {**base, "status": "unmatched", "reason": "team_unresolved", "detail": ",".join(missing)}
    if kickoff is None:
        return {**base, "status": "unmatched", "reason": "kickoff_missing", "detail": "无开赛时刻"}
    found = candidate_events(match, index, pool, kickoff)
    ids = [event_id_of(ev) for ev in found]
    if len(found) == 0:
        return {**base, "status": "unmatched", "reason": "no_candidate", "detail": "", "candidate_event_ids": []}
    if len(found) > 1:
        return {
            **base,
            "status": "unmatched",
            "reason": "multi_candidate",
            "detail": ",".join(str(i) for i in ids),
            "candidate_event_ids": ids,
        }
    event = found[0]
    remote_score = event_score(event)
    local_score = (as_goals(match.get("home_goals")), as_goals(match.get("away_goals")))
    score_checked = False
    if remote_score is not None and None not in local_score:
        score_checked = True
        if remote_score != local_score:
            return {
                **base,
                "status": "unmatched",
                "reason": "score_mismatch",
                "detail": f"local={local_score[0]}-{local_score[1]};bsd={remote_score[0]}-{remote_score[1]}",
                "candidate_event_ids": ids,
                "event": event,
            }
    home_name, _ = event_side(event, "home")
    away_name, _ = event_side(event, "away")
    return {
        **base,
        "status": "mapped",
        "reason": None,
        "note": NOTE_AUTO,
        "event_id": event_id_of(event),
        "detail": "",
        "candidate_event_ids": ids,
        "bsd_home": home_name,
        "bsd_away": away_name,
        "bsd_event_date": event.get("event_date") or event.get("kickoff") or event.get("date"),
        "score_checked": score_checked,
        "event": event,
    }


class EventCache:
    def __init__(self, client):
        self.client = client
        self.days: dict[str, list[dict]] = {}
        self.pages = 0
        self.calls: list[str] = []

    def day(self, day: date, *, fetch: bool = True) -> list[dict]:
        key = day.isoformat()
        if key not in self.days:
            if not fetch:
                # 别名覆盖前后对比复用已经拉过的日期，不为「导入前」再打接口。
                return []
            if self.client is None:
                raise SystemExit(
                    "需要拉取 BSD 事件，但未配置 BSD_BZZOIRO_API_KEY，也未提供 --events-json。"
                )
            events, pages = self.client.get_events(key, key)
            self.pages += pages
            self.calls.append(key)
            self.days[key] = list(events)
        return self.days[key]


def classify_matches(matches: list[dict], index: TeamIndex, cache: EventCache, *, fetch: bool = True) -> list[dict]:
    """先查开赛日当天。当天 0 候选才扩 ±1 日。15 分钟窗跨日时补相邻日，避免漏掉第二个候选。

    fetch=False 时缺的日期当空，不新发请求。用于别名导入前的对照，避免为对比多耗额度。
    """
    decisions = []
    for match in matches:
        kickoff = parse_dt(match.get("kickoff_used"))
        home_id = local_team_id(match, index, "home")
        away_id = local_team_id(match, index, "away")
        if home_id is None or away_id is None or kickoff is None:
            decisions.append(decide_one(match, index, [], kickoff))
            continue
        primary = cache.day(kickoff.date(), fetch=fetch)
        found = candidate_events(match, index, primary, kickoff)
        pool = list(primary)
        if not found:
            for delta in (-1, 1):
                pool.extend(cache.day(kickoff.date() + timedelta(days=delta), fetch=fetch))
        else:
            for extra in neighbor_dates(kickoff):
                pool.extend(cache.day(extra, fetch=fetch))
        decisions.append(decide_one(match, index, pool, kickoff))
    return decisions


def reason_counts(decisions: list[dict]) -> dict[str, int]:
    counts = {reason: 0 for reason in CORE_REASONS + EXTRA_REASONS}
    for item in decisions:
        if item.get("status") == "unmatched":
            reason = item.get("reason") or "no_candidate"
            counts[reason] = counts.get(reason, 0) + 1
    return {k: v for k, v in counts.items() if v}


def coverage_snapshot(matches: list[dict], index: TeamIndex) -> dict:
    """别名覆盖：完赛场双方能否解析，以及双方是否都有拉丁别名（BSD 英文队名的前置条件）。"""
    both_resolved = both_latin = 0
    teams: set[int] = set()
    teams_latin: set[int] = set()
    for match in matches:
        home_id = local_team_id(match, index, "home")
        away_id = local_team_id(match, index, "away")
        if home_id is not None and away_id is not None:
            both_resolved += 1
        if home_id is not None:
            teams.add(home_id)
            if index.has_latin(home_id):
                teams_latin.add(home_id)
        if away_id is not None:
            teams.add(away_id)
            if index.has_latin(away_id):
                teams_latin.add(away_id)
        if home_id and away_id and index.has_latin(home_id) and index.has_latin(away_id):
            both_latin += 1
    return {
        "finished": len(matches),
        "both_teams_resolved": both_resolved,
        "both_sides_latin_alias": both_latin,
        "teams_in_finished": len(teams),
        "teams_with_latin_alias": len(teams_latin),
    }


def publish_summary(decisions: list[dict], existing: dict[int, int], known_ids: set[int]) -> dict:
    """可映射数按「会写入的行」计，已映射与 event_id 冲突都不算可映射。"""
    writes, rest = assign_writes(decisions, [], existing, known_ids)
    unmatched = [item for item in rest if item.get("status") == "unmatched"]
    return {"mappable": len(writes), "unmatched": reason_counts(unmatched)}


# ----------------------------------------------------------------------------- 库表读写

def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_finished(conn: sqlite3.Connection, limit: int | None) -> list[dict]:
    if not _table_exists(conn, "matches") or not _table_exists(conn, "results"):
        raise SystemExit("库里没有 matches 或 results，无法枚举完赛场次。")
    cols = _table_columns(conn, "matches")
    home_id = "m.home_team_id" if "home_team_id" in cols else "NULL"
    away_id = "m.away_team_id" if "away_team_id" in cols else "NULL"
    kickoff_actual = "m.kickoff_actual" if "kickoff_actual" in cols else "NULL"
    sql = f"""
        SELECT m.id, m.home_team, m.away_team, {home_id} AS home_team_id, {away_id} AS away_team_id,
               m.kickoff_at AS kickoff_at, {kickoff_actual} AS kickoff_actual,
               r.home_goals AS home_goals, r.away_goals AS away_goals
        FROM matches m
        JOIN results r ON r.match_id = m.id
        WHERE r.home_goals IS NOT NULL AND r.away_goals IS NOT NULL
        ORDER BY m.id
    """
    rows = []
    for record in conn.execute(sql):
        item = {k: record[k] for k in record.keys()}
        actual = item.get("kickoff_actual")
        if actual:
            item["kickoff_used"] = actual
            item["kickoff_field"] = "kickoff_actual"
        else:
            item["kickoff_used"] = item.get("kickoff_at")
            item["kickoff_field"] = "kickoff_at" if item.get("kickoff_at") else None
        rows.append(item)
        if limit is not None and len(rows) >= limit:
            break
    return rows


def load_existing_map(conn: sqlite3.Connection) -> dict[int, int]:
    if not _table_exists(conn, MAP_TABLE):
        return {}
    cols = _table_columns(conn, MAP_TABLE)
    if not {"match_id", "event_id"} <= cols:
        raise SystemExit("bsd_event_map 已存在，但缺少 match_id / event_id，拒绝继续。")
    return {int(m): int(e) for m, e in conn.execute(f"SELECT match_id, event_id FROM {MAP_TABLE}")}


def load_known_match_ids(conn: sqlite3.Connection) -> set[int]:
    return {int(r[0]) for r in conn.execute("SELECT id FROM matches")}


def load_manual_csv(path: Path) -> list[tuple[int, int]]:
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "match_id" not in reader.fieldnames or "event_id" not in reader.fieldnames:
            raise SystemExit("--manual-csv 需要表头 match_id,event_id。")
        for line_no, row in enumerate(reader, start=2):
            try:
                mid = int(str(row["match_id"]).strip())
                eid = int(str(row["event_id"]).strip())
            except (TypeError, ValueError):
                raise SystemExit(f"--manual-csv 第 {line_no} 行 match_id/event_id 不是整数。") from None
            if mid <= 0 or eid <= 0:
                raise SystemExit(f"--manual-csv 第 {line_no} 行编号必须为正整数。")
            rows.append((mid, eid))
    return rows


def assign_writes(decisions: list[dict], manuals: list[tuple[int, int]], existing: dict[int, int], known_ids: set[int]) -> tuple[list[dict], list[dict]]:
    """已映射不覆盖。同一 event_id 被多场同时要走时，相关自动行改记 event_id_conflict，都不写。"""
    taken_match = set(existing)
    taken_event = {event_id: match_id for match_id, event_id in existing.items()}
    writes: list[dict] = []
    extras: list[dict] = []
    manual_by_event: dict[int, list[int]] = {}
    for mid, eid in manuals:
        manual_by_event.setdefault(eid, []).append(mid)
    claimed_manual_events: set[int] = set()
    for mid, eid in manuals:
        if mid not in known_ids:
            extras.append({"match_id": mid, "status": "unmatched", "reason": "manual_match_missing",
                           "detail": f"event_id={eid}", "event_id": eid, "note": NOTE_MANUAL})
            continue
        if mid in taken_match:
            extras.append({"match_id": mid, "status": "skipped_existing", "reason": "already_mapped",
                           "detail": f"event_id={existing.get(mid)}", "event_id": existing.get(mid), "note": NOTE_MANUAL})
            continue
        if len(manual_by_event.get(eid, [])) > 1 or eid in taken_event:
            extras.append({"match_id": mid, "status": "unmatched", "reason": "manual_conflict",
                           "detail": f"event_id={eid}", "event_id": eid, "note": NOTE_MANUAL})
            continue
        claimed_manual_events.add(eid)
        taken_match.add(mid)
        taken_event[eid] = mid
        writes.append({"match_id": mid, "event_id": eid, "note": NOTE_MANUAL, "status": "mapped",
                       "reason": None, "detail": "manual", "score_checked": False})

    auto = [d for d in decisions if d.get("status") == "mapped"]
    by_event: dict[int, list[dict]] = {}
    for item in auto:
        by_event.setdefault(int(item["event_id"]), []).append(item)
    blocked_events = {eid for eid, group in by_event.items() if len(group) > 1}
    for item in decisions:
        if item.get("status") != "mapped":
            extras.append(item)
            continue
        mid = int(item["match_id"])
        eid = int(item["event_id"])
        if mid in taken_match:
            extras.append({**item, "status": "skipped_existing", "reason": "already_mapped",
                           "detail": f"event_id={existing.get(mid, taken_event.get(eid))}"})
            continue
        if eid in blocked_events or eid in taken_event:
            extras.append({**item, "status": "unmatched", "reason": "event_id_conflict",
                           "detail": f"event_id={eid}"})
            continue
        taken_match.add(mid)
        taken_event[eid] = mid
        writes.append(item)
    return writes, extras


def ensure_map_table(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, MAP_TABLE):
        cols = _table_columns(conn, MAP_TABLE)
        missing = [c for c in MAP_COLUMNS if c not in cols]
        if missing:
            raise SystemExit(f"bsd_event_map 已存在但缺少列 {missing}，拒绝改表。")
        return
    conn.execute(
        f"""
        CREATE TABLE {MAP_TABLE} (
          match_id INTEGER PRIMARY KEY,
          event_id INTEGER NOT NULL UNIQUE,
          mapped_at TEXT NOT NULL,
          note TEXT
        )
        """
    )


def write_aliases(conn: sqlite3.Connection, planned: list[dict], mapped_at: str) -> int:
    if not planned:
        return 0
    if not _table_exists(conn, "team_aliases"):
        raise SystemExit("库里没有 team_aliases，拒绝自动建别名表。请先做 v1.2 migration。")
    cols = _table_columns(conn, "team_aliases")
    written = 0
    for item in planned:
        existing = conn.execute(
            "SELECT team_id FROM team_aliases WHERE alias=?", (item["alias"],)
        ).fetchone()
        if existing:
            if int(existing[0]) != int(item["team_id"]):
                continue
            continue
        if "created_at" in cols:
            conn.execute(
                "INSERT INTO team_aliases(alias, team_id, source, created_at) VALUES (?,?,?,?)",
                (item["alias"], item["team_id"], item["source"], mapped_at),
            )
        else:
            conn.execute(
                "INSERT INTO team_aliases(alias, team_id, source) VALUES (?,?,?)",
                (item["alias"], item["team_id"], item["source"]),
            )
        written += 1
    return written


def write_map(conn: sqlite3.Connection, writes: list[dict], mapped_at: str) -> int:
    ensure_map_table(conn)
    written = 0
    for item in writes:
        mid, eid = int(item["match_id"]), int(item["event_id"])
        clash = conn.execute(
            f"SELECT match_id, event_id FROM {MAP_TABLE} WHERE match_id=? OR event_id=?",
            (mid, eid),
        ).fetchone()
        if clash:
            continue
        conn.execute(
            f"INSERT INTO {MAP_TABLE}(match_id, event_id, mapped_at, note) VALUES (?,?,?,?)",
            (mid, eid, mapped_at, item.get("note") or NOTE_AUTO),
        )
        written += 1
    return written


def guard_apply(db: Path) -> Path:
    """写库前走正式库闸门。模块按当前环境变量重新加载，避免进程里早先导入把路径钉死。"""
    api_root = str(_MA_API_ROOT)
    if api_root not in sys.path:
        sys.path.insert(0, api_root)
    import importlib
    import app.formal_db as formal_db

    importlib.reload(formal_db)
    return formal_db.guard_write_target(db, writing=True)


# ----------------------------------------------------------------------------- 输出

def write_csv(out_dir: Path, writes: list[dict], unmatched: list[dict], mapped_at: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    map_path = out_dir / "event_map.csv"
    unmatched_path = out_dir / "unmatched.csv"
    with map_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["match_id", "event_id", "mapped_at", "note", "kickoff_at", "bsd_event_date",
                        "home_team", "away_team", "bsd_home", "bsd_away", "score_checked"],
        )
        writer.writeheader()
        for item in writes:
            writer.writerow({
                "match_id": item["match_id"],
                "event_id": item["event_id"],
                "mapped_at": mapped_at,
                "note": item.get("note") or NOTE_AUTO,
                "kickoff_at": item.get("kickoff_at") or "",
                "bsd_event_date": item.get("bsd_event_date") or "",
                "home_team": item.get("home_team") or "",
                "away_team": item.get("away_team") or "",
                "bsd_home": item.get("bsd_home") or "",
                "bsd_away": item.get("bsd_away") or "",
                "score_checked": "1" if item.get("score_checked") else "0",
            })
    with unmatched_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["match_id", "reason", "detail", "home_team", "away_team", "kickoff_at",
                        "home_goals", "away_goals", "candidate_event_ids"],
        )
        writer.writeheader()
        for item in unmatched:
            ids = item.get("candidate_event_ids") or []
            writer.writerow({
                "match_id": item.get("match_id"),
                "reason": item.get("reason"),
                "detail": item.get("detail") or "",
                "home_team": item.get("home_team") or "",
                "away_team": item.get("away_team") or "",
                "kickoff_at": item.get("kickoff_at") or "",
                "home_goals": "" if item.get("home_goals") is None else item.get("home_goals"),
                "away_goals": "" if item.get("away_goals") is None else item.get("away_goals"),
                "candidate_event_ids": ",".join(str(i) for i in ids),
            })


def _print_summary(report: dict) -> None:
    mode = "写入" if report["apply"] else "干跑"
    print(f"模式: {mode}")
    print(f"完赛: {report['finished']}")
    print(f"可映射: {report['mappable']}")
    print(f"已有映射跳过: {report['skipped_existing']}")
    print(f"本次写 bsd_event_map: {report['wrote_rows']}")
    print("unmatched:")
    unmatched = report["unmatched"] or {}
    if not unmatched:
        print("  （无）")
    for reason in list(CORE_REASONS) + [r for r in unmatched if r not in CORE_REASONS]:
        if reason in unmatched:
            print(f"  {reason}: {unmatched[reason]}")
    alias = report.get("alias_import")
    if alias:
        before = alias["coverage_before"]
        after = alias["coverage_after"]
        print(
            "别名覆盖（双方可解析）: "
            f"导入前 {before['both_teams_resolved']}/{before['finished']} → "
            f"导入后 {after['both_teams_resolved']}/{after['finished']}"
        )
        print(
            "别名覆盖（双方有拉丁别名）: "
            f"导入前 {before['both_sides_latin_alias']}/{before['finished']} → "
            f"导入后 {after['both_sides_latin_alias']}/{after['finished']}"
        )
        if "map_before" in alias:
            print(
                "可映射前后: "
                f"{alias['map_before']['mappable']} → {alias['map_after']['mappable']}"
            )
        stats = alias["stats"]
        print(
            "别名计划: "
            f"读入 {stats['rows_read']}，低置信跳过 {stats['skipped_low']}，"
            f"拟插入 {stats['planned_insert']}，冲突 {stats['conflicts']}，"
            f"落不到已有队 {stats['target_unresolved'] + stats['target_mismatch'] + stats['target_ambiguous']}，"
            f"实际写入 {alias['written']}"
        )
    print(f"BSD 分页请求: {report['bsd_pages']}")
    print(f"旁路目录: {report['out_dir']}")


# ----------------------------------------------------------------------------- 主流程

def _out_dir(ns) -> Path:
    if ns.out_dir:
        return Path(ns.out_dir)
    env = _rp_os.environ.get("BSD_EVENT_MAP_OUT_DIR", "").strip()
    if env:
        return Path(env)
    return DEFAULT_OUT_DIR


def _resolve_alias_file(ns) -> Path | None:
    if ns.aliases_csv:
        return Path(ns.aliases_csv)
    return _first_existing(default_alias_paths()) or default_alias_paths()[0]


def run(ns, client=None) -> dict:
    db_path = Path(ns.db).resolve()
    if not db_path.is_file():
        raise SystemExit(f"找不到数据库：{db_path}")
    if ns.apply:
        guard_apply(db_path)
    if ns.aliases_only and not ns.import_aliases_from_dc_train:
        raise SystemExit("--aliases-only 需要同时加上 --import-aliases-from-dc-train（或使用子命令 import-aliases）。")

    ro = connect_ro(db_path)
    try:
        # 别名覆盖要按完赛场次算，所以只导别名时也读完赛清单，只是不请求 BSD。
        matches = load_finished(ro, ns.limit)
        index_before = load_team_index(ro)
        existing = {} if ns.aliases_only else load_existing_map(ro)
        known_ids = load_known_match_ids(ro)
        final_info = None
        if not ns.no_final_names:
            final_path = Path(ns.final_names) if ns.final_names else _first_existing(default_final_paths())
            if final_path is not None:
                final_info = load_final_names(final_path, index_before)
        alias_report = None
        index_for_map = index_before
        planned: list[dict] = []
        if ns.import_aliases_from_dc_train:
            alias_path = _resolve_alias_file(ns)
            if alias_path is None or not alias_path.is_file():
                raise SystemExit(f"找不到 dc_train 别名研究表：{alias_path}")
            coverage_before = coverage_snapshot(matches, index_before)
            rows = load_alias_rows(alias_path)
            planned_pack = plan_alias_import(ro, index_before, rows, ns.min_confidence)
            planned = planned_pack["planned"]
            index_after = apply_alias_plan(index_before, planned, planned_pack["bsd_binds"])
            coverage_after = coverage_snapshot(matches, index_after)
            alias_report = {
                "file": str(alias_path),
                "min_confidence": ns.min_confidence,
                "stats": planned_pack["stats"],
                "coverage_before": coverage_before,
                "coverage_after": coverage_after,
                "written": 0,
            }
            index_for_map = index_after
        decisions_after: list[dict] = []
        pages = 0
        calls: list[str] = []
        if not ns.aliases_only:
            if client is None and ns.events_json:
                client = load_events_json(Path(ns.events_json))
            if client is None and _needs_client(matches, index_for_map):
                client = BsdClient(_rp_os.environ.get("BSD_BZZOIRO_API_KEY", "").strip())
            # 先按导入后的别名拉事件（这是真正要写的结果）。导入前对照只复用这些响应。
            cache = EventCache(client)
            decisions_after = classify_matches(matches, index_for_map, cache, fetch=True)
            if alias_report is not None:
                alias_report["map_after"] = publish_summary(decisions_after, existing, known_ids)
                decisions_before = classify_matches(matches, index_before, cache, fetch=False)
                alias_report["map_before"] = publish_summary(decisions_before, existing, known_ids)
            pages = cache.pages
            calls = cache.calls
    finally:
        ro.close()

    manuals = load_manual_csv(Path(ns.manual_csv)) if ns.manual_csv else []
    writes, rest = assign_writes(decisions_after, manuals, existing, known_ids)
    skipped = [item for item in rest if item.get("status") == "skipped_existing"]
    unmatched = [item for item in rest if item.get("status") == "unmatched"]
    mapped_at = now_bj_iso()
    wrote_rows = 0
    aliases_written = 0
    if ns.apply and (writes or planned):
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            if planned:
                aliases_written = write_aliases(conn, planned, mapped_at)
            if writes:
                wrote_rows = write_map(conn, writes, mapped_at)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    if alias_report is not None:
        alias_report["written"] = aliases_written

    out_dir = _out_dir(ns)
    if not ns.aliases_only:
        write_csv(out_dir, writes, unmatched, mapped_at)
    report = {
        "apply": bool(ns.apply),
        "db": str(db_path),
        "finished": len(matches),
        "mappable": len(writes),
        "skipped_existing": len(skipped),
        "unmatched": reason_counts(unmatched),
        "wrote_rows": wrote_rows,
        "bsd_pages": pages,
        "bsd_dates": calls,
        "out_dir": str(out_dir),
        "final_names": final_info,
        "alias_import": alias_report,
        "writes": [{k: v for k, v in item.items() if k != "event"} for item in writes],
        "unmatched_rows": [{k: v for k, v in item.items() if k != "event"} for item in unmatched],
    }
    if not ns.aliases_only:
        (out_dir / "bsd_event_map_report.json").write_text(
            json.dumps({k: v for k, v in report.items() if k not in {"writes", "unmatched_rows"}},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if not ns.quiet:
        _print_summary(report)
    return report


def _needs_client(matches: list[dict], index: TeamIndex) -> bool:
    for match in matches:
        if local_team_id(match, index, "home") is None or local_team_id(match, index, "away") is None:
            continue
        if parse_dt(match.get("kickoff_used")) is None:
            continue
        return True
    return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bsd_event_map_build.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "把完赛场次对照到 BSD event_id。\n"
            "默认干跑：只输出可映射数、unmatched 原因分布和旁路 CSV，不写库。\n"
            "自动写入条件（须同时满足）：开赛差 ≤ 15 分钟、主客别名归一且方向一致、候选恰好 1 场；\n"
            "两侧都有比分时还要比分一致。不满足则不写表，unmatched 原因：\n"
            "no_candidate / multi_candidate / score_mismatch / team_unresolved。\n"
            "已映射行不覆盖。note 自动为 kickoff15_alias，人工 CSV 为 manual。\n"
            "正式库队名多为中文、BSD 为英文。建议先导入 dc_train 的 high/med 中英别名再对照。\n"
            "低置信别名不自动入库。禁止按编辑距离猜队名。"
        ),
        epilog=(
            "示例：\n"
            "  干跑（离线事件文件）：\n"
            "    python scripts/backfill/bsd_event_map_build.py --db api/data/app.db --events-json events.json\n"
            "  先看别名覆盖再干跑对照（不写库）：\n"
            "    python scripts/backfill/bsd_event_map_build.py --import-aliases-from-dc-train \\\n"
            "        --aliases-csv data/odds-data/research/dc_train/team_aliases_proposed.csv\n"
            "  只导入别名：\n"
            "    python scripts/backfill/bsd_event_map_build.py import-aliases --aliases-csv 研究表.csv\n"
            "  确认干跑结果后写入（正式库须已备份并设置 FORMAL_DB_BACKUP_DONE=1）：\n"
            "    python scripts/backfill/bsd_event_map_build.py --apply\n"
            "环境变量：BSD_BZZOIRO_API_KEY（Authorization: Token）、APP_DB_PATH、ODDS_DATA_DIR、\n"
            "BSD_EVENT_MAP_OUT_DIR。密钥不会写入报告或 CSV。"
        ),
    )
    parser.add_argument("--db", default=str(_APP_DB), help="SQLite 路径。默认 APP_DB_PATH 或 api/data/app.db")
    parser.add_argument("--apply", action="store_true", help="写入 bsd_event_map（以及本次计划的别名）。默认干跑不写库")
    parser.add_argument("--import-aliases-from-dc-train", action="store_true",
                        help="先导入 dc_train 研究表中 high/med 中英别名，再对照；报告含覆盖前后对比")
    parser.add_argument("--aliases-only", action="store_true", help="只做别名导入，不拉 BSD、不写 bsd_event_map")
    parser.add_argument("--aliases-csv", default=None,
                        help="dc_train 别名 CSV 或 sqlite。默认 ODDS_DATA_DIR/research/dc_train/team_aliases_proposed.csv")
    parser.add_argument("--min-confidence", choices=["high", "med"], default="med",
                        help="别名置信度下限。med 表示 high 与 med 都收，low 一律不收（默认 med）")
    parser.add_argument("--final-names", default=None, help="定稿 teams.csv。默认识别 name-standard 路径，只参与解析不写库")
    parser.add_argument("--no-final-names", action="store_true", help="不读定稿常用名")
    parser.add_argument("--events-json", default=None, help="离线 BSD 事件 JSON，替代在线接口（单测与无密钥干跑）")
    parser.add_argument("--manual-csv", default=None, help="人工确认的 match_id,event_id。--apply 时 note=manual，不覆盖已有行")
    parser.add_argument("--out-dir", default=None,
                        help="旁路 CSV 目录。默认 /workspace/odds-data/bsd-bzzoiro/prematch-injury-weather")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 场完赛（按 match_id），默认全部完赛")
    parser.add_argument("--quiet", action="store_true", help="不打印摘要（仍写 CSV 与报告）")
    return parser


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "import-aliases":
        argv = ["--import-aliases-from-dc-train", "--aliases-only", *argv[1:]]
    ns = build_parser().parse_args(argv)
    run(ns)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0) from None
