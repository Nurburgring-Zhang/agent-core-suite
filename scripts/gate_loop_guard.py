"""Loop 门：四道成本闸门 + 停止协议 + 安全阀校验。

对症实测根因：单步目标过重、完成标准不清晰、上下文反复回灌、空转。

用法：
    python -X utf8 gate_loop_guard.py --state .acs/task-state.json [--tier T2]

退出码：0=PASS，1=BLOCK，2=USAGE_ERROR（视为未验证=未完成）

v1.1 增强（spec/retry 节）：有界重试——retries 超上限拦；重试必须留痕
    （retry_reason）并声明打法变化（delta_from_last），同法重试即空转。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _acs_common import Report, TIER_SPEC, load_json, norm_tier, parse_args, spec_section  # noqa: E402

# 硬阈值全部来自 spec/thresholds.json（单一真相源），本文件不得自带数字。
_LOOP = spec_section("loop")
MAX_OUTPUTS_PER_STEP = _LOOP["max_outputs_per_step"]
MAX_BUDGET_MIN = _LOOP["max_budget_min"]
MAX_CONTEXT_BYTES = _LOOP["max_context_bytes"]
MAX_FILES_READ = _LOOP["max_files_read"]
MAX_SUMMARY_CHARS = _LOOP["max_summary_chars"]
MAX_THINK_RATIO = _LOOP["max_think_ratio"]
SPIN_STRIKES = _LOOP["spin_strikes"]
SUMMARY_CHARS_TOLERANCE = _LOOP["summary_chars_tolerance"]

# 缺字段 = 无法计量 = 等于绕过闸门，故必须显式存在（与 task-state.schema.json 的 steps.required 一致）
REQUIRED_STEP_FIELDS = tuple(_LOOP["required_step_fields"])

# 完成标准里的空话：出现即视为「完成标准不清晰」
VAGUE_ACCEPTANCE = tuple(spec_section("vague_phrases")["acceptance"])
# 证据里的空话：evidence_ref 写成这些词等于没给证据
VAGUE_EVIDENCE = tuple(spec_section("vague_phrases")["evidence"])

# token 计量：proxy 是降维产物，能拿到真账就必须回填真账并交代来源
_CM = spec_section("cost_metering")
CM_SOURCES = tuple(_CM["sources"])
CM_AUDITABLE = tuple(_CM["auditable_sources"])
CM_DECLARE_TIERS = tuple(_CM["declare_required_tiers"])
CM_MIN_EVIDENCE = _CM["min_evidence_chars"]
CM_MAX_ACTUAL = _CM["max_tokens_actual_by_tier"]

# 有界重试（v1.1）：同法重试是空转的另一种形态，必须有上限、留痕与打法变化
_RETRY = spec_section("retry")
RT_MAX_RETRIES = _RETRY["max_retries"]
RT_MIN_REASON_CHARS = _RETRY["min_reason_chars"]
RT_MIN_DELTA_CHARS = _RETRY["min_delta_chars"]

# handoff（v2.0）：每轮压缩 + 保留目标防跑偏。每步写 ≤1000 字交接（目标/已完成/下一步），
# 下一步只读它不重放历史；目标锚点每轮复述以防长程跑偏。无损=只压过程不压结论/证据/目标。
_HANDOFF = spec_section("handoff")
HO_MAX_CHARS = _HANDOFF["max_handoff_chars"]
HO_REQUIRED_SECTIONS = tuple(_HANDOFF["required_sections"])
HO_MIN_SECTION_CHARS = _HANDOFF["min_section_chars"]


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def check_cost_metering(state, tier, report):
    """token 计量来源核查：proxy 可以是代理，但不许冒充计量，也不许不交代。

    三条不可越的线：
      1. T2+ 必须显式声明来源 —— 拿不到真账也要写 unavailable，沉默不算声明。
      2. 声称来源可审计（api_usage / cli_usage）就必须给出数字 + 可复查证据。
      3. 来源不可审计时不得出现 tokens_actual —— 字段名本身就是诚实声明。
    """
    cm = state.get("cost_metering")
    if not isinstance(cm, dict):
        if tier in CM_DECLARE_TIERS:
            report.error("cost_metering",
                         "%s 级必须声明 cost_metering.source：不交代计量来源，proxy 数字就无从审计"
                         "（真拿不到就显式写 source=unavailable，沉默不算声明）" % tier)
        return

    src = cm.get("source")
    if src not in CM_SOURCES:
        report.error("cost_metering", "source=%r 不在允许来源 %s 内" % (src, list(CM_SOURCES)))
        return

    actual = cm.get("tokens_actual")
    has_actual = _is_int(actual)
    ev = (cm.get("evidence_ref") or "").strip()

    if src in CM_AUDITABLE:
        if not has_actual:
            report.error("cost_metering",
                         "source=%s 声称可审计却没有 tokens_actual：声称拿到真账就必须回填数字" % src)
        if len(ev) < CM_MIN_EVIDENCE:
            report.error("cost_metering",
                         "source=%s 必须给出 evidence_ref（usage 落盘路径或取数命令），当前 %r 不足 %d 字"
                         % (src, cm.get("evidence_ref"), CM_MIN_EVIDENCE))
        else:
            low = ev.lower()
            for bad in VAGUE_EVIDENCE:
                if bad in ev or bad in low:
                    report.error("cost_metering",
                                 "evidence_ref 是空话 %r：要的是可复查的路径或命令，不是结论" % bad)
                    break
    elif has_actual:
        report.error("cost_metering",
                     "source=%s 不可审计却填了 tokens_actual=%d：请退回 *_proxy 字段，"
                     "字段名带后缀就是为了不冒充真实计量" % (src, actual))

    if src == "unavailable" and ev:
        report.warn("cost_metering", "source=unavailable 却给了 evidence_ref，二者矛盾（按无计量处理）")

    ins, outs = cm.get("input_tokens_actual"), cm.get("output_tokens_actual")
    if has_actual and _is_int(ins) and _is_int(outs) and ins + outs != actual:
        report.error("cost_metering",
                     "input+output=%d != tokens_actual=%d，分项与总量不自洽（计量必须能对上账）"
                     % (ins + outs, actual))

    # 回填真账的意义就在这里：有真账就按真账拦，不再只受 proxy 闸门约束
    cap = CM_MAX_ACTUAL.get(tier)
    if has_actual and _is_int(cap) and actual > cap:
        report.error("cost_metering",
                     "tokens_actual=%d > %s 级上限 %d，成本已失控（反面基线：同框架对照模型 9 个任务共 956,630 token）"
                     % (actual, tier, cap))


def check_safety_valve(state, tier, report):
    valve = state.get("safety_valve")
    if tier in ("T0",):
        return
    if not isinstance(valve, dict):
        report.error("safety_valve", "未声明安全阀（max_rounds/max_requests_proxy/max_minutes/on_break），循环无上限")
        return
    spec = TIER_SPEC[tier]
    for field in ("max_rounds", "max_minutes"):
        if not isinstance(valve.get(field), int) or isinstance(valve.get(field), bool):
            report.error("safety_valve", "%s 缺失或非整数（%r）：无上限等于没有安全阀" % (field, valve.get(field)))
    if valve.get("max_rounds", 0) > spec["max_rounds"]:
        report.error("safety_valve", "max_rounds=%s 超过 %s 级上限 %d" % (valve.get("max_rounds"), tier, spec["max_rounds"]))
    if valve.get("max_minutes", 0) > spec["max_minutes"]:
        report.error("safety_valve", "max_minutes=%s 超过 %s 级上限 %d" % (valve.get("max_minutes"), tier, spec["max_minutes"]))
    steps = state.get("steps", []) or []
    if valve.get("max_rounds") and len(steps) > valve["max_rounds"]:
        report.error("safety_valve", "已执行 %d 步 > max_rounds=%s，安全阀已触发却未记录中断处置"
                     % (len(steps), valve["max_rounds"]))


def check_step(idx, step, tier, report):
    where = "steps[%d](%s)" % (idx, step.get("id"))

    # 计量字段必须存在：否则下方所有 isinstance 检查会因字段缺失而静默通过
    for field in REQUIRED_STEP_FIELDS:
        if field not in step:
            report.error(where, "缺少计量字段 %s：无法计量即等于绕过闸门，不得省略" % field)

    # 窄步闸
    outs = step.get("outputs") or []
    if len(outs) > MAX_OUTPUTS_PER_STEP:
        report.error(where, "单步声明 %d 个产出（上限 %d），违反窄步闸，必须拆分" % (len(outs), MAX_OUTPUTS_PER_STEP))
    if not outs:
        report.error(where, "单步没有任何产出，不是有效工作单元")
    budget = step.get("budget_min")
    if isinstance(budget, int) and budget > MAX_BUDGET_MIN:
        report.error(where, "budget_min=%d > %d 分钟，单步目标过重" % (budget, MAX_BUDGET_MIN))

    # 完成标准必须可机检
    acc = step.get("acceptance") or []
    if not acc:
        report.error(where, "缺少 acceptance，完成标准不清晰")
    for aidx, item in enumerate(acc):
        val = (item.get("value") or "") if isinstance(item, dict) else ""
        kind = (item.get("kind") or "") if isinstance(item, dict) else ""
        if kind == "cmd" and "expect_exit" not in (item or {}):
            report.error("%s.acceptance[%d]" % (where, aidx), "kind=cmd 必须给出 expect_exit（退出码断言）")
        low = val.lower()
        for bad in VAGUE_ACCEPTANCE:
            if bad in val or bad in low:
                report.error("%s.acceptance[%d]" % (where, aidx), "完成标准含空话 %r：%r" % (bad, val))
                break

    # 回灌禁令
    if step.get("refeed_full_history") is True:
        report.error(where, "refeed_full_history=true，全量上文回灌（输入 token 是成本主项，禁止）")
    ctx = step.get("context_bytes_proxy")
    if isinstance(ctx, int) and ctx > MAX_CONTEXT_BYTES:
        report.error(where, "context_bytes_proxy=%d > %d，单步上下文过重" % (ctx, MAX_CONTEXT_BYTES))
    files_read = step.get("files_read_proxy")
    if isinstance(files_read, int) and files_read > MAX_FILES_READ:
        report.error(where, "files_read_proxy=%d > %d，违反渐进披露（先检索定位再定点读）" % (files_read, MAX_FILES_READ))
    summ = step.get("summary_chars")
    if isinstance(summ, int) and summ > MAX_SUMMARY_CHARS:
        report.error(where, "summary_chars=%d > %d，压缩总结超长" % (summ, MAX_SUMMARY_CHARS))
    if isinstance(summ, int) and summ > 0 and isinstance(step.get("summary"), str):
        real = len(step["summary"])
        if abs(real - summ) > SUMMARY_CHARS_TOLERANCE:
            report.error(where, "summary_chars=%d 与 summary 实际长度 %d 不符（禁止漏记/虚报）" % (summ, real))

    # 思考预算闸
    ratio = step.get("think_ratio")
    if isinstance(ratio, (int, float)) and ratio > MAX_THINK_RATIO:
        report.error(where, "think_ratio=%.2f > %.2f，应改写最小可执行验证而非继续推理" % (ratio, MAX_THINK_RATIO))

    # 有界重试（v1.1）：重试必须有上限、有留痕、有变化（同法重试即空转）
    retries = step.get("retries")
    if retries is not None:
        if not _is_int(retries) or retries < 0:
            report.error(where, "retries=%r 必须是非负整数（重试次数必须可计量）" % retries)
        elif retries > RT_MAX_RETRIES:
            report.error(where, "retries=%d 超过上限 %d：同法重试超过 %d 次就该换路，不是再来一次"
                         % (retries, RT_MAX_RETRIES, RT_MAX_RETRIES))
        elif retries > 0:
            reason = (step.get("retry_reason") or "").strip()
            delta = (step.get("delta_from_last") or "").strip()
            if len(reason) < RT_MIN_REASON_CHARS:
                report.error(where, "retries=%d 但 retry_reason 缺失或过短（≥%d 字）：重试不写原因等于掩盖失败"
                             % (retries, RT_MIN_REASON_CHARS))
            if len(delta) < RT_MIN_DELTA_CHARS:
                report.error(where, "retries=%d 但 delta_from_last 缺失或过短（≥%d 字）："
                                   "说不清这次与上次差在哪，就是同法重试"
                             % (retries, RT_MIN_DELTA_CHARS))

    # Builder / Verifier 分离
    if tier in ("T2", "T3") and step.get("builder") and step.get("builder") == step.get("verifier"):
        report.error(where, "builder == verifier == %r，禁止自评签字" % step.get("builder"))

    # handoff（v2.0）：每步必须写 ≤1000 字交接（目标/已完成/下一步），目标锚点防跑偏。
    # 缺 handoff = 下一步只能重放历史或凭空续跑 = 跑偏风险；故每步强制。
    handoff = step.get("handoff")
    if not isinstance(handoff, dict):
        report.error(where, "缺 handoff 交接：每步结束必须写目标/已完成/下一步，下一步只读它防跑偏")
    else:
        total = 0
        for sec in HO_REQUIRED_SECTIONS:
            val = handoff.get(sec)
            if not isinstance(val, str) or len(val.strip()) < HO_MIN_SECTION_CHARS:
                report.error("%s.handoff" % where,
                             "缺 %s 段或过短（≥%d 字）：current_goal 是防跑偏锚点，completed/next_step 是无损交接"
                             % (sec, HO_MIN_SECTION_CHARS))
            elif isinstance(val, str):
                total += len(val)
        if total > HO_MAX_CHARS:
            report.error("%s.handoff" % where,
                         "handoff 三段合计 %d 字 > %d：压缩只压过程不压结论/证据/目标，超长说明没压缩"
                         % (total, HO_MAX_CHARS))


def check_spin(steps, report):
    """空转闸：连续 SPIN_STRIKES 步 evidence_delta=0 即 two-strike 出局。"""
    streak = 0
    for idx, step in enumerate(steps):
        delta = step.get("evidence_delta")
        if isinstance(delta, int) and delta == 0:
            streak += 1
            if streak >= SPIN_STRIKES:
                report.error("steps[%d](%s)" % (idx, step.get("id")),
                             "连续 %d 步 evidence_delta=0（two-strike）：必须停止并升级换路，禁止同法重试"
                             % streak)
        else:
            streak = 0


def check_trend(state, report):
    trend = state.get("progress_trend") or []
    streak = 0
    for i in range(1, len(trend)):
        if trend[i] <= trend[i - 1]:
            streak += 1
            if streak >= SPIN_STRIKES:
                report.error("progress_trend", "验证分数连续 %d 次不升（%s → %s → %s），方向已偏离，必须换路"
                             % (streak, trend[i - 2] if i >= 2 else "-", trend[i - 1], trend[i]))
        else:
            streak = 0
        if trend[i] < trend[i - 1]:
            report.warn("progress_trend", "第 %d 次评分下降（%s → %s），应回滚到上一高分状态" % (i + 1, trend[i - 1], trend[i]))


def main(argv):
    args = parse_args(argv, {"--state": "state", "--tier": "tier"}, ["state"])
    state = load_json(args["state"], "task-state")
    tier = norm_tier(args.get("tier") or state.get("tier"), default="T2")

    report = Report("gate_loop_guard (四道成本闸门)")
    report.note("state=%s tier=%s" % (args["state"], tier))

    if tier == "T0":
        report.note("T0 仅适用诚实纪律，四道成本闸门不启用。")
        return report.finish()

    steps = state.get("steps") or []
    if not steps:
        report.error("steps", "没有任何步骤记录，无法证明执行过程（状态外置是硬要求）")

    for idx, step in enumerate(steps):
        check_step(idx, step, tier, report)

    check_spin(steps, report)
    check_trend(state, report)
    check_safety_valve(state, tier, report)
    check_cost_metering(state, tier, report)
    return report.finish()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
