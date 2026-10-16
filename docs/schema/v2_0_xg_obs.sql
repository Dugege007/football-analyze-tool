-- 预期进球六列（数据表「预测」标签的基本面）。
-- 观测仍写入已有的 stats_obs，一行 kind = 'xg'。本文件不增加宽表快捷列。
-- 映射表和拉取队列与盘口补数队列分开。语句可重复执行。
-- 库内数值保留供应商给出的完整精度，不在这里做四舍五入。

CREATE TABLE IF NOT EXISTS stats_obs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  match_id      INTEGER NOT NULL,
  kind          TEXT NOT NULL,
  as_of         TEXT NOT NULL,
  source        TEXT NOT NULL,
  payload_json  TEXT NOT NULL,
  provider_ref  TEXT,
  fetched_at    TEXT,
  quality       TEXT,
  extras_json   TEXT,
  UNIQUE (match_id, kind, as_of, source)
);

CREATE INDEX IF NOT EXISTS idx_stats_obs_match_kind_asof
  ON stats_obs (match_id, kind, as_of);
CREATE INDEX IF NOT EXISTS idx_stats_obs_source
  ON stats_obs (source, kind);

-- 本库比赛与 BSD 赛事编号的对应。没有对应行时，完赛场次的六列都是无法获取。
CREATE TABLE IF NOT EXISTS bsd_event_map (
  match_id  INTEGER PRIMARY KEY,
  event_id  INTEGER NOT NULL,
  mapped_at TEXT,
  note      TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_bsd_event_map_event_id
  ON bsd_event_map (event_id);

-- 完赛后的预期进球拉取队列。与 5DollarFootballAPI 盘口队列无关。
-- 第一次在完赛写入后立即到期；成功后再排一次，到期时间为三小时之后。
CREATE TABLE IF NOT EXISTS xg_fetch_queue (
  match_id      INTEGER PRIMARY KEY,
  due_at        TEXT NOT NULL,
  attempts      INTEGER NOT NULL DEFAULT 0,
  max_attempts  INTEGER NOT NULL DEFAULT 2,
  last_status   TEXT,
  last_error    TEXT,
  done_at       TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_xg_fetch_queue_due
  ON xg_fetch_queue (done_at, due_at);
