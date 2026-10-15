-- 机构赛果（亚洲让球盘、大小球、欧盘胜平负 × 五家机构）
-- 口径：uploads 中的《机构赛果落库口径》2026-10-15，第 4 节。
-- 唯一键是 match_id + book + market。重复计算必须覆盖原行，不能追加第二行。
-- 本文件只建表，不写入任何比赛的业务数据。回填写库见 api/scripts/backfill_market_settle.py，默认不执行。

CREATE TABLE IF NOT EXISTS market_settle (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  match_id       INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  book           TEXT NOT NULL,   -- macau | crown | pinnacle | william | bet365
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
  line_source    TEXT,            -- odds_asian | odds_snapshot | odds_goalline | odds_euro_home
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
