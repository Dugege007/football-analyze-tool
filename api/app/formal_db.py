"""正式库路径与一次性脚本的写前保护（2026-10-15 研究副本 v2d3 并入正式库之后）。

约定：
- 正式库路径沿用仓库现有写法：环境变量 APP_DB_PATH 优先，未设置时为 api/data/app.db。
- 研究副本 v2d3 已并入正式库并设为只读归档（chmod 444），一次性脚本不得再把它当作写入目标。
- 运行任何会写正式库的一次性脚本之前，必须先用 SQLite backup 接口备份正式库，
  然后设置环境变量 FORMAL_DB_BACKUP_DONE=1 表示已经备份；没有设置时，脚本拒绝写正式库。
  只读运行（dry-run、report、--help）不需要备份。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parent.parent


def _resolve_env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name, "").strip()
    path = Path(value).expanduser() if value else default
    return (path if path.is_absolute() else API_ROOT / path).resolve()


# 正式库：与 app/db.py 的 APP_DB_PATH 写法一致。
FORMAL_DB_PATH: Path = _resolve_env_path("APP_DB_PATH", API_ROOT / "data" / "app.db")
# 已归档的研究副本（只读）。
ARCHIVED_V2D3_PATH: Path = (API_ROOT / "data" / "v2d3" / "app.db").resolve()

BACKUP_ENV = "FORMAL_DB_BACKUP_DONE"
BACKUP_NOTICE = (
    "运行前必须先备份正式库（使用 SQLite backup 接口，不要直接复制正在写入的文件），"
    f"备份完成后设置环境变量 {BACKUP_ENV}=1 再运行；只读运行（dry-run、report）不需要。"
)


def guard_write_target(db: Path | str, *, writing: bool = True) -> Path:
    """检查写入目标：拒绝已归档的 v2d3；写正式库时要求已备份。返回解析后的路径。"""
    path = Path(db).resolve()
    if path == ARCHIVED_V2D3_PATH:
        raise SystemExit(
            f"REFUSE: {path} 是已并入正式库的只读归档，不能再写入。请改用正式库 {FORMAL_DB_PATH}。"
        )
    if writing and path == FORMAL_DB_PATH:
        if os.environ.get(BACKUP_ENV, "").strip() != "1":
            raise SystemExit(f"REFUSE: 将要写正式库 {path}。{BACKUP_NOTICE}")
        print(f"[notice] 正在写正式库 {path}；请确认已经完成备份。", file=sys.stderr)
    return path
