"""队名与赛事名待定池的核心库。

本模块负责四件事：计算规范化键和宽松键，识别不能自动映射的歧义标记，
把还不能确认的名称记入待定池，以及在满足判据时自动映射并写入同一张审计表。
撤销也写在这张审计表里，用 mode 区分自动、人工和撤销。

分析师已经确定的规则如下。名称组的七步顺序写在 docs/schema/v2_0-name-resolve-group-rules.md。
本模块不另做一套并组：它实现规范化、精确命中、歧义闸，以及并入已有组时写入别名和空着的身份编号。
同场上的其他证据、赛事正向一致、库外资料，都还没有单独的自动并组动作；门槛未写明之前不能靠它们自动并组。
去掉整词前缀或结尾的「队」只在已有同场上下文时留下候选，而且仍然不能自动并组。没有同场上下文时，不记这份额外候选。
外部来源只写入别名，不改常用名列。无法唯一确定时进入待定池，不新建球队或赛事。本模块不抓取网页或百科。
等级 1 是规范化键与已知别名或标准名完全相等，会自动生效，并且可以撤销。
等级 2 在两种情况下自动生效。一种是宽松键与唯一标准名完全相等。另一种是宽松键与已有别名的宽松键相同，并且这些别名只指向同一个标准名。
等级 2 是本模块原有行为，不是七步里新增的门槛。别名指向多个标准名，或者来源赛事与该球队已有赛事没有交集时，仍然进入待定池。
同名不同队不给别名表增加赛事列，也不修改任何已有表的结构。身份编号无法唯一确定时保持为空，交给人工处理。
映射只填写身份编号和别名，不修改已经冻结的预测。审计表用 effective_at 记录映射生效时间。
审计保持一张表。所有新表的时间都使用北京时间的国际标准化组织 8601 文本，并带有 +08:00。

公开函数如下。
norm_key(name) 计算规范化键，结果与 app.team_pending.norm 相同。
loose_key(name) 在规范化键上删除空白和方案规定的标点。
detect_ambiguity(name) 识别性别、年龄段、二队和已经点名的同名歧义，返回原因代码或空。
ensure_schema(conn) 在调用方给出的连接上执行建表语句。可以重复执行。
pool_observe(conn, kind, raw_name, source, source_ref, context=None) 上报一次出现。
revoke_mapping(conn, audit_id, actor) 撤销一条自动映射或人工映射。

建表方式如下。
建表语句在 docs/schema/alias_pool.sql。导入本模块不会打开正式库，也不会把该文件注册进自动迁移。
调用方打开自己的连接后执行 ensure_schema。写入前会先经过 formal_db.guard_write_target。
写正式库时必须已经设置环境变量 FORMAL_DB_BACKUP_DONE=1。已归档的研究副本一律拒绝写入。

回滚方式如下。
按顺序删除 alias_mapping_audit、alias_pool_sighting、alias_pool_entry、alias_auto_block。
若自动映射新插入了别名行，撤销函数会按审计记录中的别名行编号删除该行。
撤销只恢复该次映射实际改过的身份编号列，并且只有当前值仍等于映射写入的值时才恢复。已经被改过的列跳过，撤销审计用完整句子说明原因。
直接自动映射、从未进入待定池的名称，撤销后会新建待定池记录，原因记为 auto_revoked，并写一条出现记录。
已经冻结的预测不在撤销范围内，因为自动映射和撤销都不会修改预测表。

本模块不接入赛果入库、竞彩场次构建、预期进球观测或导入库。那些入口属于后续拉取请求。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from app.db import split_sql
from app.formal_db import guard_write_target
from app.team_pending import norm as _team_pending_norm

_BEIJING = timezone(timedelta(hours=8))
_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs" / "schema" / "alias_pool.sql"

KIND_TEAM = "team"
KIND_COMPETITION = "competition"
_KINDS = frozenset((KIND_TEAM, KIND_COMPETITION))

MODE_AUTO = "auto"
MODE_MANUAL = "manual"
MODE_REVOKE = "revoke"

STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"
STATUS_AMBIGUOUS = "ambiguous"

CRITERION_KNOWN_ALIAS = "known_alias_exact"
CRITERION_LOOSE_KEY = "loose_key_equal"
CRITERION_REVOKE = "revoke"

CONFIDENCE_LEVEL1 = "level1_known_alias"
CONFIDENCE_LEVEL2 = "level2_equal_after_cleaning"
CONFIDENCE_REVOKE = "revoke"

REASON_GENDER = "gender"
REASON_AGE = "age_group"
REASON_RESERVE = "reserve_team"
REASON_SAME_NAME = "same_name_other_team"
REASON_MULTI = "multi_candidate"
REASON_REVOKED = "auto_revoked"

BASIS_ALIAS = "alias_table"
BASIS_LOOSE = "loose_key"
BASIS_AFFIX = "affix"

_ALIAS_TABLE = {KIND_TEAM: "team_aliases", KIND_COMPETITION: "league_aliases"}
_STANDARD_TABLE = {KIND_TEAM: "teams", KIND_COMPETITION: "leagues"}
_STANDARD_ID_COLUMN = {KIND_TEAM: "team_id", KIND_COMPETITION: "league_id"}

# 方案点名要删除的标点。全角形式在规范化之后通常已经变成半角，这里仍保留全角，避免漏删。
# 只删除这些字符，不把它们替换成别的字符，也不做繁简转换或音译。
_LOOSE_PUNCTUATION = frozenset(
    {
        ".",
        "．",
        "·",
        "・",
        "-",
        "－",
        "–",
        "'",
        "’",
        "(",
        ")",
        "（",
        "）",
        "/",
        "／",
        "_",
        "＿",
    }
)

# 设计文档已经点名、不能因为库里目前只有一个标准名就自动合并的名称。
# 阿赫利同时被用来称呼沙特和埃及的球队，因此名称中只要出现这几个字就不自动映射，沙特阿赫利也算在内。
# 韩国按整个规范化键相等来判断。大韩民国按包含关系判断。
# 英文 Al Ahly 与 Al Ahli 用单独的表达式识别。这里不收入全部国家名称。
_KNOWN_HOMONYM_KEYS = frozenset(("阿赫利", "韩国"))

_CONTEXT_KEYS = ("competition_name", "jingcai_date", "kickoff_at", "region", "country", "opponent_mapped")
_AFFIX_PREFIXES = ("fc", "ac", "cs", "sc")
_POOL_TABLES = (
    "alias_pool_entry",
    "alias_pool_sighting",
    "alias_mapping_audit",
    "alias_auto_block",
)
_MATCH_SLOT = {
    "home_team": (KIND_TEAM, "home_team_id"),
    "home": (KIND_TEAM, "home_team_id"),
    "away_team": (KIND_TEAM, "away_team_id"),
    "away": (KIND_TEAM, "away_team_id"),
    "competition_name": (KIND_COMPETITION, "league_id"),
    "competition": (KIND_COMPETITION, "league_id"),
}
_IDENTITY_COLUMNS = ("home_team_id", "away_team_id", "league_id")
_IDENTITY_LABEL = {
    "home_team_id": "主队编号",
    "away_team_id": "客队编号",
    "league_id": "赛事编号",
}
_SOURCE_REF_PATTERNS = (
    re.compile(r"matches\.id=(\d+)#(home_team|away_team|competition_name)(?![A-Za-z0-9_])"),
    re.compile(r"(?<![A-Za-z0-9_])match_id=(\d+)#(home|away|competition)(?![A-Za-z0-9_])"),
)
_AGE_U = re.compile(r"(?<![a-z])u[\s.\-]*?(\d{2})(?!\d)")
_SUB_AGE = re.compile(r"(?<![a-z])sub[\s.\-]*(\d{2})(?!\d)")
_PAREN_W = re.compile(r"\(\s*w\s*\)")
_STANDALONE_W = re.compile(r"(?<![a-z0-9])w(?![a-z0-9])")
_STANDALONE_B = re.compile(r"(?<![a-z0-9])b(?![a-z0-9])")
_CASTILLA_MARK = re.compile(r"(?<![a-z])castilla(?![a-z])")
_AHLY_MARK = re.compile(
    r"(?<![a-z])al[\s.\-·・]*ahly(?![a-z])|(?<![a-z])al[\s.\-·・]*ahli(?![a-z])"
)
_FEM_MARK = re.compile(r"(?<![a-z])fem(?![a-z])")
_II_MARK = re.compile(r"(?<![a-z])ii(?![a-z])|(?<=[a-hj-uwyz])ii(?![a-z])")
_RES_MARK = re.compile(r"(?<![a-z])res(?![a-z])")
_YOUTH_MARK = re.compile(r"(?<![a-z])youth(?![a-z])")
_RESERVES_MARK = re.compile(r"(?<![a-z])reserves(?![a-z])")
_WOMEN_MARK = re.compile(r"(?<![a-z])women(?![a-z])")
_LADIES_MARK = re.compile(r"(?<![a-z])ladies(?![a-z])")
_REASON_SENTENCE = {
    REASON_GENDER: "名称含有性别标记，不能自动映射。",
    REASON_AGE: "名称含有年龄段标记，不能自动映射。",
    "age_u22": "名称含有年龄段标记 U22，不能自动映射。",
    REASON_RESERVE: "名称含有二队或预备队标记，不能自动映射。",
    REASON_SAME_NAME: "这个名称对应一支以上的球队，或者是已经点名的同名歧义，不能自动映射。",
    REASON_MULTI: "这个名称对应一个以上的标准名，不能自动映射。",
    REASON_REVOKED: "这个名称的自动映射曾经被撤销，不能再次自动映射。",
}
_CRITERION_SENTENCE = {
    CRITERION_KNOWN_ALIAS: "判据是规范化键与已知别名或标准名完全相等。",
    CRITERION_LOOSE_KEY: "判据是宽松键与唯一的标准名完全相等。",
}


def now_beijing_text() -> str:
    """返回当前北京时间的国际标准化组织 8601 文本，精确到秒，并带有 +08:00。"""
    return datetime.now(_BEIJING).isoformat(timespec="seconds")


def norm_key(name: Any) -> str:
    """计算规范化键。

    规则与球队待确认模块的 norm 函数相同：先做兼容等价分解与组合，再去掉首尾空白，
    把连续空白合并为一个空格，并把拉丁字母按不区分大小写的规则折叠。名称为空时返回空字符串。
    """
    return _team_pending_norm(name)


def loose_key(name: Any) -> str:
    """计算宽松键。

    先取规范化键，再删除全部空白，并删除方案规定的点、中点、连字符、撇号、圆括号、斜杠和下划线。
    只做删除。不做替换，不做繁简转换，不做音译，也不去掉队名前后缀。
    """
    base = norm_key(name)
    if not base:
        return ""
    without_space = re.sub(r"\s+", "", base)
    return "".join(character for character in without_space if character not in _LOOSE_PUNCTUATION)


def _age_reason(text: str) -> str | None:
    """识别年龄段标记，并给出原因代码。

    任意两位数字的 U 年龄都算年龄段，数字前允许空格、点或连字符。
    U22 的原因代码是 age_u22，用来标明这是年龄段，而不是名称结尾的数字 2 所代表的二队。
    其余 U 年龄段、Sub 加两位年龄，以及「青年」「预备」「梯队」，原因代码仍是 age_group。
    """
    found = _AGE_U.search(text)
    if found is not None:
        if found.group(1) == "22":
            return "age_u22"
        return REASON_AGE
    if _SUB_AGE.search(text) or "青年" in text or "预备" in text or "梯队" in text:
        return REASON_AGE
    return None


def _is_known_homonym(text: str) -> bool:
    """判断名称是不是已经点名的同名歧义。

    阿赫利按包含判断，因此沙特阿赫利也会被拦住。韩国按整个规范化键相等判断。
    大韩民国按包含判断。英文 Al Ahly 与 Al Ahli 允许中间有空格、点或连字符。
    """
    if "阿赫利" in text or "大韩民国" in text or text in _KNOWN_HOMONYM_KEYS:
        return True
    return _AHLY_MARK.search(text) is not None


def detect_ambiguity(name: Any) -> str | None:
    """识别禁止自动映射的歧义标记。

    返回 gender、age_group、age_u22、reserve_team、same_name_other_team 之一。没有标记时返回空。
    性别包括「女」「女足」「女子」，括号中的 W，独立出现的 W，以及 Women、Ladies、Fem。
    年龄段包括任意两位数字的 U 年龄和 Sub 加两位年龄，也包括「青年」「预备」「梯队」。
    U22 的原因代码是 age_u22。其余年龄段的原因代码是 age_group。
    二队包括「B队」、独立出现的字母 B、Castilla、独立出现的罗马数字 II、名称结尾的数字 2、「二队」，以及 Reserves、Res、Youth。
    阿赫利、沙特阿赫利、韩国、大韩民国、Al Ahly 和 Al Ahli 即使库里目前只有一个标准名也不自动映射。
    """
    text = norm_key(name)
    if not text:
        return None
    if (
        "女" in text
        or _WOMEN_MARK.search(text)
        or _LADIES_MARK.search(text)
        or _FEM_MARK.search(text)
        or _PAREN_W.search(text)
        or _STANDALONE_W.search(text)
    ):
        return REASON_GENDER
    age = _age_reason(text)
    if age is not None:
        return age
    if (
        re.search(r"b\s*队", text)
        or _STANDALONE_B.search(text)
        or _CASTILLA_MARK.search(text)
        or _II_MARK.search(text)
        or text.endswith("2")
        or "二队" in text
        or _RESERVES_MARK.search(text)
        or _RES_MARK.search(text)
        or _YOUTH_MARK.search(text)
    ):
        return REASON_RESERVE
    if _is_known_homonym(text):
        return REASON_SAME_NAME
    return None


def _guard_connection_target(conn: sqlite3.Connection) -> None:
    """写入前检查连接上的数据库文件。

    内存库没有文件路径，因此不会要求备份确认。
    每个非空文件路径都交给 guard_write_target。写正式库时必须已经设置 FORMAL_DB_BACKUP_DONE=1。
    指向已归档研究副本的连接会被拒绝。
    """
    for row in conn.execute("PRAGMA database_list"):
        file_path = row[2]
        if file_path:
            guard_write_target(file_path, writing=True)


def schema_path() -> Path:
    """返回建表语句文件的路径。"""
    return _SCHEMA_PATH


def ensure_schema(conn: sqlite3.Connection) -> None:
    """在给定连接上建立四张新表。

    可以重复执行，不会删除已经写入的行。本函数不负责打开正式库。
    写入前会先经过 guard_write_target。正式库必须已经备份并设置 FORMAL_DB_BACKUP_DONE=1。
    """
    _guard_connection_target(conn)
    path = schema_path()
    try:
        script = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"无法读取待定池建表语句文件：{path}。") from exc
    for statement in split_sql(script):
        conn.execute(statement)


def pool_observe(
    conn: sqlite3.Connection,
    kind: str,
    raw_name: Any,
    source: str,
    source_ref: str,
    context: dict | None = None,
) -> dict:
    """上报一个队名或赛事名。

    同一个入口的同一个位置重复上报时，出现次数保持不变。
    命中等级 1 或等级 2 时写入别名和审计，并只回填来源位置指向的那一场的一个身份编号。
    未命中时写入待定池。含歧义标记、对应多个标准名、赛事不一致或已经撤销的名称都不会自动映射。
    上下文只保留赛前已经知道的信息。比分、赛果和赔率即使被传入也会丢弃，不参与判断，也不写入出现记录。
    真正写入之前会先经过 guard_write_target。名称为空时不写库，因此也不检查备份。
    """
    _require_kind(kind)
    source_text = _require_token(source, "入口名称")
    ref_text = _require_token(source_ref, "来源位置")
    observed = "" if raw_name is None else str(raw_name)
    normalized = norm_key(observed)
    loosened = loose_key(observed)
    if not normalized:
        return _result(
            action="skipped",
            kind=kind,
            raw_name=observed,
            norm_key="",
            loose_key="",
            message="名称为空，没有写入待定池。",
        )
    cleaned = _clean_context(context)
    _guard_connection_target(conn)

    def work() -> dict:
        _require_pool_tables(conn)
        repeated = _existing_mapping(conn, kind, source_text, ref_text)
        if repeated is not None:
            return _result_from_audit(conn, repeated, normalized, loosened, repeated=True)
        decision = _analyze(conn, kind, observed, normalized, loosened, cleaned)
        moment = now_beijing_text()
        if decision["action"] == MODE_AUTO:
            return _apply_auto(
                conn, decision, kind, observed, normalized, loosened, source_text, ref_text, moment
            )
        return _apply_pool(
            conn,
            decision,
            kind,
            observed,
            normalized,
            loosened,
            source_text,
            ref_text,
            cleaned,
            moment,
        )

    return _run_in_transaction(conn, work)


def revoke_mapping(conn: sqlite3.Connection, audit_id: int, actor: str) -> dict:
    """撤销一条映射审计。

    如果该次映射新插入了别名行，按编号删除那一行。原来就已经存在的别名不删除。
    只恢复该次映射实际改过的身份编号列，并且只有当前值仍等于映射写入的值时才恢复。
    某一列在映射之后已被改过时，跳过该列，并在撤销审计里用完整句子说明原因。
    随后写入一条 mode 为 revoke 的审计，把原记录指向它，并把规范化键写入禁止自动名单。
    名称已经在待定池里时，把该记录改为必须人工处理，原因是曾经撤销。
    名称是直接自动映射、从未进入待定池时，新建一条待定池记录，原因记为 auto_revoked，并写一条出现记录。
    此后同一规范化键，以及宽松键相同的写法，都不再自动映射。本函数不修改预测表。
    写入前会先经过 guard_write_target。
    """
    actor_text = _require_token(actor, "操作人")
    try:
        audit_number = int(audit_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("撤销时必须提供映射审计的整数编号。") from exc
    _guard_connection_target(conn)

    def work() -> dict:
        _require_pool_tables(conn)
        original = _fetchone_dict(conn, "SELECT * FROM alias_mapping_audit WHERE id = ?", (audit_number,))
        if original is None:
            raise ValueError(f"没有找到编号为 {audit_number} 的映射审计记录。")
        if original["mode"] == MODE_REVOKE:
            raise ValueError(f"编号为 {audit_number} 的记录本身是撤销记录，不能再对它执行撤销。")
        if original["revoked_by_audit_id"] is not None:
            raise ValueError(f"编号为 {audit_number} 的映射审计记录已经撤销，不能再次撤销。")
        return _apply_revoke(conn, original, actor_text, now_beijing_text())

    return _run_in_transaction(conn, work)


def _apply_revoke(conn: sqlite3.Connection, original: dict, actor: str, moment: str) -> dict:
    """在调用方已经打开的事务里完成撤销。"""
    kind = str(original["kind"])
    raw_name = str(original["raw_name"])
    normalized = norm_key(raw_name)
    mapped_after = _load_object(original["after_json"])
    mapped_before = _load_object(original["before_json"])
    entry_before = _fetchone_dict(
        conn,
        "SELECT * FROM alias_pool_entry WHERE kind = ? AND norm_key = ?",
        (kind, normalized),
    )
    alias_deleted = _delete_inserted_alias(conn, original)
    # 先记下撤销前场次上的真实编号。人工改过的值必须出现在审计的动作前快照里，不能沿用映射当时写入的值。
    live_before = _current_matches(conn, mapped_before)
    restored, skip_reasons = _restore_matches(conn, mapped_before, mapped_after)
    entry_after = entry_before
    pool_created = False
    if entry_before is not None:
        conn.execute(
            """
            UPDATE alias_pool_entry
               SET status = ?, manual_reason = ?, confirmed_id = NULL,
                   confirmed_by = NULL, confirmed_at = NULL, updated_at = ?
             WHERE id = ?
            """,
            (STATUS_AMBIGUOUS, REASON_REVOKED, moment, entry_before["id"]),
        )
        entry_after = _fetchone_dict(conn, "SELECT * FROM alias_pool_entry WHERE id = ?", (entry_before["id"],))
    else:
        entry_after = _insert_revoked_pool_entry(conn, original, kind, raw_name, normalized, moment)
        pool_created = True
    conn.execute(
        """
        INSERT OR IGNORE INTO alias_auto_block (kind, norm_key, reason, created_by, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (kind, normalized, REASON_REVOKED, actor, moment),
    )
    before_audit = {
        "pool_entry": _entry_snapshot(entry_before),
        "alias_row": mapped_after.get("alias_row"),
        "matches": live_before,
    }
    after_audit = {
        "pool_entry": _entry_snapshot(entry_after),
        "alias_deleted": alias_deleted,
        "matches": _current_matches(conn, mapped_before),
        "effective_at": moment,
        "skip_reason": "".join(skip_reasons) if skip_reasons else None,
        "pool_created": pool_created,
    }
    message = f"已撤销编号为 {original['id']} 的映射，并禁止这个规范化键再次自动映射。"
    if skip_reasons:
        message += "".join(skip_reasons)
    if pool_created:
        message += "这个名称此前没有进入待定池，撤销时已新建待定池记录，并写了一条出现记录。"
    cursor = conn.execute(
        """
        INSERT INTO alias_mapping_audit (
            mode, kind, raw_name, standard_id, standard_name, criterion, confidence,
            source, source_ref, pool_entry_id, alias_row_table, alias_row_id,
            before_json, after_json, actor, batch_id, revoked_by_audit_id, effective_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
        """,
        (
            MODE_REVOKE,
            kind,
            raw_name,
            original["standard_id"],
            original["standard_name"],
            CRITERION_REVOKE,
            CONFIDENCE_REVOKE,
            "revoke",
            f"audit.id={original['id']}",
            entry_after["id"] if entry_after is not None else original["pool_entry_id"],
            original["alias_row_table"] if alias_deleted else None,
            original["alias_row_id"] if alias_deleted else None,
            _dump(before_audit),
            _dump(after_audit),
            actor,
            moment,
            moment,
        ),
    )
    revoke_id = int(cursor.lastrowid)
    conn.execute(
        "UPDATE alias_mapping_audit SET revoked_by_audit_id = ? WHERE id = ?",
        (revoke_id, original["id"]),
    )
    return _result(
        action="revoked",
        kind=kind,
        raw_name=raw_name,
        norm_key=normalized,
        loose_key=loose_key(raw_name),
        entry_id=entry_after["id"] if entry_after is not None else None,
        status=STATUS_AMBIGUOUS if entry_after is not None else None,
        seen_count=entry_after["seen_count"] if entry_after is not None else None,
        manual_reason=REASON_REVOKED if entry_after is not None else None,
        audit_id=revoke_id,
        criterion=CRITERION_REVOKE,
        confidence=CONFIDENCE_REVOKE,
        standard_id=original["standard_id"],
        standard_name=original["standard_name"],
        match_updated=restored > 0,
        message=message,
        revoked_audit_id=int(original["id"]),
        alias_deleted=alias_deleted,
        matches_restored=restored,
        skip_reasons=skip_reasons,
        pool_created=pool_created,
    )


def _analyze(
    conn: sqlite3.Connection,
    kind: str,
    raw_name: str,
    normalized: str,
    loosened: str,
    context: dict,
) -> dict:
    """决定这次上报是自动映射还是进入待定池，并准备候选提示。"""
    standards, aliases = _load_catalog(conn, kind)
    text_reason = detect_ambiguity(raw_name)
    if text_reason is not None:
        candidate, basis = _single_candidate(standards, aliases, normalized, loosened)
        return _manual_decision(text_reason, candidate, basis=basis)
    if _is_blocked(conn, kind, normalized, loosened):
        candidate, basis = _single_candidate(standards, aliases, normalized, loosened)
        return _manual_decision(REASON_REVOKED, candidate, basis=basis)

    norm_hits = [row for row in standards + aliases if row["norm"] == normalized]
    standard_ids = _unique_ids(norm_hits)
    if len(standard_ids) > 1:
        return _manual_decision(
            REASON_SAME_NAME,
            None,
            reason_text=_multi_sentence(norm_hits, "规范化键"),
        )
    if len(standard_ids) == 1:
        marker = _marker_on_hits(norm_hits)
        target = _standard_by_id(standards, standard_ids[0])
        if marker is not None or target is None:
            return _manual_decision(marker or REASON_SAME_NAME, target, basis=BASIS_LOOSE)
        return _auto_decision(CRITERION_KNOWN_ALIAS, CONFIDENCE_LEVEL1, target)

    canonical_hits = [row for row in standards if row["loose"] == loosened]
    alias_hits = [row for row in aliases if row["loose"] == loosened]
    if len(canonical_hits) > 1:
        return _manual_decision(REASON_MULTI, None, reason_text=_multi_sentence(canonical_hits, "宽松键"))
    if len(canonical_hits) == 1:
        target = canonical_hits[0]
        other_alias_ids = {row["standard_id"] for row in alias_hits if row["standard_id"] != target["standard_id"]}
        if other_alias_ids:
            return _manual_decision(
                REASON_MULTI,
                None,
                reason_text=_multi_sentence(canonical_hits + alias_hits, "宽松键"),
            )
        marker = _marker_on_hits([target, *alias_hits])
        if marker is not None:
            return _manual_decision(marker, target, basis=BASIS_LOOSE)
        competition_name = context.get("competition_name")
        if kind == KIND_TEAM and _competition_conflicts(conn, int(target["standard_id"]), competition_name):
            sentence = (
                f"宽松键与标准名「{target['standard_name']}」相同，但该球队已经出现过的赛事与本次赛事"
                f"「{competition_name}」没有交集，因此不自动映射。"
            )
            return _manual_decision(None, target, reason_text=sentence, basis=BASIS_LOOSE, ambiguous=False)
        return _auto_decision(CRITERION_LOOSE_KEY, CONFIDENCE_LEVEL2, target)

    unique_alias_ids = _unique_ids(alias_hits)
    if len(unique_alias_ids) > 1:
        return _manual_decision(REASON_MULTI, None, reason_text=_multi_sentence(alias_hits, "宽松键"))
    if len(unique_alias_ids) == 1:
        target = _standard_by_id(standards, unique_alias_ids[0])
        if target is None:
            sentence = "宽松键与已有别名相同，但没有找到对应的标准名，因此不自动映射。"
            return _manual_decision(None, None, reason_text=sentence, ambiguous=False)
        marker = _marker_on_hits([target, *alias_hits])
        if marker is not None:
            return _manual_decision(marker, target, basis=BASIS_ALIAS)
        competition_name = context.get("competition_name")
        if kind == KIND_TEAM and _competition_conflicts(conn, int(target["standard_id"]), competition_name):
            sentence = (
                f"宽松键与已有别名相同，该别名只指向标准名「{target['standard_name']}」，"
                f"但该球队已经出现过的赛事与本次赛事「{competition_name}」没有交集，因此不自动映射。"
            )
            return _manual_decision(None, target, reason_text=sentence, basis=BASIS_ALIAS, ambiguous=False)
        return _auto_decision(
            CRITERION_LOOSE_KEY,
            CONFIDENCE_LEVEL2,
            target,
            detail="判据是宽松键与已有别名相同，并且该别名只指向这一个标准名。",
        )

    affix = _affix_candidate(standards, loosened)
    if affix is not None:
        # 歧义只走 detect_ambiguity。任一侧带标记，都不把前后缀剥离记成猜挂。
        marker = detect_ambiguity(raw_name) or detect_ambiguity(affix.get("display_name"))
        if marker is not None:
            return _manual_decision(marker, None)
        if not _same_match_context(context):
            sentence = (
                f"去掉常见前缀或结尾的「队」之后与标准名「{affix['standard_name']}」相同。"
                "没有同场上下文，弱模糊不能并组，也不记下猜挂候选。"
            )
            return _manual_decision(None, None, reason_text=sentence, ambiguous=False)
        sentence = (
            f"去掉常见前缀或结尾的「队」之后与标准名「{affix['standard_name']}」相同。"
            "同场已有对手映射和开赛时刻，这仍只是候选提示，前后缀不同不能自动映射。"
        )
        return _manual_decision(None, affix, reason_text=sentence, basis=BASIS_AFFIX, ambiguous=False)
    return _manual_decision(None, None, ambiguous=False)


def _apply_auto(
    conn: sqlite3.Connection,
    decision: dict,
    kind: str,
    raw_name: str,
    normalized: str,
    loosened: str,
    source: str,
    source_ref: str,
    moment: str,
) -> dict:
    """写入一条自动映射。

    先保存动作前的池记录和别名，再回填当前场次的身份编号，再插入尚不存在的别名，最后写审计。
    别名行编号只在本次确实插入时写入审计，撤销时才按该编号删除。
    """
    target_id = int(decision["standard_id"])
    target_name = str(decision["standard_name"])
    entry_before = _fetchone_dict(
        conn,
        "SELECT * FROM alias_pool_entry WHERE kind = ? AND norm_key = ?",
        (kind, normalized),
    )
    alias_text = str(raw_name).strip()
    existing_alias = _fetch_alias(conn, kind, alias_text)
    if existing_alias is not None and int(existing_alias["standard_id"]) != target_id:
        conflict = _manual_decision(
            REASON_SAME_NAME,
            None,
            reason_text="这个原始写法已经是另一支标准名的别名，不能自动改指向。",
        )
        return _apply_pool(conn, conflict, kind, raw_name, normalized, loosened, source, source_ref, {}, moment)

    before_match, after_match, match_updated = _backfill_match(conn, kind, target_id, source_ref)
    alias_inserted = False
    alias_row = existing_alias
    if existing_alias is None and alias_text:
        if not _table_exists(conn, _ALIAS_TABLE[kind]):
            raise RuntimeError("自动映射需要写入别名表，但该表不存在。")
        cursor = conn.execute(
            f"INSERT INTO {_ALIAS_TABLE[kind]} (alias, {_STANDARD_ID_COLUMN[kind]}, source) VALUES (?, ?, ?)",
            (alias_text, target_id, f"{MODE_AUTO}:{decision['criterion']}"),
        )
        alias_inserted = True
        alias_row = {
            "id": int(cursor.lastrowid),
            "alias": alias_text,
            "standard_id": target_id,
            "source": f"{MODE_AUTO}:{decision['criterion']}",
        }
    entry_after = entry_before
    if entry_before is not None:
        conn.execute(
            """
            UPDATE alias_pool_entry
               SET status = ?, confirmed_id = ?, confirmed_by = ?, confirmed_at = ?, updated_at = ?
             WHERE id = ?
            """,
            (STATUS_CONFIRMED, target_id, MODE_AUTO, moment, moment, entry_before["id"]),
        )
        entry_after = _fetchone_dict(conn, "SELECT * FROM alias_pool_entry WHERE id = ?", (entry_before["id"],))
    before_payload = {
        "pool_entry": _entry_snapshot(entry_before),
        "alias_existed": existing_alias is not None,
        "alias_row": _alias_snapshot(existing_alias),
        "matches": [before_match] if before_match is not None else [],
    }
    after_payload = {
        "pool_entry": _entry_snapshot(entry_after),
        "alias_inserted": alias_inserted,
        "alias_row": _alias_snapshot(alias_row) if alias_inserted else None,
        "matches": [after_match] if after_match is not None else [],
        "effective_at": moment,
    }
    audit_cursor = conn.execute(
        """
        INSERT INTO alias_mapping_audit (
            mode, kind, raw_name, standard_id, standard_name, criterion, confidence,
            source, source_ref, pool_entry_id, alias_row_table, alias_row_id,
            before_json, after_json, actor, batch_id, revoked_by_audit_id, effective_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
        """,
        (
            MODE_AUTO,
            kind,
            raw_name,
            target_id,
            target_name,
            decision["criterion"],
            decision["confidence"],
            source,
            source_ref,
            entry_after["id"] if entry_after is not None else None,
            _ALIAS_TABLE[kind] if alias_inserted else None,
            int(alias_row["id"]) if alias_inserted and alias_row is not None else None,
            _dump(before_payload),
            _dump(after_payload),
            MODE_AUTO,
            moment,
            moment,
        ),
    )
    return _result(
        action=MODE_AUTO,
        kind=kind,
        raw_name=raw_name,
        norm_key=normalized,
        loose_key=loosened,
        entry_id=entry_after["id"] if entry_after is not None else None,
        status=STATUS_CONFIRMED if entry_after is not None else None,
        seen_count=entry_after["seen_count"] if entry_after is not None else None,
        audit_id=int(audit_cursor.lastrowid),
        criterion=decision["criterion"],
        confidence=decision["confidence"],
        standard_id=target_id,
        standard_name=target_name,
        alias_inserted=alias_inserted,
        match_updated=match_updated,
        message=f"已自动映射到标准名「{target_name}」。{decision.get('detail') or _CRITERION_SENTENCE[decision['criterion']]}",
    )


def _apply_pool(
    conn: sqlite3.Connection,
    decision: dict,
    kind: str,
    raw_name: str,
    normalized: str,
    loosened: str,
    source: str,
    source_ref: str,
    context: dict,
    moment: str,
) -> dict:
    """写入或复用待定池主表，并按来源位置保证出现次数不因重复上报而增加。"""
    entry = _fetchone_dict(
        conn,
        "SELECT * FROM alias_pool_entry WHERE kind = ? AND norm_key = ?",
        (kind, normalized),
    )
    if entry is None:
        cursor = conn.execute(
            """
            INSERT INTO alias_pool_entry (
                kind, raw_name, norm_key, loose_key, scope_hint, first_source, sources_json,
                first_source_ref, seen_count, first_seen_at, last_seen_at, candidate_kind_id,
                candidate_name, candidate_reason, candidate_basis, status, manual_reason, extras,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)
            """,
            (
                kind,
                raw_name,
                normalized,
                loosened,
                _scope_hint(kind, context),
                source,
                _dump_list([source]),
                source_ref,
                moment,
                moment,
                decision.get("candidate_id"),
                decision.get("candidate_name"),
                decision.get("candidate_reason"),
                decision.get("candidate_basis"),
                decision["status"],
                decision.get("manual_reason"),
                moment,
                moment,
            ),
        )
        entry_id = int(cursor.lastrowid)
    else:
        entry_id = int(entry["id"])
    count_before = int(
        conn.execute(
            "SELECT COUNT(*) FROM alias_pool_sighting WHERE entry_id = ?",
            (entry_id,),
        ).fetchone()[0]
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO alias_pool_sighting (entry_id, source, source_ref, context_json, seen_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (entry_id, source, source_ref, _dump(context), moment),
    )
    count_after = int(
        conn.execute(
            "SELECT COUNT(*) FROM alias_pool_sighting WHERE entry_id = ?",
            (entry_id,),
        ).fetchone()[0]
    )
    sighting_inserted = count_after > count_before
    if sighting_inserted:
        _refresh_entry(conn, entry_id, decision, kind, source, context, moment)
    entry = _fetchone_dict(conn, "SELECT * FROM alias_pool_entry WHERE id = ?", (entry_id,))
    if not sighting_inserted:
        message = "同一个来源位置已经上报过，出现次数没有增加。"
    elif entry["status"] == STATUS_AMBIGUOUS:
        message = "已进入待定池，并且需要人工处理。"
    else:
        message = "已进入待定池。"
    return _result(
        action="pooled",
        repeated=not sighting_inserted,
        kind=kind,
        raw_name=entry["raw_name"],
        norm_key=normalized,
        loose_key=loosened,
        entry_id=entry_id,
        status=entry["status"],
        seen_count=entry["seen_count"],
        manual_reason=entry["manual_reason"],
        candidate_id=entry["candidate_kind_id"],
        candidate_name=entry["candidate_name"],
        candidate_reason=entry["candidate_reason"],
        candidate_basis=entry["candidate_basis"],
        sighting_inserted=sighting_inserted,
        message=message,
    )


def _refresh_entry(
    conn: sqlite3.Connection,
    entry_id: int,
    decision: dict,
    kind: str,
    source: str,
    context: dict,
    moment: str,
) -> None:
    """新增出现记录后，按出现记录表的行数回写次数，并刷新最近时间和候选提示。"""
    current = _fetchone_dict(conn, "SELECT * FROM alias_pool_entry WHERE id = ?", (entry_id,))
    count = conn.execute(
        "SELECT COUNT(*) FROM alias_pool_sighting WHERE entry_id = ?",
        (entry_id,),
    ).fetchone()[0]
    sources = json.loads(current["sources_json"])
    if source not in sources:
        sources.append(source)
    status, manual_reason = _merge_status(
        str(current["status"]),
        current["manual_reason"],
        str(decision["status"]),
        decision.get("manual_reason"),
    )
    conn.execute(
        """
        UPDATE alias_pool_entry
           SET seen_count = ?, last_seen_at = ?, updated_at = ?, sources_json = ?, scope_hint = ?,
               candidate_kind_id = ?, candidate_name = ?, candidate_reason = ?, candidate_basis = ?,
               status = ?, manual_reason = ?
         WHERE id = ?
        """,
        (
            int(count),
            moment,
            moment,
            _dump_list(list(sources)),
            _scope_hint(kind, context) or current["scope_hint"],
            decision.get("candidate_id"),
            decision.get("candidate_name"),
            decision.get("candidate_reason"),
            decision.get("candidate_basis"),
            status,
            manual_reason,
            entry_id,
        ),
    )


def _merge_status(
    old_status: str,
    old_reason: str | None,
    new_status: str,
    new_reason: str | None,
) -> tuple[str, str | None]:
    """合并新旧状态。已经确认的记录不被一次普通入池改回待处理；新的歧义或撤销原因可以覆盖旧状态。"""
    if new_status == STATUS_AMBIGUOUS:
        return STATUS_AMBIGUOUS, new_reason
    if old_status == STATUS_CONFIRMED:
        return old_status, old_reason
    if old_status == STATUS_AMBIGUOUS:
        return old_status, old_reason or new_reason
    return new_status, new_reason


def _auto_decision(criterion: str, confidence: str, target: dict, detail: str | None = None) -> dict:
    """构造一条允许自动映射的决定。detail 用来换成更具体的判据说明。"""
    return {
        "action": MODE_AUTO,
        "criterion": criterion,
        "confidence": confidence,
        "standard_id": int(target["standard_id"]),
        "standard_name": target["standard_name"],
        "status": STATUS_CONFIRMED,
        "manual_reason": None,
        "candidate_id": int(target["standard_id"]),
        "candidate_name": target["standard_name"],
        "candidate_reason": None,
        "candidate_basis": None,
        "detail": detail,
    }


def _manual_decision(
    manual_reason: str | None,
    candidate: dict | None,
    *,
    reason_text: str | None = None,
    basis: str | None = None,
    ambiguous: bool | None = None,
) -> dict:
    """构造一条进入待定池的决定。候选字段只作提示，不表示已经映射。"""
    if ambiguous is None:
        ambiguous = manual_reason is not None
    sentence = reason_text
    if sentence is None and manual_reason is not None:
        sentence = _REASON_SENTENCE[manual_reason]
        if candidate is not None:
            sentence += f"名称与标准名「{candidate['standard_name']}」相近，这里只作为候选提示，没有写入别名。"
    return {
        "action": "pool",
        "criterion": None,
        "confidence": None,
        "standard_id": None,
        "standard_name": None,
        "status": STATUS_AMBIGUOUS if ambiguous else STATUS_PENDING,
        "manual_reason": manual_reason if ambiguous else None,
        "candidate_id": int(candidate["standard_id"]) if candidate is not None else None,
        "candidate_name": candidate["standard_name"] if candidate is not None else None,
        "candidate_reason": sentence,
        "candidate_basis": basis if candidate is not None else None,
    }


def _load_catalog(conn: sqlite3.Connection, kind: str) -> tuple[list[dict], list[dict]]:
    """读取标准名和别名，并预先算好规范化键与宽松键。标准名表不存在时视为没有任何已知名称。"""
    standards: list[dict] = []
    standard_table = _STANDARD_TABLE[kind]
    if _table_exists(conn, standard_table):
        for row in _fetchall_dicts(conn, f"SELECT id, name_zh_canonical FROM {standard_table}"):
            name = "" if row["name_zh_canonical"] is None else str(row["name_zh_canonical"])
            standards.append(_catalog_row(int(row["id"]), name, None, None, None))
    aliases: list[dict] = []
    alias_table = _ALIAS_TABLE[kind]
    id_column = _STANDARD_ID_COLUMN[kind]
    if _table_exists(conn, alias_table) and _table_exists(conn, standard_table):
        rows = _fetchall_dicts(
            conn,
            f"""
            SELECT alias_row.id AS alias_id, alias_row.alias AS alias, alias_row.source AS source,
                   alias_row.{id_column} AS standard_id, standard_row.name_zh_canonical AS standard_name
              FROM {alias_table} AS alias_row
              JOIN {standard_table} AS standard_row ON standard_row.id = alias_row.{id_column}
            """,
        )
        for row in rows:
            aliases.append(
                _catalog_row(
                    int(row["standard_id"]),
                    "" if row["standard_name"] is None else str(row["standard_name"]),
                    int(row["alias_id"]),
                    "" if row["alias"] is None else str(row["alias"]),
                    None if row["source"] is None else str(row["source"]),
                )
            )
    return standards, aliases


def _catalog_row(
    standard_id: int,
    standard_name: str,
    alias_id: int | None,
    alias_text: str | None,
    alias_source: str | None,
) -> dict:
    """把标准名或别名整理成同一结构。标记检测使用这一行自己的文字，而不是标准名。"""
    display_name = standard_name if alias_text is None else alias_text
    return {
        "standard_id": standard_id,
        "standard_name": standard_name,
        "norm": norm_key(display_name),
        "loose": loose_key(display_name),
        "alias_id": alias_id,
        "alias_text": alias_text,
        "alias_source": alias_source,
        "display_name": display_name,
    }


def _single_candidate(
    standards: list[dict],
    aliases: list[dict],
    normalized: str,
    loosened: str,
) -> tuple[dict | None, str | None]:
    """在不自动映射时，找一个可以展示给人工的候选。对应多个标准名时不挑选其中任何一个。"""
    groups = (
        (BASIS_ALIAS, [row for row in aliases if row["norm"] == normalized]),
        (BASIS_LOOSE, [row for row in standards if row["norm"] == normalized]),
        (BASIS_ALIAS, [row for row in aliases if row["loose"] == loosened]),
        (BASIS_LOOSE, [row for row in standards if row["loose"] == loosened]),
    )
    for basis, rows in groups:
        identifiers = _unique_ids(rows)
        if len(identifiers) > 1:
            return None, None
        if len(identifiers) == 1:
            target = _standard_by_id(standards, identifiers[0])
            if target is not None:
                return target, basis
    return None, None


def _is_blocked(conn: sqlite3.Connection, kind: str, normalized: str, loosened: str) -> bool:
    """判断该写法是否已经因为撤销而被禁止自动映射。宽松键相同的写法一并禁止，避免靠增删标点绕过。"""
    rows = _fetchall_dicts(conn, "SELECT norm_key FROM alias_auto_block WHERE kind = ?", (kind,))
    for row in rows:
        blocked = "" if row["norm_key"] is None else str(row["norm_key"])
        if blocked == normalized or loose_key(blocked) == loosened:
            return True
    return False


def _marker_on_hits(rows: list[dict]) -> str | None:
    """检查匹配到的已知名称是否带有歧义标记。任意一边有标记都不自动映射。"""
    for row in rows:
        marker = detect_ambiguity(row["display_name"])
        if marker is not None:
            return marker
    return None


def _competition_conflicts(conn: sqlite3.Connection, team_id: int, competition_name: str | None) -> bool:
    """赛事信息缺失时不阻止自动映射。球队还没有任何赛事记录时也不阻止。已有赛事与本次完全没有交集时阻止。"""
    if not competition_name or not norm_key(competition_name):
        return False
    if not _table_exists(conn, "matches"):
        return False
    columns = _column_names(conn, "matches")
    required = {"home_team_id", "away_team_id", "competition_name"}
    if not required.issubset(columns):
        return False
    rows = _fetchall_dicts(
        conn,
        """
        SELECT competition_name FROM matches
         WHERE home_team_id = ? OR away_team_id = ?
        """,
        (team_id, team_id),
    )
    existing = {norm_key(row["competition_name"]) for row in rows if norm_key(row["competition_name"])}
    if not existing:
        return False
    return norm_key(competition_name) not in existing


def _same_match_context(context: dict | None) -> bool:
    """同场上下文要同时具备开赛时刻，以及对手已经映射。

    只有赛事名、竞彩日或国家，不算同场多证据。比分不进入上下文，因此不能单独充数。
    缺少这份上下文时，整词前后缀剥离不得留下猜挂候选，也不得并组。
    """
    if not context:
        return False
    kickoff = context.get("kickoff_at")
    if kickoff is None or str(kickoff).strip() == "":
        return False
    return bool(context.get("opponent_mapped"))


def _affix_candidate(standards: list[dict], loosened: str) -> dict | None:
    """前后缀不同只生成候选，不自动映射。去掉的只限于常见俱乐部前缀和结尾的「队」。

    调用方在没有同场上下文时不得采用这份候选。结尾的 FC、CF 等俱乐部后缀不在这里剥离。
    """
    variants: list[str] = []
    for prefix in _AFFIX_PREFIXES:
        if loosened.startswith(prefix) and len(loosened) > len(prefix) + 1:
            variants.append(loosened[len(prefix) :])
    extended = list(variants)
    for item in [loosened, *variants]:
        if item.endswith("队") and len(item) > 2:
            extended.append(item[:-1])
    keys = [item for item in extended if item and item != loosened]
    matched: list[dict] = []
    seen: set[int] = set()
    for row in standards:
        if row["loose"] in keys and row["standard_id"] not in seen:
            matched.append(row)
            seen.add(int(row["standard_id"]))
    if len(matched) == 1:
        return matched[0]
    return None


def _backfill_match(
    conn: sqlite3.Connection,
    kind: str,
    standard_id: int,
    source_ref: str,
) -> tuple[dict | None, dict | None, bool]:
    """只回填来源位置指向的那一场的一个身份编号。编号已经有值时不覆盖。无法识别位置时不修改场次。"""
    parsed = _parse_source_ref(source_ref)
    if parsed is None or not _table_exists(conn, "matches"):
        return None, None, False
    match_id, slot = parsed
    expected_kind, column = _MATCH_SLOT[slot]
    if expected_kind != kind:
        return None, None, False
    columns = _column_names(conn, "matches")
    if not {"id", "home_team_id", "away_team_id", "league_id"}.issubset(columns):
        return None, None, False
    current = _fetchone_dict(
        conn,
        "SELECT id, home_team_id, away_team_id, league_id FROM matches WHERE id = ?",
        (match_id,),
    )
    if current is None or current[column] is not None:
        return None, None, False
    before = _match_snapshot(current)
    conn.execute(
        f"UPDATE matches SET {column} = ? WHERE id = ? AND {column} IS NULL",
        (standard_id, match_id),
    )
    updated = _fetchone_dict(
        conn,
        "SELECT id, home_team_id, away_team_id, league_id FROM matches WHERE id = ?",
        (match_id,),
    )
    after = _match_snapshot(updated) if updated is not None else None
    changed = after is not None and before[column] != after[column]
    if not changed:
        return None, None, False
    return before, after, True


def _format_identity(value: int | None) -> str:
    """把身份编号写成审计句子里的文字。空编号写成「空值」。"""
    if value is None:
        return "空值"
    return str(value)


def _insert_revoked_pool_entry(
    conn: sqlite3.Connection,
    original: dict,
    kind: str,
    raw_name: str,
    normalized: str,
    moment: str,
) -> dict:
    """为从未进入待定池的自动映射补一条池记录和一条出现记录。

    出现记录使用原来的入口和来源位置，便于对照是哪一次上报被撤销。
    原因记为 auto_revoked。禁止自动名单由调用方另行写入，这里不能只写名单。
    """
    source = str(original["source"]).strip() if original["source"] else "revoke"
    source_ref = str(original["source_ref"]).strip() if original["source_ref"] else f"audit.id={original['id']}"
    standard_name = "" if original["standard_name"] is None else str(original["standard_name"])
    sentence = (
        f"这个名称曾被自动映射到标准名「{standard_name}」，撤销后进入待定池。"
        "它必须由人工处理，不能再次自动映射。"
    )
    basis = None
    if original["criterion"] == CRITERION_KNOWN_ALIAS:
        basis = BASIS_ALIAS
    elif original["criterion"] == CRITERION_LOOSE_KEY:
        basis = BASIS_LOOSE
    cursor = conn.execute(
        """
        INSERT INTO alias_pool_entry (
            kind, raw_name, norm_key, loose_key, scope_hint, first_source, sources_json,
            first_source_ref, seen_count, first_seen_at, last_seen_at, candidate_kind_id,
            candidate_name, candidate_reason, candidate_basis, status, manual_reason, extras,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)
        """,
        (
            kind,
            raw_name,
            normalized,
            loose_key(raw_name),
            source,
            _dump_list([source]),
            source_ref,
            moment,
            moment,
            original["standard_id"],
            standard_name,
            sentence,
            basis,
            STATUS_AMBIGUOUS,
            REASON_REVOKED,
            moment,
            moment,
        ),
    )
    entry_id = int(cursor.lastrowid)
    conn.execute(
        """
        INSERT INTO alias_pool_sighting (entry_id, source, source_ref, context_json, seen_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (entry_id, source, source_ref, _dump({}), moment),
    )
    conn.execute(
        "UPDATE alias_pool_entry SET seen_count = 1, updated_at = ? WHERE id = ?",
        (moment, entry_id),
    )
    entry = _fetchone_dict(conn, "SELECT * FROM alias_pool_entry WHERE id = ?", (entry_id,))
    if entry is None:
        raise RuntimeError("撤销后新建的待定池记录没有写成功。")
    return entry


def _restore_matches(conn: sqlite3.Connection, before_payload: dict, after_payload: dict) -> tuple[int, list[str]]:
    """只恢复本次映射真正改过、且当前值仍等于写入值的那一列。

    映射没有改过的列保持原样，即使后来被别人改过，也不改回去。
    当前值已经不同于映射写入的值时，跳过该列，并返回一句完整的跳过原因。
    返回值是实际恢复过的场次数，以及跳过原因列表。
    """
    if not _table_exists(conn, "matches"):
        return 0, []
    before_rows = {row["id"]: row for row in _matches_from_payload(before_payload)}
    after_rows = {row["id"]: row for row in _matches_from_payload(after_payload)}
    restored = 0
    skips: list[str] = []
    for match_id, before_row in before_rows.items():
        after_row = after_rows.get(match_id)
        if after_row is None:
            continue
        current_found = _fetchone_dict(
            conn,
            "SELECT id, home_team_id, away_team_id, league_id FROM matches WHERE id = ?",
            (match_id,),
        )
        if current_found is None:
            continue
        current = _match_snapshot(current_found)
        assignments: list[str] = []
        params: list[int | None] = []
        for column in _IDENTITY_COLUMNS:
            if before_row[column] == after_row[column]:
                continue
            if current[column] == after_row[column]:
                assignments.append(f"{column} = ?")
                params.append(before_row[column])
                continue
            label = _IDENTITY_LABEL[column]
            skips.append(
                f"场次 {match_id} 的{label}当前是{_format_identity(current[column])}，"
                f"与本次映射写入的{_format_identity(after_row[column])}不同，"
                "说明该编号在映射之后已被改过，撤销时跳过这一列，没有恢复。"
            )
        if not assignments:
            continue
        params.append(match_id)
        conn.execute(
            f"UPDATE matches SET {', '.join(assignments)} WHERE id = ?",
            tuple(params),
        )
        restored += 1
    return restored, skips


def _current_matches(conn: sqlite3.Connection, payload: dict) -> list[dict]:
    """读取快照里那些场次现在的三个身份编号，供撤销后的审计使用。"""
    if not _table_exists(conn, "matches"):
        return []
    current: list[dict] = []
    for row in _matches_from_payload(payload):
        found = _fetchone_dict(
            conn,
            "SELECT id, home_team_id, away_team_id, league_id FROM matches WHERE id = ?",
            (row["id"],),
        )
        if found is not None:
            current.append(_match_snapshot(found))
    return current


def _delete_inserted_alias(conn: sqlite3.Connection, original: dict) -> bool:
    """只删除本次映射新插入的别名行。编号已经挪作他用时不删除。"""
    table = original["alias_row_table"]
    alias_id = original["alias_row_id"]
    if table not in ("team_aliases", "league_aliases") or alias_id is None:
        return False
    if not _table_exists(conn, str(table)):
        return False
    id_column = "team_id" if table == "team_aliases" else "league_id"
    row = _fetchone_dict(
        conn,
        f"SELECT id, {id_column} AS standard_id FROM {table} WHERE id = ?",
        (int(alias_id),),
    )
    if row is None or int(row["standard_id"]) != int(original["standard_id"]):
        return False
    conn.execute(f"DELETE FROM {table} WHERE id = ?", (int(alias_id),))
    return True


def _existing_mapping(conn: sqlite3.Connection, kind: str, source: str, source_ref: str) -> dict | None:
    """同一个来源位置已经有未撤销的映射时，不再写第二条审计。"""
    return _fetchone_dict(
        conn,
        """
        SELECT * FROM alias_mapping_audit
         WHERE kind = ? AND source = ? AND source_ref = ?
           AND mode IN ('auto', 'manual')
           AND revoked_by_audit_id IS NULL
         ORDER BY id DESC
         LIMIT 1
        """,
        (kind, source, source_ref),
    )


def _result_from_audit(conn: sqlite3.Connection, audit: dict, normalized: str, loosened: str, repeated: bool) -> dict:
    """把已经存在的审计整理成上报结果，不再写入。"""
    entry = None
    if audit["pool_entry_id"] is not None:
        entry = _fetchone_dict(conn, "SELECT * FROM alias_pool_entry WHERE id = ?", (audit["pool_entry_id"],))
    if entry is None:
        entry = _fetchone_dict(
            conn,
            "SELECT * FROM alias_pool_entry WHERE kind = ? AND norm_key = ?",
            (audit["kind"], normalized),
        )
    return _result(
        action=str(audit["mode"]),
        repeated=repeated,
        kind=str(audit["kind"]),
        raw_name=str(audit["raw_name"]),
        norm_key=normalized,
        loose_key=loosened,
        entry_id=entry["id"] if entry is not None else None,
        status=entry["status"] if entry is not None else None,
        seen_count=entry["seen_count"] if entry is not None else None,
        manual_reason=entry["manual_reason"] if entry is not None else None,
        audit_id=int(audit["id"]),
        criterion=audit["criterion"],
        confidence=audit["confidence"],
        standard_id=audit["standard_id"],
        standard_name=audit["standard_name"],
        message="同一个来源位置已经映射过，没有再次写入。",
    )


def _parse_source_ref(source_ref: str) -> tuple[int, str] | None:
    """从来源位置中识别场次编号和要回填的列。无法识别时返回空。"""
    for pattern in _SOURCE_REF_PATTERNS:
        found = pattern.search(source_ref)
        if found is not None:
            return int(found.group(1)), found.group(2)
    return None


def _fetch_alias(conn: sqlite3.Connection, kind: str, alias_text: str) -> dict | None:
    """按原始写法精确查找别名行。找不到或别名表不存在时返回空。"""
    table = _ALIAS_TABLE[kind]
    if not alias_text or not _table_exists(conn, table):
        return None
    id_column = _STANDARD_ID_COLUMN[kind]
    row = _fetchone_dict(
        conn,
        f"SELECT id, alias, {id_column} AS standard_id, source FROM {table} WHERE alias = ?",
        (alias_text,),
    )
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "alias": str(row["alias"]),
        "standard_id": int(row["standard_id"]),
        "source": None if row["source"] is None else str(row["source"]),
    }


def _entry_snapshot(entry: dict | None) -> dict | None:
    """摘出撤销和审计需要对照的池字段。"""
    if entry is None:
        return None
    return {
        "id": int(entry["id"]),
        "status": entry["status"],
        "manual_reason": entry["manual_reason"],
        "confirmed_id": _int_or_none(entry["confirmed_id"]),
        "confirmed_by": entry["confirmed_by"],
        "confirmed_at": entry["confirmed_at"],
        "norm_key": entry["norm_key"],
        "seen_count": int(entry["seen_count"]),
    }


def _alias_snapshot(alias_row: dict | None) -> dict | None:
    """摘出别名行里用于审计的字段。"""
    if alias_row is None:
        return None
    return {
        "id": int(alias_row["id"]),
        "alias": alias_row["alias"],
        "standard_id": int(alias_row["standard_id"]),
        "source": alias_row.get("source"),
    }


def _match_snapshot(row: dict) -> dict:
    """摘出场次的三个身份编号。"""
    return {
        "id": int(row["id"]),
        "home_team_id": _int_or_none(row["home_team_id"]),
        "away_team_id": _int_or_none(row["away_team_id"]),
        "league_id": _int_or_none(row["league_id"]),
    }


def _matches_from_payload(payload: dict) -> list[dict]:
    """从审计快照中取出场次列表。格式不对时返回空列表，避免把无法理解的文本写成场次更新。"""
    rows = payload.get("matches")
    if not isinstance(rows, list):
        return []
    cleaned: list[dict] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("id") is None:
            continue
        cleaned.append(
            {
                "id": int(row["id"]),
                "home_team_id": _int_or_none(row.get("home_team_id")),
                "away_team_id": _int_or_none(row.get("away_team_id")),
                "league_id": _int_or_none(row.get("league_id")),
            }
        )
    return cleaned


def _unique_ids(rows: list[dict]) -> list[int]:
    """按出现顺序收集不重复的标准名编号。"""
    found: list[int] = []
    for row in rows:
        identifier = int(row["standard_id"])
        if identifier not in found:
            found.append(identifier)
    return found


def _standard_by_id(standards: list[dict], standard_id: int) -> dict | None:
    """在标准名列表中按编号查找。"""
    for row in standards:
        if int(row["standard_id"]) == int(standard_id):
            return row
    return None


def _multi_sentence(rows: list[dict], label: str) -> str:
    """把多个标准名写成一句候选理由。"""
    names: list[str] = []
    for row in rows:
        name = str(row["standard_name"])
        if name not in names:
            names.append(name)
    quoted = "、".join(f"「{name}」" for name in names)
    return f"{label}同时对应标准名{quoted}，候选不止一个，因此不自动映射。"


def _scope_hint(kind: str, context: dict) -> str | None:
    """球队记录赛事名，赛事记录国家或地区。没有这些信息时返回空。"""
    if kind == KIND_TEAM:
        return context.get("competition_name")
    return context.get("region") or context.get("country")


def _clean_context(context: dict | None) -> dict:
    """只保留赛前已经知道的字段。其他字段直接丢弃，不写入出现记录。"""
    if context is None:
        return {}
    if not isinstance(context, dict):
        raise ValueError("上报时的上下文必须是字典。比分、赛果和赔率不会被保存。")
    cleaned: dict[str, Any] = {}
    for key in _CONTEXT_KEYS:
        if key not in context or context[key] is None:
            continue
        if key == "opponent_mapped":
            cleaned[key] = bool(context[key])
            continue
        text = str(context[key]).strip()
        if text:
            cleaned[key] = text
    return cleaned


def _result(**kwargs: Any) -> dict:
    """补齐上报结果里的固定字段，方便调用方按同一套键读取。"""
    payload = {
        "action": None,
        "repeated": False,
        "kind": None,
        "raw_name": None,
        "norm_key": None,
        "loose_key": None,
        "entry_id": None,
        "status": None,
        "seen_count": None,
        "manual_reason": None,
        "candidate_id": None,
        "candidate_name": None,
        "candidate_reason": None,
        "candidate_basis": None,
        "audit_id": None,
        "criterion": None,
        "confidence": None,
        "standard_id": None,
        "standard_name": None,
        "alias_inserted": False,
        "match_updated": False,
        "sighting_inserted": False,
        "message": "",
        "revoked_audit_id": None,
        "alias_deleted": False,
        "matches_restored": 0,
    }
    payload.update(kwargs)
    return payload


def _run_in_transaction(conn: sqlite3.Connection, work: Callable[[], dict]) -> dict:
    """自己开启的事务在成功时提交，失败时回滚。调用方已经开启事务时，由调用方负责提交或回滚。"""
    started = False
    if not conn.in_transaction:
        conn.execute("BEGIN")
        started = True
    try:
        result = work()
    except Exception:
        if started:
            conn.rollback()
        raise
    if started:
        conn.commit()
    return result


def _require_kind(kind: str) -> None:
    """类别只允许球队或赛事。"""
    if kind not in _KINDS:
        raise ValueError("类别只能是 team 或 competition。team 表示球队，competition 表示赛事。")


def _require_token(value: Any, label: str) -> str:
    """入口名称、来源位置和操作人不能是空白。"""
    text = "" if value is None else str(value).strip()
    if not text:
        raise ValueError(f"{label}不能为空。")
    return text


def _require_pool_tables(conn: sqlite3.Connection) -> None:
    """四张新表必须已经由 ensure_schema 建立。本函数不会替调用方建表。"""
    missing = [name for name in _POOL_TABLES if not _table_exists(conn, name)]
    if missing:
        joined = "、".join(missing)
        raise RuntimeError(
            f"待定池的表还没有建立：{joined}。请先在目标连接上调用 ensure_schema。"
            "导入本模块不会建立这些表，也不会写入正式库。"
        )


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """判断当前连接里是否已经有这张表。"""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    """读取一张表的列名。表名只来自本模块的固定名单。"""
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _fetchall_dicts(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    """把查询结果转成字典列表，不依赖连接的行工厂。"""
    cursor = conn.execute(sql, params)
    names = [item[0] for item in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _fetchone_dict(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> dict | None:
    """读取一行。没有结果时返回空。"""
    rows = _fetchall_dicts(conn, sql, params)
    return rows[0] if rows else None


def _dump(payload: dict) -> str:
    """把字典写成 JavaScript 对象表示法文本，并保留中文。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _dump_list(items: list) -> str:
    """把列表写成 JavaScript 对象表示法文本，并保留原来的顺序。"""
    return json.dumps(items, ensure_ascii=False)


def _load_object(text: str) -> dict:
    """读取审计里的对象文本。内容不是对象时拒绝继续撤销，以免按错误内容回写场次。"""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("映射审计中的前后值不是合法的对象文本，已停止撤销。") from exc
    if not isinstance(payload, dict):
        raise ValueError("映射审计中的前后值必须是对象，已停止撤销。")
    return payload


def _int_or_none(value: Any) -> int | None:
    """把数据库中的编号转成整数。空值保持为空。"""
    if value is None or value == "":
        return None
    return int(value)
