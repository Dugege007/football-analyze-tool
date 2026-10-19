/**
 * 赛程页、当日消息预览、比赛详情页和数据表的赛事名、队名显示。
 * 优先读规范名（扁平字段与嵌套字段同值），再经 shownLeague / shownTeam 显示。
 * 规范名字段缺席时才用来源文本。
 * 赛程接口在没有常用名时已把该字段回退成来源文本，两边都空时再回退成名称组里的默认显示。
 * 数据表在来源文本还有字时把规范名留空，页面再回到来源文本；两边都空时接口写入组内默认显示。
 * 悬停别名赛事走 leagueAliasTip，队名走 teamAliasTip，都是一行一个、不加前缀。
 * 球队待确认走 isTeamPending，优先用接口布尔值。字段缺席时，双方名都空则标星，否则只对已知临时名标星。
 * 赛事待确认走 isLeaguePending，优先用 league_pending_confirm。已升格的常用名不标星；临时名、还没有常用名的来源名，以及双方名都空时标星。
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

/** 数据表一行上与名称有关的字段。规范名留空时表示接口没有给出常用名。 */
export interface TableNameFields {
  league?: string | null
  league_canonical?: string | null
  league_pending_confirm?: boolean | null
  league_id?: number | string | null
  home_team?: string | null
  away_team?: string | null
  home_team_canonical?: string | null
  away_team_canonical?: string | null
  home_team_pending_confirm?: boolean | null
  away_team_pending_confirm?: boolean | null
}

/**
 * 数据表赛事、主队、客队的显示结果。
 * 有规范名时用规范名，没有规范名时用来源文本。接口给出待确认布尔值时以接口为准；缺席时走名称兜底。
 */
export function labelsForTableMatch(fields: TableNameFields): MatchLabels {
  return matchLabels({
    date: '',
    scope: 'jingcai',
    jc: null,
    competition: { name: fields.league ?? undefined },
    league_canonical: fields.league_canonical,
    league_pending_confirm: fields.league_pending_confirm,
    league_id: fields.league_id,
    teams: {
      home: fields.home_team ?? '',
      away: fields.away_team ?? '',
    },
    home_team_canonical: fields.home_team_canonical,
    away_team_canonical: fields.away_team_canonical,
    home_team_pending_confirm: fields.home_team_pending_confirm,
    away_team_pending_confirm: fields.away_team_pending_confirm,
  })
}
