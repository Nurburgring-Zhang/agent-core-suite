"""自验证门：候选评分、pivot 排序与门槛线校验（LLM-as-a-Verifier 降维实现）。

用法：
    python -X utf8 gate_verify_rank.py --record .acs/verify-record.json [--tier T3] [--schema <path>]

退出码：0=PASS，1=BLOCK，2=USAGE_ERROR（视为未验证=未完成）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _acs_common import (  # noqa: E402
    Report,
    TIER_SPEC,
    load_json,
    median,
    norm_tier,
    parse_args,
    spec_section,
    validate,
)

# 评分与排序的全部阈值来自 spec/thresholds.json（单一真相源）
_VR = spec_section("verify_rank")
PIVOT_K = _VR["pivot_k"]
SPREAD_LIMIT = _VR["spread_limit"]          # 同一候选同一标准的重复评分极差上限
WEIGHT_TOL = _VR["weight_tol"]
WEIGHTED_TOL = _VR["weighted_tol"]
MIN_CRITERIA = _VR["min_criteria"]          # 评价标准拆分下限（三维协同扩展之一）
T3_HARD_FLOOR = _VR["t3_hard_floor"]
# 趋势不升的连击阈值与 loop 门共用一个真相源，避免两处各写字面量后悄悄漂移
TREND_STRIKES = spec_section("loop")["spin_strikes"]

# 自一致性重采样：拿不到 logits 时，重复评分的分歧度是唯一可得的置信度代理
_SC = spec_section("self_consistency")
SC_TIERS = tuple(_SC["enabled_tiers"])
SC_GAP = _SC["trigger_score_gap"]
SC_EXTRA = _SC["extra_samples"]
SC_MAX_EXTRA = _SC["max_extra_samples"]

DEFAULT_SCHEMA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "templates", "verify-record.schema.json",
)


def check_criteria(record, tier, report):
    criteria = record.get("criteria") or []
    weights = record.get("weights") or []
    if len(criteria) != len(weights):
        report.error("weights", "criteria 数 %d 与 weights 数 %d 不一致" % (len(criteria), len(weights)))
    total = sum(w for w in weights if isinstance(w, (int, float)))
    if weights and abs(total - 1.0) > WEIGHT_TOL:
        report.error("weights", "权重和 %.4f != 1.0（权重必须在评分前锁定且合计为 1）" % total)
    if len(criteria) < MIN_CRITERIA:
        if tier in ("T2", "T3"):
            report.error("criteria", "只有 %d 项评价标准 < %d，评价标准拆分维度缺失" % (len(criteria), MIN_CRITERIA))
        else:
            report.warn("criteria", "只有 %d 项评价标准，建议拆到 %d 项" % (len(criteria), MIN_CRITERIA))
    return criteria, weights


def check_candidate(idx, cand, criteria, weights, repeats, report, expect_len=None):
    where = "candidates[%d](%s)" % (idx, cand.get("id"))
    scores = cand.get("scores") or []
    seen = {}
    for sidx, sc in enumerate(scores):
        cwhere = "%s.scores[%d](%s)" % (where, sidx, sc.get("criterion"))
        name = sc.get("criterion")
        if name in seen:
            report.error(cwhere, "标准 %s 重复打分" % name)
        seen[name] = sc
        if criteria and name not in criteria:
            report.error(cwhere, "标准 %r 未在 criteria 中声明" % name)
        values = sc.get("values") or []
        want = repeats
        if expect_len:
            want = expect_len.get((cand.get("id"), name), repeats)
        if len(values) != want:
            if want != repeats:
                report.error(cwhere, "重复评估次数 %d != repeats+extra_samples=%d（该标准已登记重采样）"
                             % (len(values), want))
            else:
                report.error(cwhere, "重复评估次数 %d != repeats=%d" % (len(values), want))
        if values:
            spread = max(values) - min(values)
            if spread > SPREAD_LIMIT:
                report.error(cwhere, "重复评分极差 %d > %d，说明标准描述不清，须先修 rubric 再重评" % (spread, SPREAD_LIMIT))
            real = median(values)
            if abs(float(sc.get("median", -1)) - real) > 1e-6:
                report.error(cwhere, "median=%s 与 values %s 的真实中位数 %s 不符" % (sc.get("median"), values, real))
        if not (sc.get("evidence") or "").strip():
            report.error(cwhere, "分数缺少证据，视为无效分")

    for name in criteria:
        if name not in seen:
            report.error(where, "缺少标准 %s 的评分" % name)

    medians = [seen[n].get("median") for n in criteria if n in seen and isinstance(seen[n].get("median"), (int, float))]
    if medians and len(set(medians)) == 1 and len(medians) > 1:
        report.warn(where, "所有标准得分相同（%s），疑未真正分项评估" % medians[0])

    if len(criteria) == len(weights) and len(medians) == len(criteria):
        expect = sum(weights[i] * float(seen[criteria[i]]["median"]) for i in range(len(criteria)))
        got = cand.get("weighted")
        if isinstance(got, (int, float)) and abs(got - expect) > WEIGHTED_TOL:
            report.error(where, "weighted=%s 与按权重重算值 %.2f 不符（禁止手改总分）" % (got, expect))
    return seen


def check_ranking(record, report):
    cands = record.get("candidates") or []
    ids = [c.get("id") for c in cands]
    ranking = record.get("ranking") or []
    if sorted(ranking) != sorted(ids):
        report.error("ranking", "ranking %s 与候选集合 %s 不是一一对应" % (ranking, ids))
        return
    weighted = dict((c.get("id"), c.get("weighted")) for c in cands)
    for i in range(1, len(ranking)):
        prev, cur = weighted.get(ranking[i - 1]), weighted.get(ranking[i])
        if isinstance(prev, (int, float)) and isinstance(cur, (int, float)):
            if cur > prev:
                report.error("ranking", "排序与分数矛盾：%s(%.2f) 排在 %s(%.2f) 之后"
                             % (ranking[i], cur, ranking[i - 1], prev))
            elif cur == prev:
                report.error("ranking", "%s 与 %s 加权总分平局（%.2f）未处置：必须追加区分性标准，禁止任选"
                             % (ranking[i - 1], ranking[i], cur))
    if record.get("winner") != ranking[0]:
        report.error("winner", "winner=%r 与 ranking[0]=%r 不一致" % (record.get("winner"), ranking[0]))


def check_threshold(record, tier, report):
    spec = TIER_SPEC[tier]
    cands = dict((c.get("id"), c) for c in (record.get("candidates") or []))
    win = cands.get(record.get("winner"))
    if not win:
        report.error("winner", "winner=%r 不在候选列表中" % record.get("winner"))
        return
    got = win.get("weighted")
    if isinstance(got, (int, float)) and got < spec["threshold"]:
        report.error("threshold", "冠军加权总分 %.2f < %s 级门槛 %.1f：必须返工/换方案/上报，禁止放宽门槛"
                     % (got, tier, spec["threshold"]))
    if tier == "T3":
        by_name = dict((s.get("criterion"), s.get("median")) for s in (win.get("scores") or []))
        for name, floor in sorted(T3_HARD_FLOOR.items()):
            val = by_name.get(name)
            if val is None:
                report.error("threshold", "T3 缺少硬底线标准 %s 的评分" % name)
            elif float(val) < floor:
                report.error("threshold", "T3 硬底线未达：%s=%.1f < %.1f" % (name, float(val), floor))


def check_scale(record, tier, report):
    spec = TIER_SPEC[tier]
    cands = record.get("candidates") or []
    if len(cands) < spec["n"]:
        report.error("candidates", "%s 级要求至少 %d 个候选，实际 %d 个" % (tier, spec["n"], len(cands)))
    if record.get("repeats", 0) < spec["r"]:
        report.error("repeats", "%s 级要求重复评估 ≥%d 次，实际 %s" % (tier, spec["r"], record.get("repeats")))
    if tier == "T3":
        pivots = record.get("pivots") or []
        if len(pivots) != PIVOT_K:
            report.error("pivots", "T3 必须用 k=%d 个 pivot 做近似排序（O(Nk)），实际 %d 个" % (PIVOT_K, len(pivots)))
        ids = set(c.get("id") for c in cands)
        for pid in pivots:
            if pid not in ids:
                report.error("pivots", "pivot %r 不在候选列表中" % pid)
    if record.get("builder") and record.get("builder") == record.get("verifier"):
        report.error("verifier", "builder == verifier == %r，验证者必须独立于执行者" % record.get("builder"))
    if not (record.get("winner_reason") or "").strip():
        report.error("winner_reason", "未给出选优理由")


def check_trend(record, report):
    trend = record.get("progress_trend") or []
    streak = 0
    for i in range(1, len(trend)):
        if trend[i] <= trend[i - 1]:
            streak += 1
            if streak >= TREND_STRIKES:
                report.error("progress_trend", "验证分数连续 %d 次不升（... %s → %s），方向已偏离"
                             % (streak, trend[i - 1], trend[i]))
        else:
            streak = 0


def _sc_obj(record):
    sc = record.get("self_consistency")
    return sc if isinstance(sc, dict) else None


def _top_gap(record):
    """返回（冠亚加权分差, 前两名 id 列表）；无法计算时返回 (None, [])。"""
    ranking = record.get("ranking") or []
    if len(ranking) < 2:
        return None, []
    weighted = dict((c.get("id"), c.get("weighted")) for c in (record.get("candidates") or []))
    a, b = weighted.get(ranking[0]), weighted.get(ranking[1])
    if not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        return None, ranking[:2]
    return float(a) - float(b), ranking[:2]


def resample_expectation(record):
    """已登记的重采样计划 → {(cand_id, criterion): 应有样本数}。

    重采样必须真的多出样本，否则 triggered=true 只是一句口号。
    """
    sc = _sc_obj(record)
    if not sc or sc.get("triggered") is not True:
        return {}
    extra = sc.get("extra_samples")
    if not isinstance(extra, int) or isinstance(extra, bool) or extra <= 0:
        return {}
    repeats = record.get("repeats") or 0
    _gap, top2 = _top_gap(record)
    plan = {}
    for cid in top2:
        for name in (sc.get("resampled") or []):
            plan[(cid, name)] = repeats + extra
    return plan


def check_self_consistency(record, tier, criteria, report):
    """智能触发的自一致性重采样（弥补拿不到 logits 的降维）。

    平局已由 check_ranking 拦住；这里管的是「没平局但差得比噪声还小」那一段 ——
    同一份 rubric 重复评分本就有抖动，分差小于阈值时直接定案就是把噪声当结论。
    重采样花 token，所以只在真可能改判的区间强制，不无脑全量采样。
    """
    sc = _sc_obj(record)

    # 自相矛盾先拦，与分级无关：没触发却填了采样计划，两个字段至少有一个是假的
    if sc and sc.get("triggered") is not True:
        if sc.get("extra_samples") or sc.get("resampled"):
            report.error("self_consistency",
                         "triggered 非 true 却填了 extra_samples/resampled，两个字段至少有一个是假的")

    triggered = bool(sc and sc.get("triggered") is True)

    if tier not in SC_TIERS:
        if triggered:
            report.note("self_consistency：%s 级不强制重采样，主动加采样属多花 token 但不违规" % tier)
        return

    gap, top2 = _top_gap(record)
    if gap is None:
        if len(top2) < 2:
            report.note("self_consistency：候选不足 2 个，无冠亚分差可判")
        return

    if gap >= SC_GAP:
        if triggered:
            report.warn("self_consistency",
                        "冠亚分差 %.2f ≥ %.1f 本不需重采样，仍触发属主动多花 token" % (gap, SC_GAP))
        return

    if not triggered:
        report.error("self_consistency",
                     "冠亚加权分差 %.2f < %.1f，落在可能改判区间：拿不到 logits，"
                     "重复评分的分歧度是唯一可得的置信度代理，必须先重采样再定案"
                     % (gap, SC_GAP))
        return

    extra = sc.get("extra_samples")
    if not isinstance(extra, int) or isinstance(extra, bool) or extra < SC_EXTRA or extra > SC_MAX_EXTRA:
        report.error("self_consistency",
                     "extra_samples=%r 必须是 %d~%d 之间的整数（少了不够去噪，多了只是烧 token）"
                     % (sc.get("extra_samples"), SC_EXTRA, SC_MAX_EXTRA))

    resampled = sc.get("resampled") or []
    if not resampled:
        report.error("self_consistency", "triggered=true 却没有 resampled：重采样必须落到具体标准上")
    for name in resampled:
        if criteria and name not in criteria:
            report.error("self_consistency", "resampled 含未在 criteria 声明的标准 %r" % name)

    if not (sc.get("reason") or "").strip():
        report.error("self_consistency", "缺 reason：哪些标准最可能改判、为何重采，必须写清")

    declared = sc.get("gap")
    if isinstance(declared, (int, float)) and abs(float(declared) - gap) > 0.01:
        report.error("self_consistency",
                     "gap=%s 与按 weighted 重算的冠亚分差 %.2f 不符（禁止手改）" % (declared, gap))


def main(argv):
    args = parse_args(argv, {"--record": "record", "--tier": "tier", "--schema": "schema"}, ["record"])
    record = load_json(args["record"], "verify-record")
    tier = norm_tier(args.get("tier") or record.get("tier"), default="T2")
    schema = load_json(args.get("schema") or DEFAULT_SCHEMA, "schema")

    report = Report("gate_verify_rank (自验证评分与排序)")
    report.note("record=%s tier=%s" % (args["record"], tier))

    for err in validate(record, schema, "$"):
        report.error("schema", err)

    if tier == "T0":
        report.note("T0 不启用自验证扩展。")
        return report.finish()

    criteria, weights = check_criteria(record, tier, report)
    repeats = record.get("repeats") or 0
    expect_len = resample_expectation(record)
    for idx, cand in enumerate(record.get("candidates") or []):
        check_candidate(idx, cand, criteria, weights, repeats, report, expect_len)
    check_ranking(record, report)
    check_scale(record, tier, report)
    check_threshold(record, tier, report)
    check_self_consistency(record, tier, criteria, report)
    check_trend(record, report)
    return report.finish()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
