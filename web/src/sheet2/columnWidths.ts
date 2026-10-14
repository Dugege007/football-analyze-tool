/**
 * 数据表（/sheet）全部列宽，单位像素。页面统一从这里读宽度。
 * 用户填写表：web/docs/sheet-column-widths.xlsx（同内容 CSV），按「配置 key」一列回写本表。
 */
export const COLUMN_WIDTHS = {
  // 基本九列（所有标签，固定在左侧）
  'base.jcDay': 100, // 竞彩日
  'base.jcNo': 80, // 编号
  'base.kickoff': 100, // 开赛时间
  'base.league': 90, // 赛事
  'base.home': 100, // 主队
  'base.away': 100, // 客队
  'base.score': 60, // 比分
  'base.goals': 60, // 总进球
  'base.outcome': 48, // 赛果
  // 预测组
  'pred.ah': 80, // 亚盘预测
  'pred.ah.res': 70, // 亚盘预测的结果
  'pred.ou': 80, // 大小预测
  'pred.ou.res': 70, // 大小预测的结果
  'pred.x1x2': 80, // 欧盘预测
  'pred.x1x2.res': 70, // 欧盘预测的结果
  'pred.jc': 80, // 竞彩预测
  'pred.jc.res': 70, // 竞彩预测的结果
  // 机构赛果列（多家平均、澳门、皇冠、平博、威廉、Bet365）
  'res.ah': 80, // 亚盘赛果
  'res.ou': 80, // 大小赛果
  'res.x1x2': 80, // 欧盘赛果
  // 机构盘口：临、中、初三个阶段共用
  'ah.water': 48, // 主水、客水
  'ah.line': 60, // 亚盘盘口
  'ou.water': 48, // 大球、小球
  'ou.line': 60, // 大小球盘口
  'x1x2.odds': 48, // 欧赔胜、平、负
  // 竞彩
  'jc.res.had': 80, // 胜平负赛果
  'jc.res.hhad': 100, // 让球胜平负赛果
  'jc.had.odds': 48, // 胜平负临、中、初盘的胜、平、负
  'jc.hhad.goal': 48, // 让球胜平负临、中、初盘的让球
  'jc.hhad.odds': 48, // 让球胜平负临、中、初盘的胜、平、负
} as const

export type WidthKey = keyof typeof COLUMN_WIDTHS
export const w = (k: WidthKey): number => COLUMN_WIDTHS[k]

export type Align = 'left' | 'center' | 'right'
/**
 * 对齐方式。单列直接写 left / center / right；
 * 几列共用一个 key 时按小标题写，例如 { 主水: 'right', 客水: 'left' }。
 * 表头小标题跟着各自列对齐。用户填写表的「对齐方式」一列按「配置 key」回写本表。
 */
export const COLUMN_ALIGN: Record<WidthKey, Align | Record<string, Align>> = {
  'base.jcDay': 'left',
  'base.jcNo': 'left',
  'base.kickoff': 'left',
  'base.league': 'left',
  'base.home': 'right',
  'base.away': 'left',
  'base.score': 'center',
  'base.goals': 'center',
  'base.outcome': 'center',
  'pred.ah': 'center',
  'pred.ah.res': 'center',
  'pred.ou': 'center',
  'pred.ou.res': 'center',
  'pred.x1x2': 'center',
  'pred.x1x2.res': 'center',
  'pred.jc': 'center',
  'pred.jc.res': 'center',
  'res.ah': 'center',
  'res.ou': 'center',
  'res.x1x2': 'center',
  'ah.water': { 主水: 'right', 客水: 'left' },
  'ah.line': 'center',
  'ou.water': { 大球: 'right', 小球: 'left' },
  'ou.line': 'center',
  'x1x2.odds': { 胜: 'right', 平: 'center', 负: 'left' },
  'jc.res.had': 'center',
  'jc.res.hhad': 'center',
  'jc.had.odds': { 胜: 'right', 平: 'center', 负: 'left' },
  'jc.hhad.goal': 'center',
  'jc.hhad.odds': { 胜: 'right', 平: 'center', 负: 'left' },
}

/** 取对齐的样式类名（al-left / al-center / al-right）；sub 为共用 key 时的小标题 */
export function alignClass(k: WidthKey, sub?: string): string {
  const v = COLUMN_ALIGN[k]
  const a = typeof v === 'string' ? v : (sub && v[sub]) || 'center'
  return `al-${a}`
}
