"""读取仓库外的 auto-cn-push 配置。不使用脚本里的默认库路径。"""
from __future__ import annotations

import os
from pathlib import Path


class ConfigError(RuntimeError):
    pass


def parse_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def load_push_env(path: str | Path | None = None) -> dict[str, str]:
    raw = str(path or os.environ.get("AUTO_CN_PUSH_ENV") or "").strip()
    if not raw:
        raise ConfigError("AUTO_CN_PUSH_ENV 未设置，拒绝使用默认库")
    env_path = Path(raw)
    if not env_path.is_file():
        raise ConfigError(f"配置文件不存在: {env_path}")
    values = parse_env_text(env_path.read_text(encoding="utf-8-sig"))
    db = values.get("APP_DB_PATH", "").strip()
    live = values.get("ODDS_DATA_DIR", "").strip()
    if not db:
        raise ConfigError("配置缺少 APP_DB_PATH")
    if not Path(db).is_file():
        raise ConfigError(f"APP_DB_PATH 不是已有文件: {db}")
    if not live:
        raise ConfigError("配置缺少 ODDS_DATA_DIR")
    if not Path(live).is_dir():
        raise ConfigError(f"ODDS_DATA_DIR 不是已有目录: {live}")
    values["APP_DB_PATH"] = db
    values["ODDS_DATA_DIR"] = live
    return values
