/**
 * 数据表（/sheet）全部列宽，单位像素。页面统一从这里读宽度。
 * 用户填写表：web/docs/sheet-column-widths.xlsx（同内容 CSV），按「配置 key」一列回写本表。
 */
export const COLUMN_WIDTHS = {
  // 基本九列（所有标签，固定在左侧）
  'base.jcDay': 104, // 竞彩日
  'base.jcNo': 88, // 编号
  'base.kickoff': 96, // 开赛时间
  'base.league': 96, // 赛事
  'base.home': 88, // 主队
  'base.away': 88, // 客队
  'base.score': 58, // 比分
  'base.goals': 66, // 总进球
  'base.outcome': 56, // 赛果
  // 预测组
  'pred.ah': 96, // 亚盘预测
  'pred.ah.res': 96, // 亚盘预测的结果
  'pred.ou': 86, // 大小预测
  'pred.ou.res': 64, // 大小预测的结果
  'pred.x1x2': 86, // 欧盘预测
  'pred.x1x2.res': 64, // 欧盘预测的结果
  'pred.jc': 86, // 竞彩预测
  'pred.jc.res': 64, // 竞彩预测的结果
  // 机构赛果列（多家平均、澳门、皇冠、平博、威廉、Bet365）
  'res.ah': 92, // 亚盘赛果
  'res.ou': 92, // 大小赛果
  'res.x1x2': 92, // 欧盘赛果
  // 机构盘口：临、中、初三个阶段共用
  'ah.water': 52, // 主水、客水
  'ah.line': 58, // 亚盘盘口
  'ou.water': 52, // 大球、小球
  'ou.line': 58, // 大小球盘口
  'x1x2.odds': 52, // 欧赔胜、平、负
  // 竞彩
  'jc.res.had': 100, // 胜平负赛果
  'jc.res.hhad': 118, // 让球胜平负赛果
  'jc.had.odds': 52, // 胜平负临、中、初盘的胜、平、负
  'jc.hhad.goal': 50, // 让球胜平负临、中、初盘的让球
  'jc.hhad.odds': 52, // 让球胜平负临、中、初盘的胜、平、负
} as const

export type WidthKey = keyof typeof COLUMN_WIDTHS
export const w = (k: WidthKey): number => COLUMN_WIDTHS[k]
