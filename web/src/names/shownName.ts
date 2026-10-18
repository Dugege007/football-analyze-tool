/**
 * 赛程页和数据表共用的常用名。
 * 规范名优先；没有规范名时用来源名。两边都再走 leagueShort / teamCommon。
 */
import { leagueShort } from './leagueShort.js'
import { teamCommon } from './teamName.js'

/** 赛事格子上的常用名。 */
export function shownLeague(source: string | null | undefined, canonical?: string | null): string | null {
  return leagueShort(canonical ?? source)
}

/**
 * 队名格子上的常用名。
 * 联赛范围用赛事常用名，和数据表主客队列一致。
 */
export function shownTeam(
  source: string | null | undefined,
  canonical: string | null | undefined,
  leagueSource: string | null | undefined,
  leagueCanonical?: string | null,
): string | null {
  return teamCommon(source, canonical, shownLeague(leagueSource, leagueCanonical))
}
