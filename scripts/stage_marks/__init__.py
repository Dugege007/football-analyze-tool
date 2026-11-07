"""取数管线统一阶段完成标记。

常用：
  from stage_marks.mark import write_stage_mark, read_stage_mark
  python3 -m stage_marks.check --profile live
"""
from .mark import (  # noqa: F401
    STAGE_DEFS,
    write_stage_mark,
    read_stage_mark,
    list_latest_marks,
    is_fresh,
    age_minutes,
    marks_root,
)
