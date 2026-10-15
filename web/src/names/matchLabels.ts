/**
 * 赛程页和比赛详情页的赛事名、队名显示。
 * 计算规则与数据表相同：赛事走 leagueShort，队名走 teamCommon，悬停走 teamAliasTip，待确认走 isTeamPending。
 * 列表和详情接口的比赛对象没有规范名，只用来源名；待确认优先用接口返回的 teams_pending_confirm。
 */
import type { Match } from '../api/types'
import { leagueShort } from './leagueShort'
import { isTeamPending, teamAliasTip, teamCommon } from './teamName'

/** 一支球队在页面上的显示结果。 */
export interface TeamLabel {
  /** 页面上显示的常用中文名。没有队名时为 null，由调用方显示占位符。 */
  text: string | null
  /** 悬停时列出的其他名称。没有别名时为 undefined，不设置 title。 */
  tip: string | undefined
  /** 为 true 时在队名旁显示待确认星号。 */
  pending: boolean
}

/** 一场比赛在赛程页和详情页上的赛事名与主客队名。 */
export interface MatchLabels {
  /** 赛事简称。没有赛事名时为 null。 */
  league: string | null
  /** 悬停时显示的赛事来源名，与数据表赛事列的 title 相同。 */
  leagueTitle: string | undefined
  home: TeamLabel
  away: TeamLabel
}

function teamLabel(
  name: string | null | undefined,
  leagueDisplay: string | null,
  apiPending: boolean | null | undefined,
): TeamLabel {
  return {
    text: teamCommon(name, null, leagueDisplay),
    tip: teamAliasTip(name, null, leagueDisplay),
    pending: isTeamPending(name, null, apiPending),
  }
}

/** 按数据表同一套规则，算出这一场在赛程页和详情页上的赛事名与主客队名。 */
export function matchLabels(match: Match): MatchLabels {
  const sourceLeague = match.competition?.name
  const league = leagueShort(sourceLeague)
  return {
    league,
    leagueTitle: sourceLeague || undefined,
    home: teamLabel(match.teams.home, league, match.teams_pending_confirm?.home),
    away: teamLabel(match.teams.away, league, match.teams_pending_confirm?.away),
  }
}
