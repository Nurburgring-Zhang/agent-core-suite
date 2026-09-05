#!/usr/bin/env python3
"""G-Honesty 诚实度门禁：证据分级、降级台账、子代理契约、裁判自证、行为评测三臂。

用法：
    python -X utf8 gate_honesty_ledger.py --state .acs/task-state.json --tier T3

五项机制的来源与理由（详见 spec/thresholds.json 各节 _why）：
    evidence_basis      caveman 七档证据分级 —— 弱证据不得静默升档
    degradation_ledger  ponytail 债务注释 —— 降级必须写天花板与升级路径
    subagent_contract   caveman cavecrew —— 硬文件数上限 + 强制空结果字面量
    judge_selftest      ponytail judge --selftest —— 不验打分器就没有排序地基
    behavior_eval       caveman 三臂评测 —— 缺中间臂会把通用效应算成自家功劳

退出码：0=PASS，1=BLOCK，2=USAGE_ERROR（视为未验证=未完成）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _acs_common import Report, load_json, parse_args, spec_section, usage_exit  # noqa: E402

EB = spec_section("evidence_basis")
DL = spec_section("degradation_ledger")
SC = spec_section("subagent_contract")
JS = spec_section("judge_selftest")
BE = spec_section("behavior_eval")
TIERS = ("T0", "T1", "T2", "T3")


def is_str(value, min_chars=1):
    return isinstance(value, str) and len(value.strip()) >= min_chars


def check_evidence_basis(state, tier, report):
    """证据分级：值必须与 basis 并列；聚合不得升档；强档必须给可复查证据。"""
    cm = state.get("cost_metering")
    if tier in EB["declare_required_tiers"]:
        if not isinstance(cm, dict):
            report.error("evidence_basis", "%s 级必须声明 cost_metering（拿不到就写 unavailable，沉默不算声明）" % tier)
            return
    if not isinstance(cm, dict):
        return

    basis = cm.get("basis")
    if basis is None:
        if tier in EB["declare_required_tiers"]:
            report.error("evidence_basis", "缺 basis 字段：数值必须与证据档位并列展示，否则弱证据会被当强证据用")
        return
    if basis not in EB["levels"]:
        report.error("evidence_basis", "basis=%r 越界，允许：%s" % (basis, ", ".join(EB["levels"])))
        return

    has_actual = any(k.endswith("_actual") for k in cm)
    if has_actual and basis not in EB["strong_levels"]:
        report.error("evidence_basis",
                     "basis=%s 不属可审计强档（%s）却出现 *_actual 字段：字段名本身就是诚实声明，不得冒充计量"
                     % (basis, ", ".join(EB["strong_levels"])))
    if basis in EB["require_evidence_ref_levels"]:
        ref = cm.get("evidence_ref")
        if not is_str(ref, EB["min_evidence_chars"]):
            report.error("evidence_basis",
                         "basis=%s 声称可复查，必须给 evidence_ref（路径或取数命令，空话无效）" % basis)

    # 聚合不得升档：合计的 basis 必须 ≤ 各分量的最弱档
    parts = cm.get("components")
    if isinstance(parts, list) and parts:
        order = EB["strength_order"]
        weakest = None
        for idx, part in enumerate(parts):
            if not isinstance(part, dict) or part.get("basis") not in order:
                report.error("evidence_basis", "components[%d] 缺合法 basis" % idx)
                continue
            rank = order[part["basis"]]
            weakest = rank if weakest is None else min(weakest, rank)
        if weakest is not None and EB.get("no_silent_upgrade") and order[basis] > weakest:
            report.error("evidence_basis",
                         "聚合 basis=%s 强于最弱分量（强度 %d > %d）：聚合时静默换档等于伪造证据强度"
                         % (basis, order[basis], weakest))


def check_degradation_ledger(state, tier, report):
    """降级台账：标了 [DEGRADED]/[降维实现] 就必须写清天花板与升级路径。"""
    entries = state.get("degradations")
    if entries is None:
        return
    if not isinstance(entries, list):
        report.error("degradation_ledger", "degradations 必须是数组")
        return
    for idx, item in enumerate(entries):
        where = "degradations[%d]" % idx
        if not isinstance(item, dict):
            report.error(where, "必须是对象")
            continue
        marker = item.get("marker")
        if marker not in DL["markers"]:
            report.error(where, "marker=%r 必须是 %s 之一" % (marker, ", ".join(DL["markers"])))
        for field in DL["required_fields"]:
            if not is_str(item.get(field), DL["min_field_chars"]):
                report.error(where, "缺 %s：没有天花板与升级路径的降级只是掩盖，不是登记" % field)


def check_subagent_contract(state, tier, report):
    """子代理契约：文件数上限 + 强制空结果字面量。"""
    if tier not in SC["require_output_contract_tiers"]:
        return
    nodes = ((state.get("graph") or {}).get("nodes") or [])
    dispatched = [n for n in nodes if isinstance(n, dict) and n.get("subagent")]
    if not dispatched:
        return
    limit = SC["max_files_per_builder_task"]
    for node in dispatched:
        nid = node.get("id", "?")
        contract = node.get("subagent")
        if not isinstance(contract, dict):
            report.error("subagent:%s" % nid, "subagent 必须是对象（role/output_contract/empty_literal）")
            continue
        role = contract.get("role")
        if role not in SC["roles"]:
            report.error("subagent:%s" % nid, "role=%r 越界，允许：%s" % (role, ", ".join(SC["roles"])))
        if role == "builder":
            outs = node.get("outputs") or []
            if len(outs) > limit:
                report.error("subagent:%s" % nid,
                             "builder 节点产出 %d 个文件 > 上限 %d：过大的任务必须先拆，不是硬做"
                             % (len(outs), limit))
        literal = contract.get("empty_literal")
        if not is_str(literal):
            report.error("subagent:%s" % nid,
                         "缺 empty_literal：无结果时必须回固定字面量而非猜测，这是「编造引用」的唯一可机检拦法")
        elif literal not in SC["empty_result_literals"]:
            report.error("subagent:%s" % nid,
                         "empty_literal=%r 不在约定集合：%s" % (literal, ", ".join(SC["empty_result_literals"])))


def check_judge_selftest(state, tier, report):
    """裁判自证：T3 打分排序前必须证明打分器本身能排对已知样本。"""
    if tier not in JS["required_tiers"]:
        return
    gates = state.get("gates") or {}
    g5 = gates.get("G5") if isinstance(gates, dict) else None
    has_blind = isinstance(g5, dict) and g5.get("blind_reviews")
    st = state.get("judge_selftest")
    if not has_blind and st is None:
        return
    if not isinstance(st, dict):
        report.error("judge_selftest", "%s：%s（缺 judge_selftest 记录）" % (tier, JS["refuse_message"]))
        return
    for field in JS["required_fields"]:
        if field not in st:
            report.error("judge_selftest", "缺 %s：不验打分器本身，排序结论没有地基" % field)
    if st.get("ranked_correctly") is not True:
        report.error("judge_selftest", "ranked_correctly 非 true：%s" % JS["refuse_message"])


def check_behavior_eval(state, tier, report):
    """行为评测三臂：诚实 delta 必须对照同等强度的朴素指令臂。"""
    ev = state.get("behavior_eval")
    if ev is None:
        return
    if not isinstance(ev, dict):
        report.error("behavior_eval", "behavior_eval 必须是对象")
        return
    runs = ev.get("runs")
    if not isinstance(runs, list) or not runs:
        report.error("behavior_eval", "缺 runs：没有实测记录的评测结论不成立")
        return
    arms = set()
    negatives = 0
    for idx, run in enumerate(runs):
        where = "behavior_eval.runs[%d]" % idx
        if not isinstance(run, dict):
            report.error(where, "必须是对象")
            continue
        for field in BE["required_fields"]:
            if field not in run:
                report.error(where, "缺字段 %s" % field)
        arm = run.get("arm")
        if arm not in BE["arms"]:
            report.error(where, "arm=%r 越界，允许：%s" % (arm, ", ".join(BE["arms"])))
        else:
            arms.add(arm)
        if run.get("near_miss") is True:
            negatives += 1
    if len(runs) < BE["min_queries"]:
        report.error("behavior_eval", "样本 %d < 最少 %d" % (len(runs), BE["min_queries"]))
    if negatives < BE["min_near_miss_negatives"]:
        report.error("behavior_eval",
                     "near-miss 负例 %d < %d：只用容易的负例会把触发率评高"
                     % (negatives, BE["min_near_miss_negatives"]))
    for need in BE["honest_delta_pair"]:
        if need not in arms:
            report.error("behavior_eval",
                         "缺 %s 臂：诚实 delta 必须是 %s 之差，否则把通用效应算成自家功劳"
                         % (need, " vs ".join(BE["honest_delta_pair"])))


def main(argv):
    args = parse_args(argv, {"--state": "state", "--tier": "tier"}, ["state"])
    tier = (args.get("tier") or "").strip() or "T2"
    if tier not in TIERS:
        usage_exit("tier=%r 非法，允许：%s" % (tier, ", ".join(TIERS)))
    state = load_json(args["state"], "task-state")
    if not isinstance(state, dict):
        usage_exit("task-state 根节点必须是对象")

    report = Report("gate_honesty_ledger (诚实度：证据分级/降级台账/子代理契约/裁判自证/行为评测)")
    report.note("state=%s tier=%s" % (args["state"], tier))
    check_evidence_basis(state, tier, report)
    check_degradation_ledger(state, tier, report)
    check_subagent_contract(state, tier, report)
    check_judge_selftest(state, tier, report)
    check_behavior_eval(state, tier, report)
    return report.finish()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
