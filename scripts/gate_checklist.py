"""G0-G6 门禁清单核对：按分级检查每道门是否有真实证据与合法状态。

用法：
    python -X utf8 gate_checklist.py --state .acs/task-state.json [--tier T2]

退出码：0=PASS，1=BLOCK，2=USAGE_ERROR（视为未验证=未完成）

v1.1 增强（spec/double_blind 节）：G5 双盲审查机检（T3）——独立审查者
    ≥2 人、reviewer 不得是建造者、verdict 冲突必须仲裁留痕。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _acs_common import (  # noqa: E402
    Report,
    load_json,
    norm_tier,
    parse_args,
    spec_section,
    usage_exit,
)

# 分级必备门与下限均来自 spec/thresholds.json（单一真相源）
_CL = spec_section("checklist")
REQUIRED_BY_TIER = _CL["required_gates_by_tier"]

GATE_NAMES = {
    "G0": "钢人门（双向论证 + 关键提问）",
    "G1": "认知门（背景/过程/目标/外部/风险 + 缺口声明）",
    "G2": "规划门（规划/设计标准/阶段明细/边界 + 工程手段登记）",
    "G3": "执行门（真实实现 + 互审 + 成本闸门）",
    "G4": "验证门（逐条验收 + 真实运行 + 方向校准）",
    "G5": "交付门（对抗审核 ≥2 + 上线测试 ≥2 + 报告四要素）",
    "G6": "进化门（经验沉淀 + RSI 闭环）",
}

VAGUE_EVIDENCE = tuple(spec_section("vague_phrases")["evidence"])

MIN_ADVERSARIAL_REVIEWS = _CL["min_adversarial_reviews"]
MIN_RELEASE_TESTS = _CL["min_release_tests"]

# ---------------------------------------------------------------- 软约束硬化层
#
# 这 7 项原先只写在 reference-gates.md 里标 [软]：靠模型自觉，会漂移。
# 现在改为读结构化字段做机检——缺字段等于无法核查，视同未做。
# v1.1 新增第 8 项 G5_blind_reviews（双盲审查，仅 T3）。
# 注意：本层不评判思考质量（那是原生能力的事），只拦「整段跳过」与「写了空话」。
_HD = _CL["hardened"]
MIN_PRO = _HD["min_steelman_pro"]
MIN_CON = _HD["min_steelman_con"]
MAX_KEY_QUESTIONS = _HD["max_key_questions"]
MIN_CLAIM_CHARS = _HD["min_claim_chars"]
MIN_BASIS_CHARS = _HD["min_basis_chars"]
MIN_REVIEWS = _HD["min_reviews"]
COGNITION_FIELDS = _HD["cognition_fields"]
PLAN_FIELDS = _HD["plan_fields"]
REPORT_FIELDS = _HD["report_fields"]
RSI_FIELDS = _HD["rsi_fields"]
FIELD_OWNER_GATE = _HD["field_owner_gate"]
HARDENED_BY_TIER = _HD["required_by_tier"]

# 双盲审查（v1.1，Anthropic 图工程第 6 步）：审批者≠建造者、互相独立、分歧必仲裁
_DB = spec_section("double_blind")
DB_MIN_REVIEWERS = _DB["min_reviewers"]

# 每步压缩交接（v2.1，方向1）：≤max_chars_hard 字硬上限 + 三要素无损 + 目标锚点防跑偏
_HO = spec_section("handoff")
HO_MAX_CHARS = _HO["max_chars_hard"]
HO_TOL = _HO["chars_tolerance"]
HO_MIN_GOAL = _HO["min_goal_anchor_chars"]
HO_MIN_NEXT = _HO["min_next_chars"]
HO_REQUIRE_DRIFT = _HO["require_drift_checked"]

# 每步双 AI 自对抗审核（v2.1）：builder≠verifier + 发现→修复→复验闭环 + tier 缩放
_PSR = spec_section("per_step_review")
PSR_VERDICTS = tuple(_PSR["verdict_values"])
PSR_NE_BUILDER = _PSR["require_verifier_ne_builder"]
PSR_MIN_SELF_CHECK = _PSR["min_self_check_chars"]
PSR_T3_MIN_ROUNDS = _PSR["t3_min_rounds"]

HARDENED_LABELS = {
    "G0_steelman": "G0 双向钢人（正/反各 ≥%d 条且条条有依据 + 分歧定位）" % MIN_PRO,
    "G1_cognition": "G1 认知摘要六要素",
    "G2_plan": "G2 规划四要素（规划/设计标准/阶段明细/边界）",
    "G3_review_closure": "G3 双身份互审闭环（签字人不同 + 发现→修复→复验）",
    "G4_acceptance_actual": "G4 验收逐条实证（acceptance.actual）",
    "G5_report": "G5 交付报告四要素",
    "G5_blind_reviews": "G5 双盲审查（独立审查者 ≥%d 人 + 非建造者 + 分歧仲裁）" % DB_MIN_REVIEWERS,
    "G6_rsi": "G6 RSI 闭环（问题→根因→动作→验证→沉淀位置）",
    "STEP_handoff": "每步压缩交接（≤%d字 + 三要素无损 + 目标锚点防跑偏）" % HO_MAX_CHARS,
    "STEP_self_review": "每步双AI自对抗审核 + 自查自检（builder≠verifier + 发现→修复→复验闭环）",
}


def _is_vague(text):
    low = (text or "").strip().lower()
    return low in [p.lower() for p in VAGUE_EVIDENCE]


def _nonempty_strings(value):
    """取出真正有内容的字符串项；不是列表或全是空白 = 视为未填。"""
    if not isinstance(value, list):
        return None
    return [x for x in value if isinstance(x, str) and x.strip()]


def check_field_placement(gates, report):
    """硬化字段写错门 = 核查会落空，必须拦。

    schema 层为避免七份重复定义，允许 gate-entry 携带全部硬化字段；
    「哪个字段属于哪道门」的语义约束在这里兜住。
    """
    for key, entry in sorted(gates.items()):
        if not isinstance(entry, dict):
            continue
        for field, owner in sorted(FIELD_OWNER_GATE.items()):
            if field in entry and key != owner:
                report.error("gates.%s.%s" % (key, field),
                             "字段 %s 属于 %s，写在 %s 下会让机检落空" % (field, owner, key))


def check_steelman(entry, report):
    where = "gates.G0.steelman"
    sm = entry.get("steelman")
    if not isinstance(sm, dict):
        report.error(where, "缺双向钢人记录：正/反论证没有结构化留痕就无法核查是否真做过")
        return
    for side, floor, label in (("pro", MIN_PRO, "正向"), ("con", MIN_CON, "反向")):
        items = sm.get(side)
        if not isinstance(items, list) or len(items) < floor:
            report.error("%s.%s" % (where, side),
                         "%s钢人 %d 条 < %d 条" % (label, len(items) if isinstance(items, list) else 0, floor))
            continue
        for i, item in enumerate(items):
            at = "%s.%s[%d]" % (where, side, i)
            if not isinstance(item, dict):
                report.error(at, "条目须为 {claim, basis} 对象")
                continue
            claim = (item.get("claim") or "").strip()
            basis = (item.get("basis") or "").strip()
            if len(claim) < MIN_CLAIM_CHARS:
                report.error(at, "claim 过短（%d 字 < %d）" % (len(claim), MIN_CLAIM_CHARS))
            if len(basis) < MIN_BASIS_CHARS:
                report.error(at, "basis 过短（%d 字 < %d）：只有主张没有依据等于没论证"
                             % (len(basis), MIN_BASIS_CHARS))
            elif _is_vague(basis):
                report.error(at, "basis 是空话 %r，须写文件/命令/实测值" % basis)
    if not (sm.get("divergence") or "").strip():
        report.error("%s.divergence" % where, "缺分歧定位：不写分歧就等于没做双向论证")
    if not (sm.get("key_variable") or "").strip():
        report.error("%s.key_variable" % where, "缺最可能改变结论的关键变量")

    questions = entry.get("key_questions")
    if questions is None:
        if not (entry.get("questions_compressed_reason") or "").strip():
            report.error("gates.G0.key_questions",
                         "既没有关键问题记录，也没写压缩理由（可压缩，但必须记录理由）")
        return
    if not isinstance(questions, list):
        report.error("gates.G0.key_questions", "key_questions 须为数组")
        return
    if len(questions) > MAX_KEY_QUESTIONS:
        report.error("gates.G0.key_questions", "关键问题 %d 个 > %d 个上限（问太多是把决策推回主人）"
                     % (len(questions), MAX_KEY_QUESTIONS))
    for i, item in enumerate(questions):
        at = "gates.G0.key_questions[%d]" % i
        if not isinstance(item, dict):
            report.error(at, "条目须为 {q, answer} 对象")
            continue
        if not (item.get("answer") or "").strip():
            report.error(at, "问题 %r 没有主人答复，不得当作已澄清" % (item.get("q") or "")[:20])


def check_cognition(entry, report):
    where = "gates.G1.cognition"
    cg = entry.get("cognition")
    if not isinstance(cg, dict):
        report.error(where, "缺《任务认知摘要》结构化记录（%s）" % "/".join(COGNITION_FIELDS))
        return
    for field in COGNITION_FIELDS:
        at = "%s.%s" % (where, field)
        value = cg.get(field)
        if isinstance(value, list):
            items = _nonempty_strings(value)
            if not items:
                report.error(at, "为空：认知摘要缺 %s 一类" % field)
            for item in items:
                if _is_vague(item):
                    report.error(at, "含空话 %r" % item)
        elif isinstance(value, str):
            if not value.strip():
                report.error(at, "为空：认知摘要缺 %s 一类" % field)
            elif _is_vague(value):
                report.error(at, "是空话 %r" % value)
        else:
            report.error(at, "缺字段 %s（认知摘要必须覆盖六类）" % field)
    for i, item in enumerate(cg.get("assumptions") or []):
        if isinstance(item, dict) and not (item.get("risk") or "").strip():
            report.error("%s.assumptions[%d]" % (where, i), "未确认假设必须标注风险")


def check_plan(entry, report):
    where = "gates.G2.plan"
    plan = entry.get("plan")
    if not isinstance(plan, dict):
        report.error(where, "缺规划四要素结构化记录（%s）" % "/".join(PLAN_FIELDS))
        return
    for field in ("phases", "stage_goals"):
        items = _nonempty_strings(plan.get(field))
        if not items:
            report.error("%s.%s" % (where, field), "为空：缺 %s" % field)
    standards = (plan.get("design_standards") or "").strip()
    if not standards:
        report.error("%s.design_standards" % where, "缺设计与标准定义（架构/数据/API 契约位置）")
    elif _is_vague(standards):
        report.error("%s.design_standards" % where, "是空话 %r" % standards)
    boundary = plan.get("boundary")
    if not isinstance(boundary, dict):
        report.error("%s.boundary" % where, "缺边界约束（in_scope/out_scope/out_of_scope_policy）")
        return
    if not _nonempty_strings(boundary.get("in_scope")):
        report.error("%s.boundary.in_scope" % where, "为空：没写做什么")
    if boundary.get("out_scope") is None:
        report.error("%s.boundary.out_scope" % where, "缺 out_scope：不写不做什么就守不住边界（无排除项写空数组）")
    if not (boundary.get("out_of_scope_policy") or "").strip():
        report.error("%s.boundary.out_of_scope_policy" % where, "缺边界外需求的处置方式")


def check_review_closure(entry, report):
    where = "gates.G3.reviews"
    reviews = entry.get("reviews")
    if not isinstance(reviews, list) or len(reviews) < MIN_REVIEWS:
        report.error(where, "互审记录 %d 条 < %d 条（Builder/Verifier 双身份分离交叉签字）"
                     % (len(reviews) if isinstance(reviews, list) else 0, MIN_REVIEWS))
        return
    for i, item in enumerate(reviews):
        at = "%s[%d]" % (where, i)
        if not isinstance(item, dict):
            report.error(at, "条目须为对象")
            continue
        builder = (item.get("builder") or "").strip()
        verifier = (item.get("verifier") or "").strip()
        if not builder or not verifier:
            report.error(at, "builder/verifier 必须都签字")
        elif builder == verifier:
            report.error(at, "builder == verifier == %r，自评签字不算互审" % builder)
        findings = item.get("findings")
        if not isinstance(findings, list):
            report.error(at, "缺 findings（无问题写空数组，字段不得缺）")
            continue
        real = [x for x in findings if isinstance(x, str) and x.strip()]
        if not real:
            continue
        fixed = _nonempty_strings(item.get("fixed")) or []
        recheck = (item.get("recheck") or "").strip()
        if len(fixed) < len(real):
            report.error(at, "发现 %d 项问题但只记录 %d 项修复（发现→修复→复验必须闭环）"
                         % (len(real), len(fixed)))
        if not recheck:
            report.error(at, "有问题被修复却没有复验记录（未复验 = 未闭环）")
        elif _is_vague(recheck):
            report.error(at, "复验记录是空话 %r，须写命令与真实输出" % recheck)


def check_acceptance_actual(state, report):
    """逐条验收必须带实测值：只写期望不写实际 = 未验证 = 未完成。"""
    steps = state.get("steps") or []
    if not steps:
        report.error("steps", "没有任何步骤记录，无法核验逐条验收")
        return
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        for aidx, item in enumerate(step.get("acceptance") or []):
            at = "steps[%d].acceptance[%d]" % (idx, aidx)
            if not isinstance(item, dict):
                continue
            actual = (item.get("actual") or "").strip()
            if not actual:
                report.error(at, "验收项 %r 缺 actual 实测值（未验证 = 未完成）"
                             % (item.get("value") or "")[:40])
            elif _is_vague(actual):
                report.error(at, "actual 是空话 %r，须写退出码/输出/实际数值" % actual)


def check_report(entry, report):
    where = "gates.G5.report"
    rp = entry.get("report")
    if not isinstance(rp, dict):
        report.error(where, "缺交付报告四要素结构化记录（%s）" % "/".join(REPORT_FIELDS))
        return
    for field in REPORT_FIELDS:
        at = "%s.%s" % (where, field)
        items = _nonempty_strings(rp.get(field))
        if items is None:
            report.error(at, "缺字段 %s（交付报告四要素）" % field)
            continue
        if field in ("deliverables", "evidence") and not items:
            report.error(at, "为空：%s 不能空" % field)
        for item in items:
            if _is_vague(item):
                report.error(at, "含空话 %r" % item)


def check_blind_reviews(state, entry, report):
    """双盲审查（Anthropic 图工程第 6 步）：两个互相看不见对方推理的独立审查者。

    三条硬线：人数下限（同人审两轮不是双盲）；审查者不得是建造者
    （角色不分离，双盲就退化成自评）；verdict 冲突必须仲裁留痕
    （分歧是验收标准有歧义的信号，不暴露就永远修不掉）。
    """
    where = "gates.G5.blind_reviews"
    reviews = entry.get("blind_reviews")
    if not isinstance(reviews, list) or len(reviews) < DB_MIN_REVIEWERS:
        report.error(where, "双盲审查 %d 条 < %d 条（独立审查者各留一份记录；同人审两轮不是双盲）"
                     % (len(reviews) if isinstance(reviews, list) else 0, DB_MIN_REVIEWERS))
        return
    builders = set()
    for step in state.get("steps") or []:
        if isinstance(step, dict) and (step.get("builder") or "").strip():
            builders.add(step["builder"].strip())
    seen = set()
    verdicts = []
    for i, item in enumerate(reviews):
        at = "%s[%d]" % (where, i)
        if not isinstance(item, dict):
            report.error(at, "条目须为 {reviewer, verdict, findings} 对象")
            continue
        reviewer = (item.get("reviewer") or "").strip()
        if not reviewer:
            report.error(at, "缺 reviewer（匿名也要有代号，无署名等于无责任人）")
        elif reviewer in builders:
            report.error(at, "reviewer=%r 是建造者：审批者与建造者不得兼任，否则双盲退化成自评" % reviewer)
        elif reviewer in seen:
            report.error(at, "reviewer=%r 重复出现：同一个人审两轮不是双盲" % reviewer)
        seen.add(reviewer)
        verdict = (item.get("verdict") or "").strip()
        if verdict not in ("approve", "reject"):
            report.error(at, "verdict=%r 非法（approve/reject）" % verdict)
        else:
            verdicts.append(verdict)
        if "findings" not in item:
            report.error(at, "缺 findings（无发现写空数组，字段不得缺）")
    if "approve" in verdicts and "reject" in verdicts:
        arb = entry.get("arbitration")
        if not isinstance(arb, dict) or not (arb.get("resolution") or "").strip():
            report.error("gates.G5.arbitration",
                         "双盲 verdict 冲突（approve/reject 并存）却没有仲裁结论："
                         "分歧是验收标准有歧义的信号，必须仲裁留痕")
        elif not (arb.get("reason") or "").strip():
            report.error("gates.G5.arbitration", "仲裁缺 reason（不写理由的裁决无法复盘）")


def check_rsi(entry, report):
    where = "gates.G6.rsi"
    rsi = entry.get("rsi")
    if not isinstance(rsi, dict):
        report.error(where, "缺 RSI 闭环记录（%s）" % "/".join(RSI_FIELDS))
        return
    for field in RSI_FIELDS:
        at = "%s.%s" % (where, field)
        value = rsi.get(field)
        if not isinstance(value, str) or not value.strip():
            report.error(at, "缺 %s：闭环断在这里，经验就沉淀不下来" % field)
        elif _is_vague(value):
            report.error(at, "是空话 %r" % value)


def check_step_handoff(state, tier, report):
    """方向1：每步结束必须写 ≤max_chars_hard 字压缩交接，三要素无损 + 目标锚点防跑偏。

    跨步唯一载体是 .acs/handoff.md，steps[].handoff 是其结构化镜像。机检只拦
    『跳过 / 写空话 / 超字数 / 无目标锚点 / 未做偏离自检』——语义级压缩质量属原生能力，
    本层不假装能评定（与其余硬化项同一诚实口径）。
    """
    steps = state.get("steps") or []
    if not steps:
        report.error("steps", "没有任何步骤记录，无法核验每步压缩交接")
        return
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        at = "steps[%d].handoff" % idx
        ho = step.get("handoff")
        if not isinstance(ho, dict):
            report.error(at, "缺每步压缩交接（当前目标/已完成项/下一步三要素）：跨步无载体=被迫回灌历史")
            continue
        anchor = (ho.get("goal_anchor") or "").strip()
        if len(anchor) < HO_MIN_GOAL:
            report.error("%s.goal_anchor" % at,
                         "当前目标锚点过短（%d 字 < %d）：压缩丢了目标就会跑偏" % (len(anchor), HO_MIN_GOAL))
        elif _is_vague(anchor):
            report.error("%s.goal_anchor" % at, "目标锚点是空话 %r，须写唯一可辨识目标" % anchor)
        if not _nonempty_strings(ho.get("done")):
            report.error("%s.done" % at, "已完成项为空：交接三要素缺一即有损压缩")
        nxt = (ho.get("next") or "").strip()
        if len(nxt) < HO_MIN_NEXT:
            report.error("%s.next" % at, "下一步过短（%d 字 < %d）" % (len(nxt), HO_MIN_NEXT))
        elif _is_vague(nxt):
            report.error("%s.next" % at, "下一步是空话 %r，须写唯一可执行动作" % nxt)
        chars = ho.get("chars")
        if not isinstance(chars, int) or isinstance(chars, bool):
            report.error("%s.chars" % at, "缺字数字段 chars（无法核对压缩上限，等于没计量）")
        elif chars > HO_MAX_CHARS + HO_TOL:
            report.error("%s.chars" % at,
                         "交接 %d 字 > 硬上限 %d（+%d 容差）：总结膨胀成第二份历史，回灌禁令失效"
                         % (chars, HO_MAX_CHARS, HO_TOL))
        if HO_REQUIRE_DRIFT and ho.get("drift_checked") is not True:
            report.error("%s.drift_checked" % at,
                         "未做方向偏离自检（drift_checked 必须为 true）：这是压缩不跑偏的机检底座")


def check_step_self_review(state, tier, report):
    """每步双 AI 自对抗审核 + 自查自检自监督（builder/verifier 分离，规模按 tier）。

    原先双 AI 对抗只在 gate/tier 级触发；本检查把它下沉到每一步收尾，错误不再累积到门才暴露。
    verdict=fail 表示该步未过自审，禁止进入下一步；发现问题必须等量闭环并复验。
    """
    steps = state.get("steps") or []
    if not steps:
        report.error("steps", "没有任何步骤记录，无法核验每步自对抗审核")
        return
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        at = "steps[%d].review" % idx
        rv = step.get("review")
        if not isinstance(rv, dict):
            report.error(at, "缺每步双AI自对抗审核记录（reviewer/verdict/issues/self_check）：错误会累积到门才暴露")
            continue
        reviewer = (rv.get("reviewer") or "").strip()
        builder = (step.get("builder") or "").strip()
        if not reviewer:
            report.error("%s.reviewer" % at, "缺审核者代号：无署名等于无责任人")
        elif PSR_NE_BUILDER and builder and reviewer == builder:
            report.error("%s.reviewer" % at, "reviewer == builder == %r：自对抗退化成自评，不算双AI互审" % reviewer)
        verdict = (rv.get("verdict") or "").strip()
        if verdict not in PSR_VERDICTS:
            report.error("%s.verdict" % at, "verdict=%r 非法（%s）" % (verdict, "/".join(PSR_VERDICTS)))
        elif verdict == "fail":
            report.error("%s.verdict" % at, "该步自审 verdict=fail：未过自对抗审核禁止进入下一步")
        self_check = (rv.get("self_check") or "").strip()
        if len(self_check) < PSR_MIN_SELF_CHECK:
            report.error("%s.self_check" % at, "自查自检结论过短（%d 字 < %d）" % (len(self_check), PSR_MIN_SELF_CHECK))
        elif _is_vague(self_check):
            report.error("%s.self_check" % at, "自查结论是空话 %r，须写核对了什么 + 结论" % self_check)
        if "issues_found" not in rv or "issues_closed" not in rv:
            report.error(at, "缺 issues_found/issues_closed 字段（无问题写空数组，字段不得缺）")
        else:
            found = _nonempty_strings(rv.get("issues_found")) or []
            closed = _nonempty_strings(rv.get("issues_closed")) or []
            if found:
                if len(closed) < len(found):
                    report.error("%s.issues_closed" % at,
                                 "发现 %d 项但只闭环 %d 项（发现→修复必须等量闭环）" % (len(found), len(closed)))
                recheck = (rv.get("recheck") or "").strip()
                if not recheck:
                    report.error("%s.recheck" % at, "有问题被修复却无复验记录（未复验 = 未闭环）")
                elif _is_vague(recheck):
                    report.error("%s.recheck" % at, "复验记录是空话 %r，须写命令与真实输出" % recheck)
        if tier == "T3":
            rounds = rv.get("rounds")
            if not isinstance(rounds, int) or isinstance(rounds, bool) or rounds < PSR_T3_MIN_ROUNDS:
                report.error("%s.rounds" % at,
                             "T3 每步自对抗审核须 ≥%d 轮，实际 %r" % (PSR_T3_MIN_ROUNDS, rounds))


HARDENED_CHECKS = {
    "G0_steelman": ("G0", check_steelman),
    "G1_cognition": ("G1", check_cognition),
    "G2_plan": ("G2", check_plan),
    "G3_review_closure": ("G3", check_review_closure),
    "G5_report": ("G5", check_report),
    "G6_rsi": ("G6", check_rsi),
}


def check_hardened(state, gates, tier, report):
    """按分级跑硬化项；被批准跳过的门不重复罚，但会留 warn。"""
    for item in HARDENED_BY_TIER.get(tier, []):
        label = HARDENED_LABELS.get(item, item)
        if item == "G4_acceptance_actual":
            check_acceptance_actual(state, report)
            continue
        if item == "STEP_handoff":
            check_step_handoff(state, tier, report)
            continue
        if item == "STEP_self_review":
            check_step_self_review(state, tier, report)
            continue
        if item == "G5_blind_reviews":
            entry5 = gates.get("G5")
            if not isinstance(entry5, dict):
                report.error("gates.G5", "%s 无法核查：该门未登记" % label)
            elif entry5.get("status") == "skipped":
                report.warn("gates.G5", "%s 因跳门未核查" % label)
            else:
                check_blind_reviews(state, entry5, report)
            continue
        if item not in HARDENED_CHECKS:
            usage_exit("spec 里 checklist.hardened.required_by_tier 含未知硬化项 %s" % item)
        gate_key, fn = HARDENED_CHECKS[item]
        entry = gates.get(gate_key)
        if not isinstance(entry, dict):
            report.error("gates.%s" % gate_key, "%s 无法核查：该门未登记" % label)
            continue
        if entry.get("status") == "skipped":
            report.warn("gates.%s" % gate_key, "%s 因跳门未核查" % label)
            continue
        fn(entry, report)


def check_gate(key, entry, tier, report):
    where = "gates.%s" % key
    label = GATE_NAMES.get(key, key)
    if not isinstance(entry, dict):
        report.error(where, "%s 未登记（缺该门的状态与证据）" % label)
        return
    status = entry.get("status")
    evidence = (entry.get("evidence") or "").strip()

    if status == "fail":
        report.error(where, "%s 状态为 fail，未准出" % label)
    elif status == "skipped":
        if not (entry.get("approved_by") or "").strip():
            report.error(where, "%s 被跳过但没有 approved_by（跳门必须主人明确批准）" % label)
        else:
            report.warn(where, "%s 被跳过，批准人=%s（须写入交付报告）" % (label, entry.get("approved_by")))
    elif status != "pass":
        report.error(where, "%s status=%r 非法（pass/fail/skipped）" % (label, status))

    if status == "pass":
        if not evidence:
            report.error(where, "%s 声明 pass 但没有证据" % label)
        else:
            low = evidence.lower()
            for bad in VAGUE_EVIDENCE:
                if evidence.strip() == bad or low.strip() == bad:
                    report.error(where, "%s 证据是空话 %r，必须写命令/文件/实际值" % (label, evidence))
                    break
            if len(evidence) < 10:
                report.warn(where, "%s 证据过短（%d 字），建议给出命令与真实输出" % (label, len(evidence)))

    if key == "G5" and tier in ("T2", "T3") and status == "pass":
        reviews = entry.get("adversarial_reviews")
        tests = entry.get("release_tests")
        if not isinstance(reviews, int) or reviews < MIN_ADVERSARIAL_REVIEWS:
            report.error(where, "对抗审核轮次 %r < %d" % (reviews, MIN_ADVERSARIAL_REVIEWS))
        if not isinstance(tests, int) or tests < MIN_RELEASE_TESTS:
            report.error(where, "上线测试轮次 %r < %d" % (tests, MIN_RELEASE_TESTS))


def main(argv):
    args = parse_args(argv, {"--state": "state", "--tier": "tier"}, ["state"])
    state = load_json(args["state"], "task-state")
    tier = norm_tier(args.get("tier") or state.get("tier"), default="T2")

    report = Report("gate_checklist (G0-G6 清单核对)")
    report.note("state=%s tier=%s" % (args["state"], tier))

    if not state.get("tier"):
        report.error("tier", "未声明分级 tier（先定级再动手）")
    if not (state.get("tier_reason") or "").strip():
        report.error("tier_reason", "未写定级理由，无法核查是否该升级")
    if "known_gaps" not in state:
        report.error("known_gaps", "缺 known_gaps 字段：抓不到的信息必须如实声明为缺口（写空数组亦可）")

    if tier == "T0":
        report.note("T0 无门禁清单要求，仅适用诚实纪律。")
        return report.finish()

    if tier in ("T2", "T3") and not state.get("enabled_engineering"):
        report.error("enabled_engineering", "T2+ 必须登记启用的工程手段（G2 硬要求）")

    gates = state.get("gates") or {}
    for key in REQUIRED_BY_TIER[tier]:
        check_gate(key, gates.get(key), tier, report)

    extra = [k for k in gates if k not in GATE_NAMES]
    for key in sorted(extra):
        report.error("gates.%s" % key, "未知门编号（合法为 G0-G6）")

    check_field_placement(gates, report)
    check_hardened(state, gates, tier, report)

    trend = state.get("progress_trend") or []
    if tier == "T3" and len(trend) < 2:
        report.error("progress_trend", "T3 必须记录 ≥2 次验证评分以支持方向偏离检测")
    elif tier == "T2" and len(trend) < 2:
        report.warn("progress_trend", "建议记录 ≥2 次验证评分用于方向偏离检测")

    return report.finish()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
