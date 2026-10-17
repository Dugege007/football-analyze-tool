-- 队名与赛事名待定池（四张新表）。
-- 本文件只新建表和索引，不修改球队、赛事、别名、场次、预测等任何已有表。
-- 全部语句都可以重复执行：表和索引都带 IF NOT EXISTS，重复执行不会清空已有的行。
-- 本文件没有加入自动迁移列表。导入程序、启动服务或合并拉取请求都不会执行它。
-- 要对某个库建表，只能由调用方在已经打开的连接上显式执行，并且不得在未经批准时指向正式库。
-- 时间列不使用数据库的协调世界时默认值。写入方必须提供北京时间的国际标准化组织 8601 文本，例如 2026-10-17T15:20:00+08:00。
--
-- 回滚时按这个顺序手工删除四张表：alias_mapping_audit、alias_pool_sighting、alias_pool_entry、alias_auto_block。
-- 本文件不包含删除语句。重复执行不会清掉已经写入的行。

PRAGMA foreign_keys = ON;

-- 待定池主表。同一个类别加上同一个规范化键只保留一行。
CREATE TABLE IF NOT EXISTS alias_pool_entry (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  kind              TEXT NOT NULL CHECK (kind IN ('team', 'competition')),
  raw_name          TEXT NOT NULL, -- 最早一次出现时的原始写法，之后不覆盖。
  norm_key          TEXT NOT NULL, -- 规范化键，规则与 api/app/team_pending.py 的 norm 函数一致。
  loose_key         TEXT NOT NULL, -- 宽松键。在规范化键上删除全部空白和方案规定的标点，只删除，不替换。
  scope_hint        TEXT, -- 最近一次出现时的辅助信息：球队记赛事名，赛事记国家或地区。
  first_source      TEXT NOT NULL, -- 第一次把这个名称送入待定池的入口名称。
  sources_json      TEXT NOT NULL DEFAULT '[]', -- 全部入口名称的去重列表，使用 JavaScript 对象表示法中的数组文本。
  first_source_ref  TEXT, -- 第一次出现的位置，例如 matches.id=338#home_team。
  seen_count        INTEGER NOT NULL DEFAULT 0, -- 出现次数，等于出现记录表中该条的行数，不是单独累加的计数器。
  first_seen_at     TEXT NOT NULL, -- 首次出现时间，北京时间的国际标准化组织 8601 文本。
  last_seen_at      TEXT NOT NULL, -- 最近一次新增出现记录的时间，重复上报同一个位置时不更新。
  candidate_kind_id INTEGER, -- 候选标准名在 teams 或 leagues 中的编号。只是提示，不是已经确认的映射。
  candidate_name    TEXT, -- 候选标准名。只是提示。
  candidate_reason  TEXT, -- 候选理由。必须写成完整句子，说明依据，不能把提示写成已经确认。
  candidate_basis   TEXT, -- 依据类别：standard_file、alias_table、loose_key、affix、manual。
  status            TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'confirmed', 'rejected', 'ambiguous')),
  manual_reason     TEXT, -- 必须人工处理的原因。包括 gender、age_group、age_u22、same_name_other_team、reserve_team、multi_candidate、auto_revoked、other。U22 记为 age_u22，表示年龄段，不是二队。
  confirmed_id      INTEGER, -- 确认后的标准名编号。自动映射确认时也会填写。
  confirmed_by      TEXT, -- 确认人。自动映射填写 auto。
  confirmed_at      TEXT, -- 确认时间，北京时间的国际标准化组织 8601 文本。
  extras            TEXT NOT NULL DEFAULT '{}', -- 备注等补充信息，使用 JavaScript 对象表示法文本。不得写入比分、赛果或赔率。
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  UNIQUE (kind, norm_key)
);

CREATE INDEX IF NOT EXISTS idx_alias_pool_entry_status ON alias_pool_entry (kind, status);
CREATE INDEX IF NOT EXISTS idx_alias_pool_entry_loose ON alias_pool_entry (kind, loose_key);

-- 出现记录表。同一个池记录、同一个入口、同一个位置只保留一行，因此重复上报不会增加次数。
CREATE TABLE IF NOT EXISTS alias_pool_sighting (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  entry_id     INTEGER NOT NULL REFERENCES alias_pool_entry (id) ON DELETE CASCADE,
  source       TEXT NOT NULL, -- 入口名称。
  source_ref   TEXT NOT NULL, -- 位置。必须能够区分是哪一条记录的哪一个名称，例如 matches.id=338#home_team。
  context_json TEXT NOT NULL DEFAULT '{}', -- 当时已经知道的赛前信息。只保留赛事名、竞彩日、计划开赛时间、国家或地区、对手是否已经映射。
  seen_at      TEXT NOT NULL, -- 这条出现记录写入的时间，北京时间的国际标准化组织 8601 文本。
  UNIQUE (entry_id, source, source_ref)
);

-- 映射审计表。人工确认、自动映射和撤销共用这一张表，用 mode 区分。
-- effective_at 是映射生效时间，供以后的回溯区分「当时已经知道这个身份」。写入本表不修改已经冻结的预测。
CREATE TABLE IF NOT EXISTS alias_mapping_audit (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  mode                TEXT NOT NULL CHECK (mode IN ('manual', 'auto', 'revoke')),
  kind                TEXT NOT NULL CHECK (kind IN ('team', 'competition')),
  raw_name            TEXT NOT NULL, -- 被映射的原始名称。
  standard_id         INTEGER NOT NULL, -- 并入的标准名编号。
  standard_name       TEXT NOT NULL, -- 并入的标准名。
  criterion           TEXT NOT NULL, -- 判据代码：manual_confirm、known_alias_exact、loose_key_equal、revoke。
  confidence          TEXT NOT NULL CHECK (confidence IN (
                        'manual',
                        'level1_known_alias',
                        'level2_equal_after_cleaning',
                        'revoke'
                      )), -- 撤销没有置信等级，单独使用 revoke，避免把撤销记成人工确认或某一级自动映射。
  source              TEXT NOT NULL, -- 触发入口。撤销记为 revoke。
  source_ref          TEXT, -- 触发位置。撤销记为 audit.id=原审计编号。
  pool_entry_id       INTEGER REFERENCES alias_pool_entry (id),
  alias_row_table     TEXT, -- 本次新写入的别名表名：team_aliases 或 league_aliases。没有新写入时为空。
  alias_row_id        INTEGER, -- 本次新写入的别名行编号。撤销时只删除这里指向的行，不删除原来就已经存在的别名。
  before_json         TEXT NOT NULL, -- 动作之前的池状态、别名行和受影响场次的三个身份编号。
  after_json          TEXT NOT NULL, -- 动作之后的池状态、别名行和受影响场次的三个身份编号。若某一列因已被改过而跳过，这里用完整句子写明原因。
  actor               TEXT NOT NULL, -- 操作人。自动映射填写 auto，撤销填写调用方给出的人名。
  batch_id            TEXT, -- 批量确认或回溯的批次号。单条自动映射和单条撤销不填写。
  revoked_by_audit_id INTEGER REFERENCES alias_mapping_audit (id), -- 若本行已被撤销，指向那条撤销记录。
  effective_at        TEXT NOT NULL, -- 映射生效时间。本拉取请求中的自动映射和撤销，生效时间就是执行当时的北京时间。
  created_at          TEXT NOT NULL -- 本行写入审计表的时间，格式与生效时间相同。
);

CREATE INDEX IF NOT EXISTS idx_alias_audit_raw ON alias_mapping_audit (kind, raw_name);
CREATE INDEX IF NOT EXISTS idx_alias_audit_batch ON alias_mapping_audit (batch_id);

-- 禁止自动名单。撤销一次自动映射后，该规范化键不再自动映射。歧义标记由程序直接识别，不必先写入本表。
CREATE TABLE IF NOT EXISTS alias_auto_block (
  kind       TEXT NOT NULL CHECK (kind IN ('team', 'competition')),
  norm_key   TEXT NOT NULL,
  reason     TEXT NOT NULL, -- 原因：auto_revoked、gender、age_group、same_name_other_team。
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL, -- 北京时间的国际标准化组织 8601 文本。
  PRIMARY KEY (kind, norm_key)
);
