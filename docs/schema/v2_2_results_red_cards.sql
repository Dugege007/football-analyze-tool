-- 赛果表追加主客红牌数。只加可空整数列，不改 home_goals、away_goals、total_goals、wdl。
-- NULL 表示这次没有取得红牌，不能当成 0。0 表示完赛来源明确该侧没有红牌。正整数是确认张数。
-- 已有行保持 NULL，直到 scripts/live/result_ingest.py 的 red_cards 子命令回填。
-- 黄牌本次不加列。
-- SQLite 不能 ADD COLUMN IF NOT EXISTS。重复执行时由 api/app/db.py 的 apply_sql_idempotent
-- 忽略 duplicate column。直接 sqlite3 < 本文件只适用于还没有这两列的库。
-- 回滚：停止写入这两列。旧客户端忽略未知字段仍然兼容。本文件不写任何比赛数据。

ALTER TABLE results ADD COLUMN home_red_cards INTEGER;
ALTER TABLE results ADD COLUMN away_red_cards INTEGER;
