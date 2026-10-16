-- 机构赛果（亚洲让球盘、大小球、欧盘胜平负 × 五家机构，另加多家平均）
-- 口径：uploads 中的《机构赛果落库口径》2026-10-15 第 4 节，多家平均见 2026-10-16 第 11 节。
-- 唯一键是 match_id + book + market。重复计算必须覆盖原行，不能追加第二行。
-- book 允许 macau、crown、pinnacle、william、bet365，以及多家平均 avg。
-- 多家平均的三行与五家机构使用同一张表，不新增列。亚盘 line 为仓库记法，写入前不再取反。
-- 本文件只建表，不写入任何比赛的业务数据。
-- 离线回填见 api/scripts/backfill_market_settle.py，不带 --apply 时不写库。
-- 只补多家平均三行时加上 --book avg。算不出仍写无法获取，不留空键。
-- 持续落库与离线回填使用同一套 apply_backfill：完场比分写入 results 成功后重算该场（含多家平均）；
-- 亚洲让球盘、大小球、欧盘三项的临盘写入或更新成功后，同样按唯一键覆盖该场（含多家平均）。
-- 触发路径只写当前数据库连接里的 market_settle，不打开双写，也不计算竞彩赛果。

CREATE TABLE IF NOT EXISTS market_settle (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  match_id       INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  book           TEXT NOT NULL,   -- macau | crown | pinnacle | william | bet365 | avg
  market         TEXT NOT NULL,   -- ah | ou | x1x2
  -- 用到的临盘快照（可追溯）
  line           REAL,            -- 亚洲让球盘：仓库记法（正数表示主队让球）；大小球：进球线；欧盘：空
  home_water     REAL,            -- 亚洲让球盘可选
  away_water     REAL,
  over_water     REAL,            -- 大小球可选
  under_water    REAL,
  odds_home      REAL,            -- 欧盘临盘三项
  odds_draw      REAL,
  odds_away      REAL,
  odds_used      REAL,            -- 欧盘：命中方向赔率；亚洲让球盘和大小球：可选，赢盘方水位
  -- 完场输入快照（重算时对照）
  home_goals     INTEGER,
  away_goals     INTEGER,
  -- 结果
  side           TEXT,            -- ah: home|away；ou: over|under；x1x2: home|draw|away；走水或空则为空
  push           INTEGER NOT NULL DEFAULT 0,  -- 1 表示整盘走水
  settle_code    TEXT,            -- ah/ou：win|win_half|push|lose_half|lose（只存结算码，不存二元组）；x1x2：空
  display_text   TEXT,            -- 短文案
  cell_status    TEXT NOT NULL,   -- ok | pending | unavailable
  null_reason    TEXT,
  null_reason_zh TEXT,
  line_source    TEXT,            -- odds_asian | odds_snapshot | odds_goalline | odds_euro_home | avg_close
  computed_at    TEXT NOT NULL,   -- ISO-8601，时区 +08:00
  UNIQUE (match_id, book, market)
);

CREATE INDEX IF NOT EXISTS idx_market_settle_book_market
  ON market_settle (book, market);

CREATE INDEX IF NOT EXISTS idx_market_settle_computed_at
  ON market_settle (computed_at);

INSERT OR IGNORE INTO schema_migrations (version, note)
VALUES (
  'v2.1',
  '机构赛果表 market_settle：五家机构的亚洲让球盘、大小球、欧盘胜平负，按 match_id + book + market 唯一覆盖'
);
