"""共享数据接口（5DF）补数让路判断（0.3.19 追加 2）。只用标准库，补数脚本 / 队列共用。

- 共享限额：同一账户窗口 （账户上限）；分析师前向采集（odds-data/scripts/live_capture.py）最多用 24 次/分钟。
  → 我们的补数（所有调这个接口的回补脚本 / 队列，**不含** live_capture 本身）合计 ≤ 16 次/分钟。
- 让路时段（北京时间，左闭右开）：11:05–11:20、14:55–15:15、21:55–22:15（11:10 即时、15:00 中盘、22:00 临盘集中到点）。
  时段内不发请求：before_request() 睡到时段结束再放行。
- 响应头 X-RateLimit-Remaining ≤ 24（分析师那份）→ 停到 X-RateLimit-Reset（缺省 61 秒）。
- 多进程：同一台机器上的补数进程共用一个时间戳账本（flock），合计 ≤ 16 次/分钟。
用法：
    import sys; sys.path.insert(0, "api/app"); import shared_api_yield as Y
    gate = Y.Gate("run_queue")
    gate.before_request(); resp = urlopen(...); gate.after_response(resp.headers)
"""
from __future__ import annotations
# --- public repo: paths are env-overridable (see config.example.env) ---
import os as _rp_os
from pathlib import Path as _RpPath
_REPO_ROOT = _RpPath(__file__).resolve().parents[2]
# 只读 .env：本模块不再把整份 .env 注入 os.environ（2026-10-13），只按名字取自己需要的几个值。
_DOTENV_CACHE: dict | None = None


def _read_dotenv(path=_REPO_ROOT / '.env') -> dict:
    """解析仓库根 .env（KEY=VALUE），返回字典；不修改 os.environ，不打印任何值。"""
    out: dict = {}
    if _rp_os.environ.get('FAT_DISABLE_DOTENV') == '1':  # 测试时由 conftest 设置，避免本机 .env 干扰
        return out
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
            if _k:
                out[_k] = _v
    except FileNotFoundError:
        pass
    return out


def env_value(name: str, default: str | None = None) -> str | None:
    """先看进程环境变量，再看 .env；都没有时返回 default。不写 os.environ。"""
    global _DOTENV_CACHE
    v = _rp_os.environ.get(name, '').strip()
    if v:
        return v
    if _DOTENV_CACHE is None:
        _DOTENV_CACHE = _read_dotenv()
    v = (_DOTENV_CACHE.get(name) or '').strip()
    return v or default


def _rp_env(name, default, base=None):
    v = env_value(name, '') or ''
    p = _RpPath(v).expanduser() if v else default
    return p if p.is_absolute() else (base or _REPO_ROOT) / p
_MA_API_ROOT = _rp_env('MA_API_ROOT', _REPO_ROOT / 'api')
_ODDS_DATA_DIR = _rp_env('ODDS_DATA_DIR', _REPO_ROOT / 'data' / 'odds-data')
_APP_DB = _rp_env('APP_DB_PATH', _MA_API_ROOT / 'data' / 'app.db', _MA_API_ROOT)
_V2D3_DB = _rp_env('V2D3_DB_PATH', _MA_API_ROOT / 'data' / 'v2d3' / 'app.db', _MA_API_ROOT)
_BACKUP_DIR = _rp_env('BACKUP_DIR', _REPO_ROOT / 'backups' / 'football')
# --- end path config ---


# 跨平台文件锁（Windows 使用 msvcrt）。本模块既可能以 app.shared_api_yield 导入，也可能把 api/app 加入搜索路径后直接导入。
try:
    from app import portable_lock as fcntl
except ImportError:
    import portable_lock as fcntl  # type: ignore[no-redef]
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

TZ_CN = timezone(timedelta(hours=8))
SHARED_LIMIT_PER_MIN = 40
ANALYST_LIVE_MAX_PER_MIN = 24
BACKFILL_MAX_PER_MIN = SHARED_LIMIT_PER_MIN - ANALYST_LIVE_MAX_PER_MIN  # 16
MIN_INTERVAL_SEC = 60.0 / BACKFILL_MAX_PER_MIN  # 3.75s（单进程也不突发）
REMAINING_FLOOR = ANALYST_LIVE_MAX_PER_MIN  # 剩余 ≤ 24 → 停
YIELD_WINDOWS: tuple[tuple[tuple[int, int], tuple[int, int]], ...] = (
    ((11, 5), (11, 20)),
    ((14, 55), (15, 15)),
    ((21, 55), (22, 15)),
)
LEDGER = Path(env_value("SHARED_API_YIELD_LEDGER", str(_ODDS_DATA_DIR / "5dollar/backfill_yield_ledger.json")))
# 单次 before_request() 的总等待上限（秒）：额度暂停 + 速率等待之和；让路时段的等待不计入（时段固定、可预期）。
DEFAULT_MAX_WAIT_SEC = float(env_value("SHARED_API_YIELD_MAX_WAIT_SEC", "300"))
# 额度暂停的合理上限（秒），防止 Reset 头异常时睡几个小时
MAX_QUOTA_PAUSE_SEC = 3600.0
LOG = Path(env_value("SHARED_API_YIELD_LOG", str(_ODDS_DATA_DIR / "5dollar/backfill_yield.jsonl")))


class SharedYieldWaitExceeded(RuntimeError):
    """before_request() 的总等待会超过上限：调用方应跳过本轮，而不是一直挂着。"""

    def __init__(self, waited_sec: float, reason: str, *, caller: str = "", max_wait_sec: float = 0.0):
        self.waited_sec = float(waited_sec)
        self.reason = reason
        self.caller = caller
        self.max_wait_sec = float(max_wait_sec)
        super().__init__(f"shared api yield wait exceeded: caller={caller} reason={reason} "
                         f"waited={self.waited_sec:.1f}s max={self.max_wait_sec:.1f}s")


def pause_path(ledger: Path) -> Path:
    """全进程共享的额度暂停文件（与时间戳账本分开存放，旧版本进程读账本不受影响）。"""
    return ledger.with_name(ledger.stem + ".pause.json")


def parse_reset_seconds(reset: Any, now_epoch: float) -> Optional[float]:
    """把 X-RateLimit-Reset 换成「距现在还有多少秒」：识别时间戳毫秒、时间戳秒和剩余秒数三种格式。"""
    try:
        r = float(reset)
    except (TypeError, ValueError):
        return None
    if r > 1e12:  # 时间戳毫秒
        return r / 1000.0 - now_epoch
    if r > 1e9:  # 时间戳秒
        return r - now_epoch
    return r  # 剩余秒数


def _cn(now: Optional[datetime] = None) -> datetime:
    n = now or datetime.now(TZ_CN)
    return (n if n.tzinfo else n.replace(tzinfo=TZ_CN)).astimezone(TZ_CN)


def yield_window(now: Optional[datetime] = None) -> Optional[tuple[datetime, datetime]]:
    """当前处在哪个让路时段 → (开始, 结束)；不在 → None。"""
    n = _cn(now)
    for (h0, m0), (h1, m1) in YIELD_WINDOWS:
        a = n.replace(hour=h0, minute=m0, second=0, microsecond=0)
        b = n.replace(hour=h1, minute=m1, second=0, microsecond=0)
        if a <= n < b:
            return a, b
    return None


def must_yield(now: Optional[datetime] = None) -> bool:
    return yield_window(now) is not None


def seconds_until_clear(now: Optional[datetime] = None) -> float:
    w = yield_window(now)
    return 0.0 if w is None else max(0.0, (w[1] - _cn(now)).total_seconds())


def _log(event: str, **kw: Any) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": _cn().isoformat(), "event": event, **kw}, ensure_ascii=False) + "\n")
    except OSError:
        pass


class Gate:
    """补数请求闸门：让路时段 + 合计 ≤16/分钟 + 剩余额度保底（暂停时刻全进程共享）+ 总等待上限。"""

    def __init__(self, caller: str, *, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.time,
                 now: Callable[[], datetime] = lambda: datetime.now(TZ_CN),
                 ledger: Optional[Path] = None, max_wait_sec: Optional[float] = None):
        self.caller = caller
        self._sleep, self._clock, self._now = sleep, clock, now
        self.ledger = ledger if ledger is not None else LEDGER
        self.max_wait_sec = DEFAULT_MAX_WAIT_SEC if max_wait_sec is None else float(max_wait_sec)
        self.pause_until = 0.0
        self._last_stamp: Optional[float] = None
        self.stats = {"requests": 0, "yield_sleeps": 0, "yield_seconds": 0.0,
                      "rate_sleeps": 0, "quota_pauses": 0, "wait_exceeded": 0}

    def _nap(self, reason: str, seconds: float) -> None:
        """每次睡眠前写一行日志（原因 + 时长）。"""
        _log("sleep", caller=self.caller, reason=reason, sleep_s=round(seconds, 3))
        self._sleep(seconds)

    # ---- 让路时段（逻辑不变）
    def wait_for_clear(self) -> float:
        slept = 0.0
        while True:
            s = seconds_until_clear(self._now())
            if s <= 0:
                return slept
            w = yield_window(self._now())
            _log("yield_window", caller=self.caller, until=w[1].isoformat() if w else None, sleep_s=round(s, 1))
            self.stats["yield_sleeps"] += 1
            self.stats["yield_seconds"] += s
            self._nap("yield_window", s + 1.0)
            slept += s + 1.0

    # ---- 全进程共享的额度暂停
    def _pause_file(self) -> Path:
        return pause_path(self.ledger)

    def shared_pause_until(self) -> float:
        try:
            d = json.loads(self._pause_file().read_text(encoding="utf-8") or "{}")
            return float(d.get("pause_until") or 0.0)
        except (OSError, ValueError, TypeError, AttributeError):
            return 0.0

    def _write_shared_pause(self, until: float) -> None:
        p = self._pause_file()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a+", encoding="utf-8") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                f.seek(0)
                try:
                    cur = float((json.loads(f.read() or "{}") or {}).get("pause_until") or 0.0)
                except (ValueError, TypeError, AttributeError):
                    cur = 0.0
                if until <= cur:
                    return
                f.seek(0)
                f.truncate()
                f.write(json.dumps({"pause_until": until, "set_by": self.caller, "set_at": self._clock()}))
        except OSError:
            pass

    # ---- 跨进程速率账本
    def _ledger_update(self, fn: Callable[[list[float]], list[float]]) -> None:
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with open(self.ledger, "a+", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            try:
                stamps = [float(x) for x in json.loads(f.read() or "[]")]
            except (ValueError, TypeError):
                stamps = []
            stamps = fn(stamps)
            f.seek(0)
            f.truncate()
            f.write(json.dumps(stamps[-BACKFILL_MAX_PER_MIN * 2:]))

    def _drop_stamp(self, stamp: Optional[float]) -> None:
        """重新预约前删掉本进程作废的那个时间戳。"""
        if stamp is None:
            return
        def fn(stamps: list[float]) -> list[float]:
            out, removed = [], False
            for x in stamps:
                if not removed and abs(x - stamp) < 1e-6:
                    removed = True
                    continue
                out.append(x)
            return out
        try:
            self._ledger_update(fn)
        except OSError:
            pass
        if self._last_stamp == stamp:
            self._last_stamp = None

    def _reserve_slot(self) -> float:
        """在账本里占一个发送时刻，返回需要等待的秒数（0 = 现在就发）。

        最多只预约到「现在 + 一个间隔」；更远的时刻不占位，只返回等待秒数，等完再来预约。
        """
        t = self._clock()
        res: dict[str, Any] = {}

        def fn(stamps: list[float]) -> list[float]:
            stamps = sorted(x for x in stamps if x > t - 60.0)
            send = t
            if stamps:
                send = max(send, stamps[-1] + MIN_INTERVAL_SEC)
            if len(stamps) >= BACKFILL_MAX_PER_MIN:
                send = max(send, stamps[-BACKFILL_MAX_PER_MIN] + 60.0)
            res["send"] = send
            if send - t <= MIN_INTERVAL_SEC + 1e-9:
                stamps.append(send)
                res["reserved"] = True
            return stamps

        try:
            self._ledger_update(fn)
        except OSError:
            return MIN_INTERVAL_SEC
        send = res.get("send", t)
        if res.get("reserved"):
            self._last_stamp = send
            return max(0.0, send - t)
        # 未占位：先等到「只差一个间隔」再来
        return -max(0.0, send - t - MIN_INTERVAL_SEC)

    def before_request(self, max_wait_sec: Optional[float] = None) -> None:
        """每次调共享接口前调用：额度暂停 → 让路时段 → 速率。

        额度暂停与速率等待合计超过 max_wait_sec（缺省 self.max_wait_sec）时抛 SharedYieldWaitExceeded；
        让路时段的等待不计入上限。
        """
        cap = self.max_wait_sec if max_wait_sec is None else float(max_wait_sec)
        waited = 0.0

        def guard(reason: str, s: float) -> None:
            if waited + s > cap:
                self.stats["wait_exceeded"] += 1
                _log("wait_exceeded", caller=self.caller, reason=reason, waited_s=round(waited, 1),
                     need_s=round(s, 1), max_wait_s=cap)
                raise SharedYieldWaitExceeded(waited, reason, caller=self.caller, max_wait_sec=cap)

        while True:
            wait_q = max(self.pause_until, self.shared_pause_until()) - self._clock()
            if wait_q > 0:
                guard("quota_pause", wait_q)
                self._nap("quota_pause", wait_q)
                waited += wait_q
            self.wait_for_clear()
            wait = self._reserve_slot()
            if wait < 0:  # 预约太远：不占位，等一会再来
                guard("rate_limit", -wait)
                self.stats["rate_sleeps"] += 1
                self._nap("rate_limit", -wait)
                waited += -wait
                continue
            if wait > 0:
                try:
                    guard("rate_limit", wait)
                except SharedYieldWaitExceeded:
                    self._drop_stamp(self._last_stamp)
                    raise
                self.stats["rate_sleeps"] += 1
                self._nap("rate_limit", wait)
                waited += wait
            if must_yield(self._now()) or self.shared_pause_until() > self._clock():
                # 等速率时跨进了让路时段或被别的进程设了额度暂停 → 删掉作废的预约再来
                self._drop_stamp(self._last_stamp)
                continue
            self._last_stamp = None
            self.stats["requests"] += 1
            return

    def after_response(self, headers: Optional[Mapping[str, Any]]) -> None:
        """看 X-RateLimit-Remaining：≤24（分析师那份）→ 按 Reset 一次算出暂停截止时刻，写进共享暂停文件。"""
        if not headers:
            return
        get = headers.get
        rem, rst = get("X-RateLimit-Remaining"), get("X-RateLimit-Reset")
        try:
            rem_i = int(rem) if rem is not None else None
        except (TypeError, ValueError):
            rem_i = None
        if rem_i is None or rem_i > REMAINING_FLOOR:
            return
        now = self._clock()
        secs = parse_reset_seconds(rst, now)
        pause = 61.0 if secs is None else secs + 1.0
        pause = min(max(pause, 1.0), MAX_QUOTA_PAUSE_SEC)
        until = now + pause
        self.pause_until = max(self.pause_until, until)
        self._write_shared_pause(until)
        self.stats["quota_pauses"] += 1
        _log("quota_floor", caller=self.caller, remaining=rem_i, reset=rst, pause_s=round(pause, 1))
