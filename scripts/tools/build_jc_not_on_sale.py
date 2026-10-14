#!/usr/bin/env python3
"""从竞彩官网赛果缓存（MSI bundle）生成「竞彩未开售胜平负」名单 CSV。只读数据库，不写库。

判断依据：bundle 的 matchResult 里胜平负赔率 h、d、a 全为空，且比赛不是退款或无效场次。
场次对应：bundle 的 matchNumStr（例如「周二008」）去掉「周」得到 jc_id，matchDate 与本库竞彩日相差 0 或 1 天，
用 matches.match_uid（竞彩日|jc_id）对上；对不上的行写入 CSV 但 match_uid 为空。
用法：python build_jc_not_on_sale.py --bundle latest_bundle.json --db app.db --out jc_not_on_sale.csv
"""
import argparse, csv, json, sqlite3
from datetime import date, timedelta


def rows_from_bundle(bundle: dict) -> list[dict]:
    out = []
    for page in bundle.get("pages") or []:
        v = (json.loads(page) if isinstance(page, str) else page).get("value") or {}
        out.extend(v.get("matchResult") or [])
    return out


def not_on_sale(m: dict) -> bool:
    if m.get("poolStatus") == "Refund" or "无效" in (m.get("sectionsNo999") or ""):
        return False
    return all((m.get(k) or "").strip() == "" for k in ("h", "d", "a"))


def build(bundle: dict, conn: sqlite3.Connection) -> list[dict]:
    res = []
    for m in rows_from_bundle(bundle):
        if not not_on_sale(m):
            continue
        jc_id = (m.get("matchNumStr") or "").replace("周", "")
        md = date.fromisoformat(m["matchDate"])
        uid = None
        for d in (md, md - timedelta(days=1)):
            r = conn.execute("select match_uid from matches where jc_id=? and jingcai_date=?", (jc_id, d.isoformat())).fetchone()
            if r:
                uid = r[0]
                break
        res.append({"match_uid": uid, "sporttery_match_id": m.get("matchId"), "match_num_str": m.get("matchNumStr"),
                    "match_date": m.get("matchDate"), "goal_line": m.get("goalLine"),
                    "basis": "MSI 赛果缓存胜平负赔率为空", "bundle_fetched_at": bundle.get("fetched_at")})
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True); ap.add_argument("--db", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    conn = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    rows = build(json.load(open(a.bundle, encoding="utf-8")), conn)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["match_uid", "sporttery_match_id", "match_num_str", "match_date", "goal_line", "basis", "bundle_fetched_at"])
        w.writeheader(); w.writerows(rows)
    print(f"{len(rows)} rows -> {a.out}")


if __name__ == "__main__":
    main()
