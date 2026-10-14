"""原因代码 → 悬停用中文说明（2026-10-14 分析师批准：前端不再维护对照表，接口同时返回中文）。

规则：响应里每个以 reason 结尾、值为字符串或空值的键，旁边都加一个同名加 _zh 的键（例如 null_reason → null_reason_zh）。
值为空时 _zh 也为空；代码不在表里时 _zh 写「未登记的原因：代码」，测试保证接口实际产出的代码都已登记。
"""
from __future__ import annotations

import csv
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

REASON_ZH: dict[str, str] = {
    # 格子状态（cell_status_reason）
    "not_due": "规定采集时刻还没到",
    "book_not_open": "机构还没开出这个盘",
    "no_source": "这家机构这个盘种没有可用来源",
    "not_captured": "时刻已过但没有采到，补数也没补到",
    "suspect_truncated": "初盘疑似被截断，不可信",
    "too_few_books": "参与平均的机构少于两家",
    "deviation_too_large": "盘口偏离多数盘口三档及以上，已剔除",
    # 竞彩格子与竞彩赛果
    "collector_empty": "采集端没有数据",
    "legacy_home_only": "手工记录只有主胜赔率，不完整",
    "jc_1x2_incomplete": "竞彩胜平负三项赔率不全",
    "jc_not_on_sale": "竞彩未开售胜平负",
    "probe_not_sporttery_official": "探测数据不是竞彩官方来源",
    "no_result": "没有比分或赛果还不可见",
    "special_status": "退款、推迟、取消或腰斩",
    "no_close_odds": "没有完整的临盘赔率",
    "no_goal_line": "让球胜平负没有让球数",
    # 隐藏原因
    "no_data": "没有数据",
    "before_kickoff": "比赛还没开始",
    "not_visible_yet": "赛果还不可见",
    "refund": "竞彩退款场次",
    "postponed": "比赛推迟",
    "cancelled": "比赛取消",
    "abandoned": "比赛腰斩",
    "no_prediction": "这场没有预测",
    # 初盘依据与截断
    "no_open_data": "没有初盘数据",
    "open_time_unknown_after_decision_possible": "初盘时间未知，可能晚于决策时刻",
    "probe_match": "探测场次，按探测结果判断",
    "no_group_for_book": "这家机构没有截断分位数分组",
    "jc_no_quantile_group": "竞彩没有截断分位数分组",
    # 人工复核
    "ah_sign_mismatch_probe": "亚盘方向与探测结果不一致，待人工复核",
    # 基本面
    "no_team_id": "球队编号无法对上",
    "extra_time_unverified": "统计窗口含加时或点球比赛，90 分钟比分未核实",
    "post_match_only": "只有赛后数据，不能使用",
    "empty_from_source": "来源返回为空",
    "not_collected": "还没有采集",
    # 2026-10-14 补登记：基本面合同核对清单与第一期合同 7.7 节
    "source_not_covered": "来源不收录这场比赛",
    "no_standings": "这项赛事没有积分榜",
    "no_pinnacle_1x2": "没有平博欧赔",
    "injury_list_empty_unconfirmed": "伤停名单为空，未确认是否无伤停",
    "home_not_due": "主队尚未到取数时间",
    "away_not_due": "客队尚未到取数时间",
    "conversion_failed": "这家机构的水位无法换算到统一盘口，已剔除",
    # 2026-10-14 补登记：接口实际会返回的其余原因
    "after_as_of": "这一格的记录时刻晚于所选的查看时刻，已隐藏",
    "jc_official_first_quote_missing": "没有竞彩官方首开赔率，自采的首次赔率只作参考，不当作初盘",
    "no_line_visible_at_decision": "决策时刻还看不到可用的让球线",
    "quantile_table_missing": "缺少截断分位数表，没有做截断检查",
    "first_record_or_kickoff_unknown": "首条记录时刻或开赛时刻未知，没有做截断检查",
    "own_capture_out_of_window": "自采时刻不在十一点到十一点二十分之间，主值改用时间线数据",
    "own_capture_out_of_window;no_timeline": "自采时刻不在十一点到十一点二十分之间，而且没有时间线数据可用",
    "produced_after_as_of": "预测产出时刻晚于所选的查看时刻，已隐藏",
    "kickoff_unknown": "开赛时刻未知，赛果暂不显示",
    "no_settle_line": "没有可用于结算的盘口，无法结算",
    "bad_direction": "预测方向无法识别，无法结算",
    # 影子策略不可评估子原因（验证与对比接口的 not_evaluable_items 会返回）
    "fit_not_converged": "模型拟合没有收敛，这场不可评估",
    "real_water_lt_3": "有真实水位的机构不足三家，这场不可评估",
    "stale_tick": "最后一次报价距离目标时点超过六十分钟，报价过旧，这场不可评估",
}


def zh(code: Any) -> str | None:
    if code is None or not isinstance(code, str) or code == "":
        return None
    return REASON_ZH.get(code, f"未登记的原因：{code}")


def annotate(obj: Any) -> Any:
    """原地给所有 *reason 键补 *_reason_zh；返回同一对象。"""
    if isinstance(obj, dict):
        for k in list(obj.keys()):
            v = obj[k]
            if k.endswith("reason") and (v is None or isinstance(v, str)):
                obj[k + "_zh"] = zh(v)
            elif isinstance(v, (dict, list)):
                annotate(v)
    elif isinstance(obj, list):
        for x in obj:
            annotate(x)
    return obj


def not_on_sale_path() -> Path:
    p = os.environ.get("JC_NOT_ON_SALE_CSV")
    if p:
        return Path(p)
    from app.db import _ODDS_DATA_DIR  # 与其他 odds-data 文件同一根目录
    return Path(_ODDS_DATA_DIR) / "sporttery" / "jc_not_on_sale.csv"


def load_jc_not_on_sale(path: Path | None = None) -> set[str]:
    """竞彩未开售胜平负的场次（match_uid 集合）。文件缺失时返回空集并记日志。"""
    p = path or not_on_sale_path()
    if not p.exists():
        log.warning("jc_not_on_sale csv missing: %s", p)
        return set()
    with p.open(encoding="utf-8") as f:
        return {r["match_uid"] for r in csv.DictReader(f) if r.get("match_uid")}


def apply_jc_not_on_sale(item: dict, uids: set[str]) -> None:
    """名单内的场次：竞彩胜平负空格子与竞彩胜平负赛果的原因改为 jc_not_on_sale。"""
    if item.get("match_id") not in uids:
        return
    for cell in (item.get("jc_1x2") or {}).values():
        if isinstance(cell, dict) and not cell.get("available") and "missing_reason" in cell:
            cell["missing_reason"] = "jc_not_on_sale"
            if "cell_status_reason" in cell and cell.get("cell_status") != "ok":
                cell["cell_status_reason"] = "jc_not_on_sale"
    had = (((item.get("result") or {}).get("jc") or {}).get("had"))
    if isinstance(had, dict) and had.get("null_reason") in ("no_close_odds", "collector_empty"):
        had["null_reason"] = "jc_not_on_sale"
