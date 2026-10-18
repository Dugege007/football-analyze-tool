/**
 * 赛程页、当日消息预览和比赛详情页的赛事名、队名显示。
 * 优先读 GET /matches 的规范名（扁平字段与嵌套字段同值），再经 shownLeague / shownTeam 显示。
 * 这两个函数与数据表赛事、主队、客队列相同。规范名字段缺席时才用来源文本。
 * 接口在没有规范名时已把该字段回退成来源文本，页面不再另造名称。
 * 悬停别名赛事走 leagueAliasTip，队名走 teamAliasTip，都是一行一个、不加前缀。
 * 球队待确认走 isTeamPending，优先用接口布尔值。
 * 赛事待确认走 isLeaguePending，优先用 league_pending_confirm。已升格的常用名不标星，临时名继续标星。
 */
import type { Match } from '../api/types'
import { leagueAliasTip } from './leagueShort.js'
import { isLeaguePending, leagueCanonicalForPending } from './leaguePending.js'
import { shownLeague, shownTeam } from './shownName.js'
import { isTeamPending, teamAliasTip } from './teamName.js'

/** 一支球队在页面上的显示结果。 */
export interface TeamLabel {
  /** 页面上显示的常用中文名。没有队名时为 null，由调用方显示占位符。 */
  text: string | null
  /** 悬停时列出的别名，一行一个。没有别名时为 undefined，不设置 Tooltip。 */
  tip: string | undefined
  /** 为 true 时在队名旁显示待确认星号。 */
  pending: boolean
}

/** 一场比赛在赛程页、消息预览和详情页上的赛事名与主客队名。 */
export interface MatchLabels {
  /** 赛事简称。没有赛事名时为 null。 */
  league: string | null
  /** 悬停时列出的赛事别名，一行一个。没有别名时为来源名。与数据表赛事列相同。 */
  leagueTitle: string | undefined
  /** 为 true 时在赛事名旁显示待确认星号。 */
  leaguePending: boolean
  home: TeamLabel
  away: TeamLabel
}

/**
 * 扁平规范名优先；扁平字段缺席时用嵌套同值字段。
 * 两者都缺席时返回 undefined，调用方才回到来源文本。
 */
function preferCanonical(
  flat: string | null | undefined,
  nested: string | null | undefined,
): string | null | undefined {
  return flat !== undefined ? flat : nested
}

/** 扁平待确认优先；缺席时用嵌套同值布尔。null 与缺席都留给名称兜底。 */
function preferPending(
  flat: boolean | null | undefined,
  nested: boolean | null | undefined,
): boolean | null | undefined {
  return flat !== undefined ? flat : nested
}

function teamLabel(
  name: string | null | undefined,
  canonical: string | null | undefined,
  leagueSource: string | null | undefined,
  leagueCanonical: string | null | undefined,
  apiPending: boolean | null | undefined,
): TeamLabel {
  const leagueDisplay = shownLeague(leagueSource, leagueCanonical)
  return {
    text: shownTeam(name, canonical, leagueSource, leagueCanonical),
    tip: teamAliasTip(name, canonical, leagueDisplay),
    pending: isTeamPending(name, canonical, apiPending),
  }
}

/** 按数据表同一套规则，算出这一场在赛程页、消息预览和详情页上的赛事名与主客队名。 */
export function matchLabels(match: Match): MatchLabels {
  const sourceLeague = match.competition?.name
  const leagueCanonical = preferCanonical(match.league_canonical, match.competition?.canonical)
  const league = shownLeague(sourceLeague, leagueCanonical)
  return {
    league,
    leagueTitle: leagueAliasTip(league, sourceLeague, [sourceLeague, leagueCanonical]),
    leaguePending: isLeaguePending(
      sourceLeague,
      leagueCanonicalForPending(leagueCanonical, match.league_id),
      preferPending(match.league_pending_confirm, match.competition?.league_pending_confirm),
    ),
    home: teamLabel(
      match.teams.home,
      preferCanonical(match.home_team_canonical, match.teams.home_canonical),
      sourceLeague,
      leagueCanonical,
      preferPending(match.home_team_pending_confirm, match.teams_pending_confirm?.home),
    ),
    away: teamLabel(
      match.teams.away,
      preferCanonical(match.away_team_canonical, match.teams.away_canonical),
      sourceLeague,
      leagueCanonical,
      preferPending(match.away_team_pending_confirm, match.teams_pending_confirm?.away),
    ),
  }
}
