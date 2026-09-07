"""Agent Core Suite 门禁自测：每道门在正样本 PASS、负样本 BLOCK 双向验证。

运行：
    python -X utf8 -m pytest c:/qoder/agent-core-suite/tests/test_gates.py -q
"""

import ast
import copy
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SUITE = Path(__file__).resolve().parent.parent
SCRIPTS = SUITE / "scripts"
TEMPLATES = SUITE / "templates"

PASS, BLOCK, USAGE = 0, 1, 2


def run_gate(script, *args, **kwargs):
    cmd = [sys.executable, "-X", "utf8", str(SCRIPTS / script)] + [str(a) for a in args]
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env.update(kwargs.get("env") or {})
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def load(name):
    with io.open(str(TEMPLATES / name), "r", encoding="utf-8") as fh:
        return json.load(fh)


def dump(tmp_path, obj, name="state.json"):
    path = tmp_path / name
    with io.open(str(path), "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)
    return path


@pytest.fixture()
def state():
    return copy.deepcopy(load("task-state.example.json"))


@pytest.fixture()
def record():
    return copy.deepcopy(load("verify-record.example.json"))


# ---------------------------------------------------------------- 正样本


def test_positive_state_validate():
    code, out = run_gate("gate_state_validate.py", "--state", TEMPLATES / "task-state.example.json", "--tier", "T2")
    assert code == PASS, out


def test_positive_loop_guard():
    code, out = run_gate("gate_loop_guard.py", "--state", TEMPLATES / "task-state.example.json", "--tier", "T2")
    assert code == PASS, out


def test_positive_checklist():
    code, out = run_gate("gate_checklist.py", "--state", TEMPLATES / "task-state.example.json", "--tier", "T2")
    assert code == PASS, out


def test_positive_verify_rank_t3():
    code, out = run_gate("gate_verify_rank.py", "--record", TEMPLATES / "verify-record.example.json", "--tier", "T3")
    assert code == PASS, out


def test_positive_reality_scan_self():
    """套件自身必须通过真实性扫描（A5）。"""
    code, out = run_gate("gate_reality_scan.py", "--root", SUITE)
    assert code == PASS, out


def test_positive_run_gates_all():
    code, out = run_gate(
        "run_gates.py",
        "--state", TEMPLATES / "task-state.example.json",
        "--root", SUITE, "--tier", "T2",
        "--record", TEMPLATES / "verify-record.example.json",
    )
    assert code == PASS, out
    assert "总判定：PASS" in out


@pytest.mark.parametrize("script,sub", [
    ("gate_state_validate.py", "state"),
    ("gate_loop_guard.py", "loop"),
    ("gate_checklist.py", "checklist"),
])
def test_positive_t3_all_gates(state, tmp_path, script, sub):
    """v1.1 的 T3-only 检查必须有全绿正样本，且双实现一致放行（否则 T3 就是只能拦不能过）。

    v2.1.0：STEP_self_review 下沉到每一步后，T3 额外要求每步 review.rounds ≥ t3_min_rounds。
    example 是 tier=T2 的样板（rounds=1 对 T2 合理），这里按 T3 更严口径把每步 rounds 抬到阈值，
    阈值从单一真相源 thresholds.json 读取，不把该常量固化进测试。
    """
    state["tier"] = "T3"
    t3_min_rounds = _spec()["per_step_review"]["t3_min_rounds"]
    for step in state.get("steps", []):
        review = step.get("review")
        if isinstance(review, dict):
            review["rounds"] = t3_min_rounds
    path = dump(tmp_path, state)
    py_code, py_out = run_gate(script, "--state", path, "--tier", "T3")
    nd_code, nd_out = run_node_gate(sub, "--state", str(path), "--tier", "T3")
    assert py_code == nd_code == PASS, \
        "python=%s node=%s\n--- python ---\n%s\n--- node ---\n%s" % (py_code, nd_code, py_out, nd_out)


# ------------------------------------------------- 负样本：契约与 DAG
#
# 这四份变异清单同时充当【跨实现一致性语料】：下方 test_cross_impl_* 会拿同一批样本
# 分别交给 Python 与 Node 两份实现，逐样本断言退出码相同。两份实现各自自测全绿却
# 对同一输入给不同结论，是双运行时方案最危险的失败模式——它使「通过」变成抽奖。

STATE_MUTATIONS = [
    (lambda s: s.pop("known_gaps"), "known_gaps"),
    (lambda s: s.update(tier="T5"), "越界"),
    (lambda s: s.update(tier_reason=123), "类型"),
    (lambda s: s.pop("steps"), "steps"),
    (lambda s: s["graph"]["nodes"][0].update(verifier="builder-A"), "自评"),
    (lambda s: s["graph"]["nodes"][2].update(calls_model=True), "禁止调模型"),
    (lambda s: s["graph"]["edges"].append({"from": "n2", "to": "n1"}), "环"),
    (lambda s: s["graph"]["nodes"][1]["inputs"].append("参考上文的讨论"), "回灌"),
    (lambda s: s["graph"]["edges"].append({"from": "n2", "to": "n3"}), "并行组"),
    (lambda s: s["graph"]["nodes"].append({
        "id": "n9", "goal": "孤立的活", "inputs": [], "outputs": ["out/x.txt"],
        "acceptance": [{"kind": "file", "value": "out/x.txt"}],
        "deterministic": False, "owner": "builder-A", "verifier": "verifier-B"}), "孤立节点"),
    (lambda s: s["steps"][0].update(node_id="n404"), "不存在"),
    (lambda s: s["graph"]["nodes"][1].update(inputs=["n3.outputs.x"]), "缺少"),
    (lambda s: s.pop("enabled_engineering"), "enabled_engineering"),
    (lambda s: s["graph"]["nodes"][0].update(acceptance=[]), "元素数"),
    (lambda s: s["steps"][0].update(unknown_field=1), "未声明字段"),
    # ---- v1.1：边门 / 汇聚语义 / 契约版本 / 工件记忆 / 验收绑定
    (lambda s: s["graph"]["edges"][0].update(
        gate={"kind": "cmd", "value": "pytest tests/test_parser.py -q"}), "expect_exit"),
    (lambda s: s.update(contract_version="v1.1"), "契约版本"),
    (lambda s: s["graph"]["barriers"].append(
        {"at": "n2", "waits_for": ["n1"], "policy": "min_success"}), "min_count"),
    (lambda s: s["graph"]["barriers"].append(
        {"at": "n2", "waits_for": ["n1"], "policy": "min_success", "min_count": 2}), "超出等待分支数"),
    (lambda s: s["artifacts"][0].update(produced_by="n404"), "不指向任何 graph 节点"),
    (lambda s: s["artifacts"][0].update(supersedes="ghost-artifact"), "版本谱系断链"),
    (lambda s: [s["artifacts"][0].update(supersedes="field-count-report"),
                s["artifacts"][3].update(supersedes="schema-contract")], "成环"),
    (lambda s: s["steps"][0]["acceptance"][0].update(value="docs/brief.md 存在且可读"), "交白卷盲区"),
]


@pytest.mark.parametrize("mutate,keyword", STATE_MUTATIONS)
def test_negative_state_validate(state, tmp_path, mutate, keyword):
    mutate(state)
    path = dump(tmp_path, state)
    code, out = run_gate("gate_state_validate.py", "--state", path, "--tier", "T2")
    assert code == BLOCK, out
    assert keyword in out, out


# v1.1 的 T3 专属契约项单独列（T2 下不应报错，否则就是分级失灵）。
# lambda 里自带 tier 升级：这份清单也进 _cross_cases，而 gate_state_validate 会拦
# 「命令行 tier 与 state.tier 不一致」，不升级就测不到目标检查。
STATE_T3_MUTATIONS = [
    (lambda s: [s.update(tier="T3"), s["graph"]["nodes"][1].pop("model_tier")], "未声明 model_tier"),
    (lambda s: [s.update(tier="T3"),
                s["graph"]["edges"][0].pop("gate"), s["graph"]["edges"][1].pop("gate")], "挂验证门"),
    (lambda s: [s.update(tier="T3"), s.pop("artifacts")], "必须登记工件"),
]


@pytest.mark.parametrize("mutate,keyword", STATE_T3_MUTATIONS)
def test_negative_state_validate_t3_only(state, tmp_path, mutate, keyword):
    """T3 专属契约项：T3 必拦，T2 不得误伤（分级触发本身也要可测）。"""
    mutate(state)
    state["tier"] = "T3"
    path = dump(tmp_path, state)
    code, out = run_gate("gate_state_validate.py", "--state", path, "--tier", "T3")
    assert code == BLOCK, out
    assert keyword in out, out

    state["tier"] = "T2"
    path2 = dump(tmp_path, state, "state-t2.json")
    code2, out2 = run_gate("gate_state_validate.py", "--state", path2, "--tier", "T2")
    assert code2 == PASS, "T3 专属契约项不应在 T2 报错：\n%s" % out2


# ------------------------------------------------- 负样本：四道成本闸门


LOOP_MUTATIONS = [
    (lambda s: s["steps"][0]["outputs"].append("out/extra.txt"), "窄步闸"),
    (lambda s: s["steps"][1].update(budget_min=45), "单步目标过重"),
    (lambda s: s["steps"][1].update(refeed_full_history=True), "回灌"),
    (lambda s: s["steps"][1].update(context_bytes_proxy=30000), "上下文过重"),
    (lambda s: s["steps"][1].update(files_read_proxy=9), "渐进披露"),
    (lambda s: s["steps"][1].update(summary_chars=520), "压缩总结超长"),
    (lambda s: s["steps"][1].update(think_ratio=0.72), "最小可执行验证"),
    (lambda s: s["steps"][1].update(verifier="builder-A"), "自评签字"),
    (lambda s: [s["steps"][0].update(evidence_delta=0), s["steps"][1].update(evidence_delta=0)], "two-strike"),
    (lambda s: s["steps"][1]["acceptance"][0].update(value="功能正常即可"), "空话"),
    (lambda s: s["steps"][1]["acceptance"][0].pop("expect_exit"), "expect_exit"),
    (lambda s: s.pop("safety_valve"), "安全阀"),
    (lambda s: s["safety_valve"].update(max_rounds=40), "上限"),
    (lambda s: s.update(progress_trend=[15.0, 15.0, 14.0]), "方向已偏离"),
    (lambda s: s["steps"][0].update(summary="太短", summary_chars=186), "不符"),
    # ---- token 真实计量回填（消掉「只有 *_proxy」这一处降维）
    (lambda s: s.pop("cost_metering"), "必须声明 cost_metering.source"),
    (lambda s: s["cost_metering"].update(source="self_reported"), "不可审计却填了"),
    (lambda s: s["cost_metering"].pop("tokens_actual"), "必须回填数字"),
    (lambda s: s["cost_metering"].update(evidence_ref="ok"), "不足"),
    (lambda s: s["cost_metering"].update(evidence_ref="已确认无误，token 已核对"), "空话"),
    (lambda s: s["cost_metering"].update(input_tokens_actual=1), "不自洽"),
    (lambda s: s["cost_metering"].update(
        tokens_actual=900000, input_tokens_actual=880000, output_tokens_actual=20000), "成本已失控"),
    # ---- v1.1 有界重试：重试必须有上限、留痕与打法变化
    (lambda s: s["steps"][2].update(retries=3), "超过上限"),
    (lambda s: s["steps"][2].update(retries=1, retry_reason=""), "retry_reason 缺失或过短"),
    (lambda s: s["steps"][2].pop("delta_from_last"), "delta_from_last 缺失或过短"),
]


@pytest.mark.parametrize("mutate,keyword", LOOP_MUTATIONS)
def test_negative_loop_guard(state, tmp_path, mutate, keyword):
    mutate(state)
    path = dump(tmp_path, state)
    code, out = run_gate("gate_loop_guard.py", "--state", path, "--tier", "T2")
    assert code == BLOCK, out
    assert keyword in out, out


def test_loop_guard_t0_exempt(state, tmp_path):
    """T0 只适用诚实纪律，成本闸门不启用（分级触发是节省 token 的关键）。"""
    state["tier"] = "T0"
    state["steps"][0]["outputs"].append("out/extra.txt")
    path = dump(tmp_path, state)
    code, out = run_gate("gate_loop_guard.py", "--state", path, "--tier", "T0")
    assert code == PASS, out


# ------------------------------------------------- 负样本：G0-G6 清单


CHECKLIST_MUTATIONS = [
    (lambda s: s["gates"].pop("G6"), "进化门"),
    (lambda s: s["gates"]["G5"].update(adversarial_reviews=1), "对抗审核轮次"),
    (lambda s: s["gates"]["G5"].update(release_tests=0), "上线测试轮次"),
    (lambda s: s["gates"]["G3"].update(status="skipped"), "approved_by"),
    (lambda s: s["gates"]["G4"].update(status="fail"), "fail"),
    (lambda s: s["gates"]["G4"].update(evidence="ok"), "空话"),
    (lambda s: s.update(tier_reason="   "), "定级理由"),
    (lambda s: s["gates"].update(G9={"status": "pass", "evidence": "无关的门"}), "未知门编号"),
    # ---- 七项软约束硬化（原先只靠自觉，现在缺字段就 BLOCK）
    (lambda s: s["gates"]["G0"].pop("steelman"), "双向钢人"),
    (lambda s: s["gates"]["G0"]["steelman"]["con"].pop(), "反向钢人"),
    (lambda s: s["gates"]["G0"]["steelman"]["pro"][0].update(basis="无"), "basis 过短"),
    (lambda s: s["gates"]["G0"]["steelman"]["pro"][1].update(basis="passed all"), "basis 是空话"),
    (lambda s: s["gates"]["G0"]["steelman"].update(divergence="  "), "分歧定位"),
    (lambda s: s["gates"]["G0"]["steelman"].update(key_variable=""), "关键变量"),
    (lambda s: s["gates"]["G0"].pop("key_questions"), "压缩理由"),
    (lambda s: s["gates"]["G0"]["key_questions"][0].update(answer=""), "没有主人答复"),
    (lambda s: s["gates"]["G0"]["key_questions"].extend(
        [{"q": "额外问题一？", "answer": "好"}, {"q": "额外问题二？", "answer": "好"}]), "个上限"),
    (lambda s: s["gates"]["G1"].pop("cognition"), "任务认知摘要"),
    (lambda s: s["gates"]["G1"]["cognition"].pop("impact"), "缺字段 impact"),
    (lambda s: s["gates"]["G1"]["cognition"].update(risks=[]), "缺 risks"),
    (lambda s: s["gates"]["G1"]["cognition"].update(goal="   "), "缺 goal"),
    (lambda s: s["gates"]["G1"]["cognition"]["assumptions"][0].update(risk=""), "标注风险"),
    (lambda s: s["gates"]["G2"].pop("plan"), "规划四要素"),
    (lambda s: s["gates"]["G2"]["plan"].update(stage_goals=[]), "缺 stage_goals"),
    (lambda s: s["gates"]["G2"]["plan"].update(design_standards=" "), "设计与标准定义"),
    (lambda s: s["gates"]["G2"]["plan"]["boundary"].pop("out_scope"), "缺 out_scope"),
    (lambda s: s["gates"]["G2"]["plan"]["boundary"].update(out_of_scope_policy=""), "边界外需求"),
    (lambda s: s["gates"]["G3"].update(reviews=[]), "互审记录"),
    (lambda s: s["gates"]["G3"]["reviews"][0].update(verifier="builder-A"), "自评签字"),
    (lambda s: s["gates"]["G3"]["reviews"][0].update(fixed=[]), "项修复"),
    (lambda s: s["gates"]["G3"]["reviews"][0].update(recheck=""), "未复验"),
    (lambda s: s["gates"]["G3"]["reviews"][0].update(recheck="都过了"), "复验记录是空话"),
    (lambda s: s["gates"]["G3"]["reviews"][0].pop("findings"), "缺 findings"),
    (lambda s: s["steps"][1]["acceptance"][0].pop("actual"), "缺 actual"),
    (lambda s: s["steps"][0]["acceptance"][0].update(actual="没问题"), "actual 是空话"),
    (lambda s: s["gates"]["G5"].pop("report"), "交付报告四要素"),
    (lambda s: s["gates"]["G5"]["report"].update(evidence=[]), "evidence 不能空"),
    (lambda s: s["gates"]["G5"]["report"].pop("next_steps"), "缺字段 next_steps"),
    # 字段写错门：机检会落空，必须拦
    (lambda s: s["gates"]["G4"].update(rsi=s["gates"]["G6"]["rsi"]), "写在 G4 下"),
    # ---- v2.1 方向1：每步压缩交接 STEP_handoff（steps[0].handoff）
    (lambda s: s["steps"][0].pop("handoff"), "压缩交接"),
    (lambda s: s["steps"][0]["handoff"].update(goal_anchor="xx"), "目标锚点过短"),
    (lambda s: s["steps"][0]["handoff"].update(goal_anchor="符合要求"), "目标锚点是空话"),
    (lambda s: s["steps"][0]["handoff"].update(done=[]), "已完成项为空"),
    (lambda s: s["steps"][0]["handoff"].update(next="xx"), "下一步过短"),
    (lambda s: s["steps"][0]["handoff"].update(next="符合要求"), "下一步是空话"),
    (lambda s: s["steps"][0]["handoff"].pop("chars"), "缺字数字段"),
    (lambda s: s["steps"][0]["handoff"].update(chars=2000), "硬上限"),
    (lambda s: s["steps"][0]["handoff"].update(drift_checked=False), "偏离自检"),
    # ---- v2.1：每步双AI自对抗审核 STEP_self_review（steps[0].review）
    (lambda s: s["steps"][0].pop("review"), "自对抗审核"),
    (lambda s: s["steps"][0]["review"].update(reviewer=""), "审核者代号"),
    (lambda s: s["steps"][0]["review"].update(reviewer="builder-A"), "退化成自评"),
    (lambda s: s["steps"][0]["review"].update(verdict="maybe"), "非法"),
    (lambda s: s["steps"][0]["review"].update(verdict="fail"), "verdict=fail"),
    (lambda s: s["steps"][0]["review"].update(self_check="xx"), "自查自检结论过短"),
    (lambda s: s["steps"][0]["review"].update(self_check="符合要求"), "自查结论是空话"),
    (lambda s: s["steps"][0]["review"].pop("issues_found"), "issues_found"),
    (lambda s: s["steps"][0]["review"].update(
        issues_found=["解析器空输入崩溃"], issues_closed=[], verdict="pass_with_fixes"), "等量闭环"),
]

# T3 才要求的硬化项单独列一份（T2 下不应报错，否则就是分级失灵）
CHECKLIST_T3_MUTATIONS = [
    (lambda s: s["gates"]["G6"].pop("rsi"), "RSI 闭环"),
    (lambda s: s["gates"]["G6"]["rsi"].update(stored_at=""), "缺 stored_at"),
    (lambda s: s["gates"]["G6"]["rsi"].update(verification="已完成"), "是空话"),
    # ---- v1.1 双盲审查：独立、非建造者、分歧必仲裁
    (lambda s: s["gates"]["G5"].pop("blind_reviews"), "双盲审查"),
    (lambda s: s["gates"]["G5"]["blind_reviews"][1].update(reviewer="builder-A"), "是建造者"),
    (lambda s: s["gates"]["G5"]["blind_reviews"][1].update(reviewer="reviewer-D"), "重复出现"),
    (lambda s: s["gates"]["G5"]["blind_reviews"][0].update(verdict="reject"), "仲裁"),
]


@pytest.mark.parametrize("mutate,keyword", CHECKLIST_MUTATIONS)
def test_negative_checklist(state, tmp_path, mutate, keyword):
    mutate(state)
    path = dump(tmp_path, state)
    code, out = run_gate("gate_checklist.py", "--state", path, "--tier", "T2")
    assert code == BLOCK, out
    assert keyword in out, out


@pytest.mark.parametrize("mutate,keyword", CHECKLIST_T3_MUTATIONS)
def test_negative_checklist_t3_only(state, tmp_path, mutate, keyword):
    """T3 硬化项：T3 必拦，T2 不得误伤（分级触发本身也要可测）。"""
    mutate(state)
    state["tier"] = "T3"
    path = dump(tmp_path, state)
    code, out = run_gate("gate_checklist.py", "--state", path, "--tier", "T3")
    assert code == BLOCK, out
    assert keyword in out, out

    state["tier"] = "T2"
    path2 = dump(tmp_path, state, "state-t2.json")
    code2, out2 = run_gate("gate_checklist.py", "--state", path2, "--tier", "T2")
    assert code2 == PASS, "T3 专属硬化项不应在 T2 报错：\n%s" % out2


def test_checklist_t3_requires_trend(state, tmp_path):
    state["tier"] = "T3"
    state["progress_trend"] = [12.5]
    path = dump(tmp_path, state)
    code, out = run_gate("gate_checklist.py", "--state", path, "--tier", "T3")
    assert code == BLOCK, out
    assert "progress_trend" in out


# ------------------------------------------------- 负样本：自验证评分


def _shrink_gap(record):
    """把 c2 的 correctness 中位数提到 18：加权 16.15，冠亚分差收窄到 1.30（< 触发线 1.5）。

    正样本原本 17.45 vs 15.45（差 2.00）本就不该被自一致性门打扰，
    所以负样本必须先把分差压进「可能改判区间」，才测得到这道门。
    """
    sc = record["candidates"][1]["scores"][0]
    assert sc["criterion"] == "correctness", sc
    sc["values"] = [18, 18, 18]
    sc["median"] = 18
    record["candidates"][1]["weighted"] = 16.15


def _resample_plan(**kw):
    plan = {
        "triggered": True, "gap": 1.3, "extra_samples": 2, "resampled": ["correctness"],
        "reason": "冠亚仅差 1.30，correctness 权重最高且是唯一分歧点，先加采样再定案",
    }
    plan.update(kw)
    return plan


VERIFY_MUTATIONS = [
    (lambda r: r.update(weights=[0.5, 0.25, 0.2, 0.1, 0.1]), "权重和"),
    (lambda r: r["candidates"][0]["scores"][0].update(median=20), "中位数"),
    (lambda r: r.update(verifier="builder-A"), "独立于执行者"),
    (lambda r: r.update(ranking=["c2", "c1", "c3"]), "矛盾"),
    (lambda r: r.update(pivots=["c1", "c2", "c3"]), "pivot"),
    (lambda r: r["candidates"][2]["scores"][0].update(evidence="  "), "长度"),
    (lambda r: r.update(repeats=1), "重复评估"),
    (lambda r: r["candidates"][0].update(weighted=19.9), "重算值"),
    (lambda r: r.update(winner="c2"), "ranking[0]"),
    (lambda r: r["candidates"][0]["scores"][0].update(values=[10, 19, 14], median=14), "极差"),
    (lambda r: r["candidates"].pop(0), "至少 3 个候选"),
    (lambda r: r.update(winner_reason=""), "长度"),
    # ---- 智能触发的自一致性重采样（消掉「拿不到 logits」这一处降维）
    (lambda r: _shrink_gap(r), "可能改判区间"),
    (lambda r: r.update(self_consistency={"triggered": False, "extra_samples": 2}), "至少有一个是假的"),
    (lambda r: [_shrink_gap(r), r.update(self_consistency=_resample_plan())], "已登记重采样"),
    (lambda r: [_shrink_gap(r), r.update(self_consistency=_resample_plan(extra_samples=9))], "extra_samples"),
    (lambda r: [_shrink_gap(r), r.update(self_consistency=_resample_plan(resampled=[]))], "没有 resampled"),
    (lambda r: [_shrink_gap(r), r.update(self_consistency=_resample_plan(gap=0.1))], "禁止手改"),
]


@pytest.mark.parametrize("mutate,keyword", VERIFY_MUTATIONS)
def test_negative_verify_rank(record, tmp_path, mutate, keyword):
    mutate(record)
    path = dump(tmp_path, record, "record.json")
    code, out = run_gate("gate_verify_rank.py", "--record", path, "--tier", "T3")
    assert code == BLOCK, out
    assert keyword in out, out


def test_verify_rank_threshold_block(record, tmp_path):
    """冠军分数低于分级门槛必须 BLOCK，禁止放宽门槛。"""
    for score in record["candidates"][0]["scores"]:
        score["values"] = [13, 13, 13]
        score["median"] = 13
    record["candidates"][0]["weighted"] = 13.0
    record["ranking"] = ["c2", "c1", "c3"]
    record["winner"] = "c2"
    path = dump(tmp_path, record, "record.json")
    code, out = run_gate("gate_verify_rank.py", "--record", path, "--tier", "T3")
    assert code == BLOCK, out
    assert "门槛" in out, out


def test_verify_rank_tie_block(record, tmp_path):
    record["candidates"][1]["weighted"] = record["candidates"][2]["weighted"]
    for score in record["candidates"][1]["scores"]:
        score["values"] = [13, 13, 13]
        score["median"] = 13
    record["candidates"][1]["weighted"] = 13.0
    record["candidates"][2]["weighted"] = 13.0
    for score in record["candidates"][2]["scores"]:
        score["values"] = [13, 13, 13]
        score["median"] = 13
    path = dump(tmp_path, record, "record.json")
    code, out = run_gate("gate_verify_rank.py", "--record", path, "--tier", "T3")
    assert code == BLOCK, out
    assert "平局" in out, out


# ------------------------------------------------- 两处降维的消解：真计量回填 + 自一致性重采样


def test_cost_metering_positive_shape(state, tmp_path):
    """正样本必须自带一份可审计的 cost_metering，否则回填规则就只活在文档里。"""
    cm = state["cost_metering"]
    assert cm["source"] == "api_usage", cm
    assert cm["input_tokens_actual"] + cm["output_tokens_actual"] == cm["tokens_actual"], cm
    assert len(cm["evidence_ref"]) >= 6, cm


def test_cost_metering_unavailable_is_honest(state, tmp_path):
    """拿不到真账时，显式写 unavailable 且不填 *_actual 是诚实声明，必须放行。

    这一条是「消降维」不能变成「逗模型编数字」的防线：没真账时正确做法是声明无，而不是编一个。
    """
    state["cost_metering"] = {
        "source": "unavailable",
        "note": "本终端不暴露 usage，本任务只有 *_proxy 代理指标",
    }
    path = dump(tmp_path, state)
    code, out = run_gate("gate_loop_guard.py", "--state", path, "--tier", "T2")
    assert code == PASS, out


def test_cost_metering_not_required_below_t2(state, tmp_path):
    """T0/T1 不强制声明计量来源：分级触发本身就是省 token 的关键。"""
    state.pop("cost_metering")
    path = dump(tmp_path, state)
    _code, out = run_gate("gate_loop_guard.py", "--state", path, "--tier", "T1")
    # 不能直接断言 PASS：T1 的安全阀上限更紧，本样本会因其他项 BLOCK，只验这一道门未误伤
    assert "必须声明 cost_metering.source" not in out, out


def test_verify_rank_self_consistency_pass_when_resampled(record, tmp_path):
    """分差落入可能改判区间，但已登记重采样且样本真的变多 → 放行。"""
    _shrink_gap(record)
    record["candidates"][0]["scores"][0]["values"] = [18, 19, 18, 18, 19]
    record["candidates"][1]["scores"][0]["values"] = [18, 18, 18, 17, 18]
    record["self_consistency"] = _resample_plan()
    path = dump(tmp_path, record, "record.json")
    code, out = run_gate("gate_verify_rank.py", "--record", path, "--tier", "T3")
    assert code == PASS, out


def test_verify_rank_self_consistency_skipped_when_gap_wide(record, tmp_path):
    """分差宽时不强制重采样：重采样花 token，不能无脑全量触发。"""
    path = dump(tmp_path, record, "record.json")
    code, out = run_gate("gate_verify_rank.py", "--record", path, "--tier", "T3")
    assert code == PASS, out
    assert "可能改判区间" not in out, out


def test_verify_rank_self_consistency_tier_scoped(record, tmp_path):
    """T2 不强制重采样（同一份记录在 T3 会被拦），否则分级失灵。"""
    _shrink_gap(record)
    record["tier"] = "T2"
    path = dump(tmp_path, record, "record.json")
    _code, out = run_gate("gate_verify_rank.py", "--record", path, "--tier", "T2")
    assert "可能改判区间" not in out, out


def test_spec_registers_dimension_recovery():
    """两处降维的消解规则必须落在 spec 里并带 _why：阈值可以调，理由不能没。"""
    spec = _spec()
    cm = spec["cost_metering"]
    assert cm["sources"][0] == "unavailable", cm["sources"]
    assert set(cm["auditable_sources"]) <= set(cm["sources"]), cm
    assert cm["declare_required_tiers"] == ["T2", "T3"], cm
    for tier in ("T1", "T2", "T3"):
        assert cm["max_tokens_actual_by_tier"][tier] > 0, cm
    sc = spec["self_consistency"]
    assert sc["enabled_tiers"] == ["T3"], sc
    assert 0 < sc["trigger_score_gap"] < 5, sc
    assert 0 < sc["extra_samples"] <= sc["max_extra_samples"], sc
    for node in (cm, sc):
        assert isinstance(node["_why"], list) and len(node["_why"]) >= 3, node


def test_spec_registers_v11_hardening():
    """v1.1 七个新节必须带 _why 落在 spec：阈值可以调，理由不能没。"""
    import re as _re

    spec = _spec()
    for node in ("edge_gates", "retry", "acceptance_binding", "double_blind",
                 "artifacts", "contract", "model_tier"):
        sec = spec[node]
        assert isinstance(sec["_why"], list) and len(sec["_why"]) >= 3, (node, sec)
    assert spec["edge_gates"]["required_tiers"] == ["T3"], spec["edge_gates"]
    assert spec["retry"]["max_retries"] >= 1, spec["retry"]
    assert spec["acceptance_binding"]["apply_tiers"] == ["T1", "T2", "T3"], spec["acceptance_binding"]
    assert spec["double_blind"]["min_reviewers"] >= 2, spec["double_blind"]
    assert spec["artifacts"]["min_artifacts"] >= 1, spec["artifacts"]
    assert _re.match(spec["contract"]["pattern"], spec["contract"]["known_version"]), spec["contract"]
    # 双盲必须登记进 T3 硬化清单：spec 与双实现同批同步，否则 check_hardened 会 usage_exit
    t3 = spec["checklist"]["hardened"]["required_by_tier"]["T3"]
    assert "G5_blind_reviews" in t3, t3


def test_example_is_v11_positive_carrier():
    """example 必须持续充当正样本载体：这些示范被删，T3-only 检查就测不到。

    contract_version 跟随 VERSION 文件而非写死常量（ACS-ALLOW：本行在讲为何不写死，属规则说明而非降级实现）：写死版本号会让每次升版
    都必须改测试，而「改测试让它变绿」正是最容易掩盖真实回退的动作。
    """
    s = load("task-state.example.json")
    want = io.open(str(SUITE / "VERSION"), "r", encoding="utf-8").read().strip()
    assert s["contract_version"] == want, (s.get("contract_version"), want)
    assert isinstance(s.get("artifacts"), list) and s["artifacts"], "artifacts 载体缺失"
    assert any("supersedes" in a for a in s["artifacts"]), "supersedes 链示范缺失"
    n2 = [n for n in s["graph"]["nodes"] if n["id"] == "n2"][0]
    assert n2.get("calls_model") is True and n2.get("model_tier"), "model_tier 载体缺失"
    s3 = [x for x in s["steps"] if x["id"] == "s3"][0]
    assert s3.get("retries", 0) >= 1 and s3.get("retry_reason") and s3.get("delta_from_last"), \
        "有界重试载体缺失"
    brs = s["gates"]["G5"].get("blind_reviews") or []
    assert len(brs) >= 2 and all("verdict" in b for b in brs), "双盲审查载体缺失"


# ------------------------------------------------- 负样本：真实性扫描


def write(tmp_path, name, text):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with io.open(str(path), "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


@pytest.mark.parametrize("name,text,keyword", [
    ("a.py", "def f():\n    return 1  # TODO 还没写完\n", "TODO"),  # ACS-ALLOW
    ("b.py", "def f():\n    pass\n", "empty-impl"),
    ("c.py", "def f():\n    raise NotImplementedError\n", "empty-impl"),  # ACS-ALLOW
    ("d.py", "def f(\n", "syntax"),
    ("e.py", "VALUE = '这里先用假数据顶一下'\n", "假数据"),
    ("f.ts", "const x = 1; // placeholder implementation\n", "placeholder"),
    ("g.py", "def f():\n    return 1  # 这里是临时方案\n", "临时方案"),  # ACS-ALLOW
])
def test_negative_reality_scan(tmp_path, name, text, keyword):
    write(tmp_path, name, text)
    code, out = run_gate("gate_reality_scan.py", "--root", tmp_path)
    assert code == BLOCK, out
    assert keyword in out, out


def test_reality_scan_allow_marker(tmp_path):
    write(tmp_path, "a.py", "PATTERN = 'TODO'  # ACS-ALLOW\n")
    code, out = run_gate("gate_reality_scan.py", "--root", tmp_path)
    assert code == PASS, out


def test_reality_scan_whitelist(tmp_path):
    write(tmp_path, "vendor/a.py", "X = 1  # TODO\n")  # ACS-ALLOW
    write(tmp_path, "wl.txt", "# 第三方目录免检\nvendor/*\n")
    code, out = run_gate("gate_reality_scan.py", "--root", tmp_path, "--whitelist", tmp_path / "wl.txt")
    assert code == PASS, out


def test_reality_scan_test_files_may_mock(tmp_path):
    """测试文件里用替身是正当的，不应误判；但残留标记仍然要抓。"""
    write(tmp_path, "tests/test_x.py", "def test_a():\n    m = 'mock server'\n    assert m\n")
    code, out = run_gate("gate_reality_scan.py", "--root", tmp_path)
    assert code == PASS, out
    write(tmp_path, "tests/test_y.py", "def test_b():\n    assert 1  # TODO\n")  # ACS-ALLOW
    code, out = run_gate("gate_reality_scan.py", "--root", tmp_path)
    assert code == BLOCK, out


def test_reality_scan_does_not_treat_contest_as_test(tmp_path):
    """生产文件名含 test 子串不能获得 fake 词豁免。"""
    write(tmp_path, "contest.py", "VALUE = 'mock server'\n")
    code, out = run_gate("gate_reality_scan.py", "--root", tmp_path)
    assert code == BLOCK, out
    assert "mock" in out, out


@pytest.mark.parametrize("limit", ["0", "-1"])
def test_reality_scan_rejects_nonpositive_max(tmp_path, limit):
    """输出上限不得关闭扫描；0或负数必须 USAGE_ERROR。"""
    write(tmp_path, "a.py", "VALUE = 'placeholder'\n")
    code, out = run_gate("gate_reality_scan.py", "--root", tmp_path, "--max", limit)
    assert code == USAGE, out
    assert "大于等于 1" in out, out


def test_reality_scan_abstract_method_allowed(tmp_path):
    write(tmp_path, "a.py", "import abc\n\n\nclass A:\n    @abc.abstractmethod\n    def f(self):\n        ...\n")
    code, out = run_gate("gate_reality_scan.py", "--root", tmp_path)
    assert code == PASS, out


# ------------------------------------------------- USAGE_ERROR（=未验证）


@pytest.mark.parametrize("script,args", [
    ("gate_state_validate.py", []),
    ("gate_loop_guard.py", ["--state", "no-such-file.json"]),
    ("gate_checklist.py", ["--state", "no-such-file.json"]),
    ("gate_verify_rank.py", ["--record", "no-such-file.json"]),
    ("gate_reality_scan.py", ["--root", "no-such-dir"]),
    ("run_gates.py", ["--state", "no-such-file.json"]),
])
def test_usage_error(script, args):
    code, out = run_gate(script, *args)
    assert code == USAGE, out


def test_usage_error_bad_json(tmp_path):
    write(tmp_path, "bad.json", "{not json")
    code, out = run_gate("gate_loop_guard.py", "--state", tmp_path / "bad.json")
    assert code == USAGE, out


def test_usage_error_bad_tier(tmp_path):
    code, out = run_gate("gate_loop_guard.py", "--state", TEMPLATES / "task-state.example.json", "--tier", "T9")
    assert code == USAGE, out


def test_usage_error_unknown_arg():
    code, out = run_gate("gate_checklist.py", "--state", TEMPLATES / "task-state.example.json", "--bogus", "1")
    assert code == USAGE, out


# ------------------------------------------------- 负样本：缺字段不得静默通过（堆绕过路径）


@pytest.mark.parametrize("field", [
    "budget_min", "context_bytes_proxy", "refeed_full_history",
    "summary_chars", "think_ratio", "evidence_delta",
])
def test_negative_loop_guard_missing_metric_field(state, tmp_path, field):
    """只要删掉计量字段就能让闸门无从判定 —— 必须 BLOCK，否则门禁形同虚设。"""
    state["steps"][1].pop(field)
    path = dump(tmp_path, state)
    code, out = run_gate("gate_loop_guard.py", "--state", path, "--tier", "T2")
    assert code == BLOCK, out
    assert field in out and "计量字段" in out, out


@pytest.mark.parametrize("field", ["max_rounds", "max_minutes"])
def test_negative_safety_valve_missing_cap(state, tmp_path, field):
    state["safety_valve"].pop(field)
    path = dump(tmp_path, state)
    code, out = run_gate("gate_loop_guard.py", "--state", path, "--tier", "T2")
    assert code == BLOCK, out
    assert "无上限等于没有安全阀" in out, out


def test_negative_schema_unknown_type(state, tmp_path):
    """schema 里 type 写错（strng）不得被当作通过。"""
    schema = {"type": "object", "required": ["task_id"],
              "properties": {"task_id": {"type": "strng"}}}
    schema_path = dump(tmp_path, schema, "bad-schema.json")
    state_path = dump(tmp_path, state)
    code, out = run_gate("gate_state_validate.py", "--state", state_path, "--schema", schema_path)
    assert code == BLOCK, out
    assert "未知类型" in out, out


# ------------------------------------------------- 分发件打包（sha256 逐项回读，A7）


def test_pack_roundtrip_sha256(tmp_path):
    out_zip = tmp_path / "acs-test.zip"
    cmd = [sys.executable, "-X", "utf8", str(SUITE / "pack.py"), "--root", str(SUITE), "--out", str(out_zip)]
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    text = proc.stdout.decode("utf-8", "replace")
    assert proc.returncode == PASS, text
    assert "sha256" in text, text
    assert out_zip.is_file() and out_zip.stat().st_size > 0, text


# ------------------------------------------------- 增强层定位：门禁只判定，不接管不副作用

_WRITE_FUNCS = (
    "remove", "unlink", "rmtree", "mkdir", "makedirs", "rename", "replace",
    "write_text", "write_bytes", "touch", "open",
    "copy", "copy2", "copytree", "move", "chmod", "kill", "killpg", "system", "putenv",
)

# 只盯模块限定调用（os.remove / shutil.rmtree ……）；字符串的 .replace() 与列表的 .copy() 不是副作用，不得误报
_WRITE_MODULES = ("os", "shutil", "path", "subprocess", "Path", "pathlib")


def _gate_sources():
    names = [p for p in sorted(os.listdir(str(SCRIPTS))) if p.endswith(".py")]
    assert names, "scripts/ 下没有 .py，测试前提不成立"
    return names


# v2.1.0：scripts/ 下有三个「按设计需要写文件」的工具脚本（用户显式调用的构建/接线工具，
# 不是「只返回退出码」的门禁）：
#   acs_compress_handoff.py       --render 写工作区 .acs/handoff.md（无镜像则 exit 2，绝不伪造）
#   acs_wire_hooks.py             preserve-strong 幂等合并终端 settings.json 的 hooks（原子写 + 读回校验）
#   acs_build_skill_inventory.py  verbatim 拷贝纯文档技能到 bundled-skills/ + 写 spec/skill-inventory.json
# 它们不受「门禁只读」不变量约束，但必须显式登记在此；门禁（gate_*.py / run_gates.py /
# *global_verify.py）与只读探针（acs_doctor / acs_bootstrap / install_check）永不进入本豁免集，
# 由 test_write_tools_allowlist_is_tight 守住。_gate_sources() 不排除它们——「阈值不得本地重写」
# 这条对工具同样适用（工具也不得私藏阈值常量）。
WRITE_TOOLS = {
    "acs_compress_handoff.py",
    "acs_wire_hooks.py",
    "acs_build_skill_inventory.py",
}
READONLY_PROBES = {"acs_doctor.py", "acs_bootstrap.py", "install_check.py"}
GATE_NAME_RE = __import__("re").compile(r"^(gate_.*\.py|run_gates\.py|.*global_verify\.py)$")


def _scan_side_effects(src, name):
    """返回源码中的写型副作用清单（空列表 = 纯只读）。"""
    import ast

    tree = ast.parse(src, filename=name)
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            fname = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
            if fname in _WRITE_FUNCS:
                if isinstance(fn, ast.Name):
                    bad.append("%s:%d 直接调用了 %s" % (name, node.lineno, fname))
                elif fname in ("write_text", "write_bytes", "touch"):
                    bad.append("%s:%d 调用了路径写方法 .%s" % (name, node.lineno, fname))
                else:
                    base = fn.value
                    base_name = base.id if isinstance(base, ast.Name) else (
                        base.attr if isinstance(base, ast.Attribute) else "")
                    if base_name in _WRITE_MODULES:
                        bad.append("%s:%d 调用了 %s.%s" % (name, node.lineno, base_name, fname))
            if fname == "open":
                mode = None
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    mode = node.args[1].value
                for kw in node.keywords:
                    if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                        mode = kw.value.value
                if mode is not None and (not isinstance(mode, str) or any(c in mode for c in "wax+")):
                    bad.append("%s:%d open(mode=%r) 非只读" % (name, node.lineno, mode))
        # os.environ[...] = ... 式的环境篡改
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for tgt in targets:
                if isinstance(tgt, ast.Subscript) and isinstance(tgt.value, ast.Attribute) \
                        and tgt.value.attr == "environ":
                    bad.append("%s:%d 修改了 os.environ" % (name, node.lineno))
    return bad


@pytest.mark.parametrize("name", _gate_sources())
def test_gate_scripts_are_readonly(name):
    """DESIGN「增强层」约束的机检：门禁脚本只返回退出码，不得写文件/删文件/建目录/改环境/杀进程。

    若未来有人给门禁加上「自动修复」或「写报告文件」，本测试立即失败 —— 那就不再是叠加层而是接管层。

    v2.1.0 例外：WRITE_TOOLS 里显式登记的三个构建/接线工具按设计需要写文件（渲染 handoff、
    合并 settings.json hooks、拷贝纯文档技能）；它们不是门禁。豁免不是「放行不查」——对它们
    反向断言『确实会写』（防止把本可不写的脚本塞进豁免集）且『已登记进 manifest.files』（是正经
    出货物而非游离脚本）。门禁与探针永不进入豁免集，由 test_write_tools_allowlist_is_tight 守住。
    """
    src = io.open(str(SCRIPTS / name), "r", encoding="utf-8").read()
    bad = _scan_side_effects(src, name)
    if name in WRITE_TOOLS:
        assert bad, "%s 在 WRITE_TOOLS 豁免集里却没有任何写副作用：要么它不需要豁免，要么豁免集写错了" % name
        manifest = json.loads(io.open(str(SUITE / "manifest.json"), "r", encoding="utf-8").read())
        assert ("scripts/" + name) in manifest["files"], \
            "%s 是写文件的出货工具，却没登记进 manifest.files（打包会把它丢在外面）" % name
        return
    assert not bad, "\n".join(bad)


def test_write_tools_allowlist_is_tight():
    """豁免集必须紧：只含真实存在的工具，且绝不含任何门禁或只读探针。

    这是「门禁只读」不变量的守门人——如果哪天有人把 gate_checklist.py 塞进 WRITE_TOOLS 来
    绕过只读检查，本测试立即失败。豁免集扩大 = 增强层退化成接管层的第一道裂缝。
    """
    all_scripts = set(_gate_sources())
    # 1) 豁免集里的每个名字都必须真实存在于 scripts/（防拼错名字让豁免静默失效）
    phantom = sorted(WRITE_TOOLS - all_scripts)
    assert not phantom, "WRITE_TOOLS 含 scripts/ 下不存在的名字（拼错=豁免静默失效）：%s" % phantom
    # 2) 门禁与只读探针永不进入豁免集
    forbidden = sorted(n for n in WRITE_TOOLS if GATE_NAME_RE.match(n) or n in READONLY_PROBES)
    assert not forbidden, "WRITE_TOOLS 不得含门禁或只读探针（那等于给门禁开写文件后门）：%s" % forbidden
    # 3) 每个 scripts/*.py 必须被明确归类：要么只读，要么在豁免集——不允许有第三类静默逃逸
    for name in sorted(all_scripts):
        src = io.open(str(SCRIPTS / name), "r", encoding="utf-8").read()
        writes = bool(_scan_side_effects(src, name))
        if writes:
            assert name in WRITE_TOOLS, \
                "%s 有写副作用却不在 WRITE_TOOLS 豁免集：要么改成只读，要么显式登记并说明理由" % name


def _tempfile_bound_names(tree):
    """收集所有由 tempfile.mkstemp(...) 绑定的变量名。

    acs_wire_hooks 的原子写先 mkstemp 建同目录临时文件，os.replace 覆盖目标；写失败时
    os.remove(tmp_path) 清掉自己的临时文件再 raise —— 删的是它自己的临时文件，不是用户文件。
    这类变量名要在删除检测里放行。
    """
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        val = node.value
        if not (isinstance(val, ast.Call) and isinstance(val.func, ast.Attribute)):
            continue
        if val.func.attr != "mkstemp":
            continue
        base = val.func.value
        if not (isinstance(base, ast.Name) and base.id == "tempfile"):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name):
                names.add(tgt.id)
            elif isinstance(tgt, ast.Tuple):
                for elt in tgt.elts:
                    if isinstance(elt, ast.Name):
                        names.add(elt.id)
    return names


def test_write_tools_do_not_delete_user_files():
    """写工具可以创建/修改，但绝不允许删除用户/目标文件；只放行「删自己的 mkstemp 临时文件」。

    与全局文件保护策略一致：acs_wire_hooks 改的是终端 settings.json（preserve-strong，只增不删），
    acs_compress_handoff 写的是工作区 .acs/handoff.md，acs_build_skill_inventory 拷入 bundled-skills/。
    rmtree/shred 一律视为越界；remove/unlink/rmdir 仅当参数是 tempfile.mkstemp 绑定的变量时放行，
    其余（指向用户/目标路径）一律视为越界。
    """
    always_bad = {"rmtree", "shred"}
    conditional_bad = {"remove", "unlink", "rmdir"}
    for name in sorted(WRITE_TOOLS):
        src = io.open(str(SCRIPTS / name), "r", encoding="utf-8").read()
        tree = ast.parse(src, filename=name)
        tmp_vars = _tempfile_bound_names(tree)
        hits = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            fname = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
            if fname in always_bad:
                hits.append("%s:%d %s" % (name, node.lineno, fname))
                continue
            if fname in conditional_bad:
                arg = node.args[0] if node.args else None
                if isinstance(arg, ast.Name) and arg.id in tmp_vars:
                    continue  # 删自己的临时文件，放行
                hits.append("%s:%d %s(非临时文件)" % (name, node.lineno, fname))
        assert not hits, "写工具含删除用户/目标文件的调用（违反文件保护策略）：\n" + "\n".join(hits)


def test_write_tools_tempfile_cleanup_is_actually_exempted():
    """反向自证：确认 acs_wire_hooks 的 os.remove(tmp_path) 被上面的检测放行，
    而把同一行改成删用户文件就会被拦住 —— 否则「放行临时文件」的豁免可能是空豁免。"""
    import tempfile as _tf

    ok_src = (
        "import os, tempfile\n"
        "fd, tmp_path = tempfile.mkstemp(prefix='.acs_wire_', dir='.')\n"
        "try:\n"
        "    os.replace(tmp_path, 'settings.json')\n"
        "except Exception:\n"
        "    if os.path.isfile(tmp_path):\n"
        "        os.remove(tmp_path)\n"
        "    raise\n"
    )
    bad_src = ok_src.replace("os.remove(tmp_path)", "os.remove('settings.json')")

    def _scan(src):
        tree = ast.parse(src)
        tmp_vars = _tempfile_bound_names(tree)
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                fname = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
                if fname in {"remove", "unlink", "rmdir"}:
                    arg = node.args[0] if node.args else None
                    if isinstance(arg, ast.Name) and arg.id in tmp_vars:
                        continue
                    found.append(fname)
        return found

    assert _scan(ok_src) == [], "临时文件清理被误拦：豁免失效"
    assert _scan(bad_src) == ["remove"], "删用户文件却放行：豁免过宽"
    assert _tf  # 保持 import 语义清晰（本用例只做 AST 断言）


@pytest.mark.parametrize("snippet,keyword", [
    ("import os\nos.remove('x')\n", "os.remove"),
    ("import shutil\nshutil.rmtree('x')\n", "shutil.rmtree"),
    ("import io\nio.open('x', 'w')\n", "非只读"),
    ("open('x', mode='a')\n", "非只读"),
    ("import os\nos.environ['A'] = '1'\n", "os.environ"),
    ("from shutil import rmtree\nrmtree('x')\n", "直接调用"),
])
def test_negative_side_effect_detector_catches(snippet, keyword):
    """检测器自身的反向验证：没有这组负样本，上一个测试全绿可能只是因为检测器什么都查不到。"""
    bad = _scan_side_effects(snippet, "fake.py")
    assert bad, "应该拦住却放过了：%r" % snippet
    assert any(keyword in item for item in bad), bad


def test_side_effect_detector_no_false_positive_on_str_methods():
    """字符串 .replace() / 列表 .copy() 不得被误判为副作用。"""
    src = "s = 'a\\\\b'.replace('\\\\', '/')\nd = {'k': 1}.copy()\n"
    assert _scan_side_effects(src, "fake.py") == []


def test_manifest_declares_augmentation_layer():
    """定位不得被静默改写：manifest 必须显式声明自己是增强层、不替代不禁用原生能力。"""
    with io.open(str(SUITE / "manifest.json"), "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    pos = manifest.get("positioning")
    assert isinstance(pos, dict), "manifest 缺 positioning 段"
    assert pos.get("kind") == "augmentation-layer", pos
    assert pos.get("replaces_native_capabilities") is False, pos
    assert pos.get("disables_native_capabilities") is False, pos
    assert pos.get("prefer_native_when_equivalent") is True, pos
    order = pos.get("conflict_precedence") or []
    assert len(order) == 4 and "主人显式指令" == order[0], order


# ------------------------------------------------- 阈值单一真相源


SPEC_FILE = SUITE / "spec" / "thresholds.json"


def _spec():
    with io.open(str(SPEC_FILE), "r", encoding="utf-8") as fh:
        return json.load(fh)


def test_spec_file_exists_and_wellformed():
    spec = _spec()
    assert spec["exit_codes"] == {"pass": 0, "block": 1, "usage_error": 2}, spec["exit_codes"]
    for tier in ("T0", "T1", "T2", "T3"):
        node = spec["tiers"][tier]
        for key in ("n", "r", "threshold", "max_rounds", "max_minutes"):
            assert key in node, (tier, key)
    assert spec["loop"]["required_step_fields"], "计量必填字段不得为空"


@pytest.mark.parametrize("name", _gate_sources())
def test_gate_scripts_declare_no_local_thresholds(name):
    """阈值不得在脚本里再写一份：多一份就多一条漂移路径，且漂移后两边都不报错。

    只管「阈值型常量」（MAX_* / MIN_* / *_LIMIT / *_TOL / *_STRIKES / PIVOT_K），
    不管 EXIT_* 这类協议常量（它们反而需要写在实现里并与 spec 互校）。
    """
    import re

    src = io.open(str(SCRIPTS / name), "r", encoding="utf-8").read()
    pat = re.compile(
        r"^(MAX_[A-Z_]+|MIN_[A-Z_]+|[A-Z_]*LIMIT|[A-Z_]*_TOL|[A-Z_]*STRIKES|PIVOT_K|SPREAD_LIMIT)"
        r"\s*=\s*[-\d.]",
        re.M)
    hits = pat.findall(src)
    assert not hits, "%s 里本地定义了阈值常量 %s，应改为从 spec_section() 取值" % (name, hits)


def test_gates_hard_fail_when_spec_missing(tmp_path):
    """拿不到阈值 = 无法计量 = 等于绕过闸门；必须 USAGE_ERROR，不得回退内置默认值静默 PASS。"""
    ghost = str(tmp_path / "nope" / "thresholds.json")
    code, out = run_gate(
        "gate_loop_guard.py", "--state", TEMPLATES / "task-state.example.json", "--tier", "T2",
        env={"ACS_SPEC_PATH": ghost},
    )
    assert code == USAGE, out
    assert "阈值真相源" in out, out


@pytest.mark.parametrize("bad,keyword", [
    ('{"spec_version":"x"}', "缺顶层字段"),
    ('{"spec_version":"x","exit_codes":{"pass":0,"block":9,"usage_error":2},'
     '"tiers":{},"loop":{},"checklist":{},"vague_phrases":{}}', "退出码与实现不一致"),
    ('{"spec_version":"x","exit_codes":{"pass":0,"block":1,"usage_error":2},'
     '"tiers":{"T0":{}},"loop":{},"checklist":{},"vague_phrases":{}}', "缺分级"),
    ('not json at all', "不是合法 JSON"),
])
def test_gates_reject_broken_spec(tmp_path, bad, keyword):
    """spec 本身写坏也不得静默通过。"""
    path = tmp_path / "thresholds.json"
    with io.open(str(path), "w", encoding="utf-8") as fh:
        fh.write(bad)
    code, out = run_gate(
        "gate_checklist.py", "--state", TEMPLATES / "task-state.example.json", "--tier", "T2",
        env={"ACS_SPEC_PATH": str(path)},
    )
    assert code == USAGE, out
    assert keyword in out, out


def _fmt(value):
    """文档里 0.40 / 20000 都是字面写法，浮点保两位以匹配。"""
    if isinstance(value, float):
        return "%.2f" % value
    return str(value)


_DOC_BOUND = {
    "max_summary_chars": ["AGENTS.md", "README.md", "DESIGN.md",
                          "rules/agent-core-suite.md",
                          "skills/loop-engineering/SKILL.md",
                          "skills/token-thrift/SKILL.md",
                          "templates/handoff-summary.md"],
    "max_context_bytes": ["AGENTS.md", "README.md", "rules/agent-core-suite.md",
                          "skills/token-thrift/SKILL.md"],
    "max_think_ratio": ["AGENTS.md", "DESIGN.md", "rules/agent-core-suite.md",
                        "skills/loop-engineering/SKILL.md"],
}


@pytest.mark.parametrize("key,docs", sorted(_DOC_BOUND.items()))
def test_docs_do_not_drift_from_spec(key, docs):
    """文档里写的数字必须跟 spec 一致。

    软约束漂移的典型开头：改了脚本阈值却忘了改文档，模型按旧文档行事，到门禁才被拦。
    """
    want = _fmt(_spec()["loop"][key])
    missing = []
    for rel in docs:
        text = io.open(str(SUITE / rel), "r", encoding="utf-8").read()
        if want not in text:
            missing.append(rel)
    assert not missing, "%s 的 spec 值 %s 未出现于：%s" % (key, want, missing)


def test_manifest_lists_spec_file():
    with io.open(str(SUITE / "manifest.json"), "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    assert "spec/thresholds.json" in manifest["files"], "清单里没 spec，安装就不会拷，门禁上岗即 USAGE_ERROR"
    assert manifest["thresholds_spec"]["role"] == "single-source-of-truth", manifest.get("thresholds_spec")


@pytest.mark.parametrize("script", ["install.sh", "install.ps1"])
def test_installers_copy_spec(script):
    src = io.open(str(SUITE / script), "r", encoding="utf-8").read()
    assert "thresholds.json" in src, "%s 没拷 spec/thresholds.json，装完就跑不起来" % script


# ------------------------------------------------- 安装脚本：语法与运行时探测


def _find_bash():
    """找一个可用的 bash：PATH 里没有 ≠ 机器上没有。

    Windows 上 IDE 常自带 Git（含 bash.exe）却不入 PATH，只查 PATH 会得出
    「无法实跑 install.sh」的错误结论，从而让 sh 脚本长期无人验证。
    """
    from shutil import which

    for name in ("bash", "sh"):
        found = which(name)
        if found:
            return found
    home = Path(os.path.expanduser("~"))
    candidates = [
        home / ".qoder" / "bin" / "git" / "bin" / "bash.exe",
        home / ".qoderwork" / "bin" / "git" / "bin" / "bash.exe",
        Path("C:/Program Files/Git/bin/bash.exe"),
        Path("C:/Program Files (x86)/Git/bin/bash.exe"),
    ]
    for cand in candidates:
        if cand.is_file():
            return str(cand)
    return None


def test_install_sh_syntax_ok():
    """install.sh 必须能通过 bash 语法检查；找不到 bash 才允许 skip。"""
    bash = _find_bash()
    if not bash:
        pytest.skip("本机未找到 bash，跳过 sh 语法检查")
    proc = subprocess.run(
        [bash, "-n", str(SUITE / "install.sh")],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    assert proc.returncode == 0, proc.stdout.decode("utf-8", "replace")


def test_install_sh_usage_block_line_range_intact():
    """die_usage 用 sed 按行号打印用法段，改注释时行数一动用法就残缺或溢出到代码。"""
    lines = io.open(str(SUITE / "install.sh"), "r", encoding="utf-8").read().splitlines()
    src = "\n".join(lines)
    assert "sed -n '3,20p'" in src, "die_usage 的行号范围被改了，请同步本测试"
    body = lines[2:20]
    assert all(l.startswith("#") for l in body), body
    assert any("--python" in l for l in body), "用法段里必须说明 --python"
    assert any("--node" in l for l in body), "用法段里必须说明 --node（无 Python 时的兜底运行时）"
    assert any("--with-hook" in l for l in body), "用法段里必须说明 --with-hook（外部强制点）"
    assert not lines[20].startswith("#"), "用法段末尾多了注释行，sed 范围需同步扩大"


@pytest.mark.parametrize("script,forbidden,required", [
    ("install.sh", ['PY="python3"', "PY='python3'"], ["python3 python py", "不静默降级"]),
    ("install.ps1", ['$Python = "python3"', '$Python = "python"'],
     ['"python", "python3", "py"', "not a silent downgrade"]),
])
def test_installers_probe_python_instead_of_hardcoding(script, forbidden, required):
    """防回归：解释器名不得固定成单一默认值。

    把 python3 当成唯一默认值，会在只有 `python` 的环境（conda / miniforge / Windows Git Bash）
    直接安装失败，且失败原因看起来与 Python 无关。缺 Python 时必须显式 FAIL，
    不得退化成「只剩软约束」还报成功。
    """
    src = io.open(str(SUITE / script), "r", encoding="utf-8").read()
    for bad in forbidden:
        assert bad not in src, "%s 里把解释器默认值固定成了：%s" % (script, bad)
    for need in required:
        assert need in src, "%s 缺少必要内容：%s" % (script, need)


@pytest.mark.parametrize("script", ["install.sh", "install.ps1"])
def test_installers_refuse_stale_overwrite_without_force(script):
    """A11：升级必须做内容比对，不能只看文件是否存在就报成功。"""
    src = io.open(str(SUITE / script), "r", encoding="utf-8").read()
    if script == "install.sh":
        assert "cmp -s" in src, "install.sh 必须用 cmp 做内容比对"
    else:
        assert "Get-FileHash" in src, "install.ps1 必须用哈希做内容比对"
    assert "force" in src.lower(), script


# ------------------------------------------------- 双运行时：Node 实现与跨实现一致性

NODE_GATE = SUITE / "scripts" / "node" / "acs_gates.mjs"

# Python 与 Node 已登记的语义差异（必须在 manifest 里同步登记）：
# JSON 里写作 25.0 这种「小数写法的整数」，Python isinstance(25.0, int) 为假会跳检，
# Node Number.isInteger(25.0) 为真会入检。一致性语料因此避开这种写法。
KNOWN_IMPL_DIFF_KEY = "decimal_written_integer"


def _find_node():
    from shutil import which
    found = which("node")
    if found:
        return found
    home = Path(os.path.expanduser("~"))
    for cand in [Path("C:/Program Files/nodejs/node.exe"), home / "AppData/Roaming/nvm/node.exe"]:
        if cand.is_file():
            return str(cand)
    return None


def run_node_gate(sub, *args, **kwargs):
    node = _find_node()
    if not node:
        pytest.skip("本机未找到 node，跳过 Node 门禁测试")
    cmd = [node, str(NODE_GATE), sub] + [str(a) for a in args]
    env = dict(os.environ)
    env.update(kwargs.get("env") or {})
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def test_node_gate_file_declares_no_local_thresholds():
    """Node 实现同样不得自带阈值，否则就多出一条漂移路径。"""
    import re

    src = io.open(str(NODE_GATE), "r", encoding="utf-8").read()
    pat = re.compile(r"^const\s+(MAX_[A-Z_]+|MIN_[A-Z_]+|[A-Z_]*LIMIT|[A-Z_]*_TOL|PIVOT_K)"
                     r"\s*=\s*[-\d.]", re.M)
    hits = pat.findall(src)
    assert not hits, "acs_gates.mjs 里本地定义了阈值 %s" % hits
    assert "spec/thresholds.json" in src or "thresholds.json" in src


def test_node_capability_declares_reality_gap():
    """能力缺口必须显式告知，不得静默降级成「4/5 也算全部通过」。"""
    code, out = run_node_gate("capability")
    assert code == PASS, out
    assert "NOT COVERED" in out, out
    assert "reality" in out, out
    assert "4/5" in out, out


def test_node_reality_is_explicit_usage_error():
    code, out = run_node_gate("reality", "--root", str(SUITE))
    assert code == USAGE, out
    assert "Python AST" in out, out


def test_node_run_prints_partial_coverage():
    code, out = run_node_gate(
        "run", "--state", str(TEMPLATES / "task-state.example.json"),
        "--root", str(SUITE), "--tier", "T2",
        "--record", str(TEMPLATES / "verify-record.example.json"),
    )
    assert code == PASS, out
    assert "PARTIAL_COVERAGE 4/5" in out, out


def test_node_hard_fails_when_spec_missing(tmp_path):
    ghost = str(tmp_path / "nope" / "thresholds.json")
    code, out = run_node_gate(
        "loop", "--state", str(TEMPLATES / "task-state.example.json"), "--tier", "T2",
        env={"ACS_SPEC_PATH": ghost})
    assert code == USAGE, out
    assert "阈值真相源" in out, out


@pytest.mark.parametrize("sub,script,flag,fixture_name", [
    ("state", "gate_state_validate.py", "--state", "task-state.example.json"),
    ("loop", "gate_loop_guard.py", "--state", "task-state.example.json"),
    ("checklist", "gate_checklist.py", "--state", "task-state.example.json"),
    ("verify", "gate_verify_rank.py", "--record", "verify-record.example.json"),
])
def test_cross_impl_positive_agree(sub, script, flag, fixture_name):
    tier = "T3" if sub == "verify" else "T2"
    py_code, py_out = run_gate(script, flag, TEMPLATES / fixture_name, "--tier", tier)
    nd_code, nd_out = run_node_gate(sub, flag, str(TEMPLATES / fixture_name), "--tier", tier)
    assert py_code == nd_code == PASS, "python=%s node=%s\n%s\n%s" % (py_code, nd_code, py_out, nd_out)


def _cross_cases():
    cases = []
    for i, (mutate, kw) in enumerate(STATE_MUTATIONS):
        cases.append(("state", "gate_state_validate.py", "--state", "task-state.example.json",
                      "T2", mutate, "state-%02d-%s" % (i, kw)))
    for i, (mutate, kw) in enumerate(STATE_T3_MUTATIONS):
        cases.append(("state", "gate_state_validate.py", "--state", "task-state.example.json",
                      "T3", mutate, "state-t3-%02d-%s" % (i, kw)))
    for i, (mutate, kw) in enumerate(LOOP_MUTATIONS):
        cases.append(("loop", "gate_loop_guard.py", "--state", "task-state.example.json",
                      "T2", mutate, "loop-%02d-%s" % (i, kw)))
    for i, (mutate, kw) in enumerate(CHECKLIST_MUTATIONS):
        cases.append(("checklist", "gate_checklist.py", "--state", "task-state.example.json",
                      "T2", mutate, "checklist-%02d-%s" % (i, kw)))
    for i, (mutate, kw) in enumerate(CHECKLIST_T3_MUTATIONS):
        cases.append(("checklist", "gate_checklist.py", "--state", "task-state.example.json",
                      "T3", mutate, "checklist-t3-%02d-%s" % (i, kw)))
    for i, (mutate, kw) in enumerate(VERIFY_MUTATIONS):
        cases.append(("verify", "gate_verify_rank.py", "--record", "verify-record.example.json",
                      "T3", mutate, "verify-%02d-%s" % (i, kw)))
    return cases


@pytest.mark.parametrize("sub,script,flag,fixture_name,tier,mutate,case_id",
                         _cross_cases(), ids=[c[-1] for c in _cross_cases()])
def test_cross_impl_exit_codes_match(tmp_path, sub, script, flag, fixture_name, tier, mutate, case_id):
    """同一负样本在 Python 与 Node 两份实现下必须得到相同退出码。

    为何只比退出码不比文案：退出码是对外承诺（CI / hook / 模型都只看它），
    人读文案允许因语言格式化差异而不逐字相同。
    """
    payload = copy.deepcopy(load(fixture_name))
    mutate(payload)
    name = "record.json" if flag == "--record" else "state.json"
    path = dump(tmp_path, payload, name)
    py_code, py_out = run_gate(script, flag, path, "--tier", tier)
    nd_code, nd_out = run_node_gate(sub, flag, str(path), "--tier", tier)
    assert py_code == nd_code, (
        "%s 退出码不一致：python=%s node=%s\n--- python ---\n%s\n--- node ---\n%s"
        % (case_id, py_code, nd_code, py_out, nd_out))


def test_manifest_registers_known_impl_diff():
    """已知跨实现差异必须登记在案；不登记等于把问题藏起来。"""
    data = json.loads(io.open(str(SUITE / "manifest.json"), "r", encoding="utf-8").read())
    runtimes = data.get("runtimes") or {}
    diffs = runtimes.get("known_impl_differences") or []
    keys = [d.get("key") for d in diffs]
    assert KNOWN_IMPL_DIFF_KEY in keys, keys
    not_covered = runtimes.get("node", {}).get("not_covered") or []
    # reality 门必须永远在列（需 Python AST）；v1.2 起 honesty/rule/skillspec 亦无 Node 镜像。
    assert "reality_scan" in not_covered, runtimes
    covers = runtimes.get("python", {}).get("covers") or []
    assert "honesty" in covers and "rule" in covers and "skillspec" in covers, runtimes
    assert "capability" in covers, runtimes
    # 未覆盖清单不得包含 Python 侧也没有的门：那说明清单在编故事。
    # covers 用 gate id（honesty/rule/skillspec/capability），not_covered 用脚本名风格
    # （rule_consistency/skill_spec/capability_registry），故 known 须兼容两种命名。
    known = set(covers) | {"reality_scan", "rule_consistency", "skill_spec", "capability_registry"}
    stray = [x for x in not_covered if x not in known]
    assert not stray, stray


# ------------------------------------------------- 编排


def test_run_gates_t3_requires_record(tmp_path, state):
    state["tier"] = "T3"
    state["safety_valve"]["max_rounds"] = 10
    state["safety_valve"]["max_minutes"] = 120
    path = dump(tmp_path, state)
    code, out = run_gate("run_gates.py", "--state", path, "--root", tmp_path, "--tier", "T3")
    assert code == BLOCK, out
    assert "T3 必须提供 verify-record.json" in out, out


# ------------------------------------------------- 能力探针与多终端落点

TERMINALS = SUITE / "spec" / "terminals.json"


def _terminals():
    with io.open(str(TERMINALS), "r", encoding="utf-8") as fh:
        return json.load(fh)


def run_doctor(*args, **kwargs):
    return run_gate("acs_doctor.py", *args, **kwargs)


def run_node_doctor(*args, **kwargs):
    return run_node_gate("doctor", *args, **kwargs)


def test_terminals_spec_shape():
    """终端落点的单一真相源必须自洽：缺字段的 spec 会让安装脚本把文件拷到空路径。"""
    spec = _terminals()
    assert spec["spec_version"] == 1, spec.get("spec_version")
    order = spec["detect_order"]
    assert order[-1] == "generic", "detect_order 末项必须是 generic 兜底，否则识别失败时无去处"
    assert len(set(order)) == len(order), order
    for tid in order:
        term = spec["terminals"][tid]
        for key in ("label", "skills_dir", "rules_source", "rules_target"):
            assert term.get(key), "%s 缺 %s" % (tid, key)
        assert (SUITE / term["rules_source"]).is_file(), \
            "%s 的 rules_source 在套件里不存在：%s" % (tid, term["rules_source"])
        assert "<workspace>" in term["rules_target"] or term["rules_target"].startswith("~"), \
            "%s 的 rules_target 必须是可展开模板：%s" % (tid, term["rules_target"])
    levels = spec["enforcement_levels"]
    named = set(k for k in levels if not k.startswith("_"))
    assert named == {"full", "partial", "soft_only"}, sorted(named)
    assert levels["full"]["gates"] == 5 and levels["partial"]["gates"] == 4, levels
    assert levels["soft_only"]["gates"] == 0, levels["soft_only"]
    assert isinstance(levels["partial"]["min_node_major"], int), levels["partial"]
    assert "不是静默降级" in levels["soft_only"]["note"], levels["soft_only"]


def test_manifest_lists_terminals_spec():
    data = json.loads(io.open(str(SUITE / "manifest.json"), "r", encoding="utf-8").read())
    assert "spec/terminals.json" in data["files"], "清单里没 terminals.json，装完就取不到落点"
    assert "scripts/acs_doctor.py" in data["files"], "清单里没 acs_doctor.py，--mode auto 就无人回答"
    assert data["terminals_spec"]["role"] == "single-source-of-truth", data.get("terminals_spec")


@pytest.mark.parametrize("script", ["install.sh", "install.ps1"])
def test_installers_ask_doctor_instead_of_hardcoding_targets(script):
    """落点只能向探针取：安装脚本里再拄一份映射表就多一条漂移路径。"""
    src = io.open(str(SUITE / script), "r", encoding="utf-8").read()
    assert "acs_doctor" in src, "%s 未向 acs_doctor 取落点" % script
    assert "terminals.json" in src, "%s 未拷 spec/terminals.json" % script
    assert "acs_doctor.py" in src, "%s 未拷 scripts/acs_doctor.py" % script
    for bad in (".qoder/skills", ".qoder\\skills", ".agent/skills", ".agent\\skills"):
        assert bad not in src, "%s 里硬写了技能目录 %s，应改为向探针取值" % (script, bad)


def test_doctor_reports_and_exits_zero_on_this_machine():
    """本机有 Python（正在跑本测试），所以探针必须得出 full 且 exit 0。"""
    code, out = run_doctor("--target", str(SUITE))
    assert code == PASS, out
    assert "full" in out, out
    assert "[执行力等级]" in out, out


def test_doctor_json_is_machine_readable():
    code, out = run_doctor("--target", str(SUITE), "--json")
    assert code == PASS, out
    rep = json.loads(out)
    assert rep["enforcement"]["level"] == "full", rep["enforcement"]
    assert rep["detected"] in _terminals()["detect_order"], rep["detected"]
    assert rep["paths"]["rules_source"], rep["paths"]


@pytest.mark.parametrize("mode", _terminals()["detect_order"] + ["auto"])
def test_doctor_paths_identical_across_impls(mode):
    """双实现必须给出字字相同的落点。

    这里与门禁不同，不能只比退出码：落点是会被当成路径直接拷文件的，
    两侧差一个字符就会把文件装到两个不同的地方，而两边都报成功。
    """
    py_code, py_out = run_doctor("--paths", "--posix", "--mode", mode, "--target", str(SUITE))
    nd_code, nd_out = run_node_doctor("--paths", "--posix", "--mode", mode, "--target", str(SUITE))
    assert py_code == nd_code == PASS, (py_out, nd_out)
    assert py_out.strip().splitlines() == nd_out.strip().splitlines(), \
        "mode=%s 落点不一致\n--- python ---\n%s\n--- node ---\n%s" % (mode, py_out, nd_out)


def test_doctor_mode_only_identical_across_impls():
    py_code, py_out = run_doctor("--mode-only", "--target", str(SUITE))
    nd_code, nd_out = run_node_doctor("--mode-only", "--target", str(SUITE))
    assert py_code == nd_code == PASS, (py_out, nd_out)
    assert py_out.strip() == nd_out.strip(), (py_out, nd_out)


def test_doctor_paths_posix_has_no_backslash():
    """Git Bash 拿到反斜杠会当转义符，mkdir/cp 会静默拼错目录。"""
    code, out = run_doctor("--paths", "--posix", "--mode", "qoder", "--target", str(SUITE))
    assert code == PASS, out
    for line in out.strip().splitlines():
        key, _, value = line.partition("=")
        if key in ("SKILLS_DIR", "RULES_TARGET"):
            assert "\\" not in value, line


@pytest.mark.parametrize("runner", ["python", "node"])
def test_doctor_rejects_unknown_mode(runner):
    call = run_doctor if runner == "python" else run_node_doctor
    code, out = call("--paths", "--mode", "no-such-terminal", "--target", str(SUITE))
    assert code == USAGE, out
    assert "no-such-terminal" in out, out


@pytest.mark.parametrize("runner", ["python", "node"])
def test_doctor_refuses_missing_terminals_spec(runner, tmp_path):
    """拿不到落点定义 = 不知道该装到哪；必须 USAGE_ERROR，不得回退内置默认表。"""
    ghost = str(tmp_path / "nope" / "terminals.json")
    call = run_doctor if runner == "python" else run_node_doctor
    code, out = call("--paths", "--target", str(SUITE), env={"ACS_TERMINALS_PATH": ghost})
    assert code == USAGE, out


@pytest.mark.parametrize("runner", ["python", "node"])
def test_doctor_refuses_spec_without_generic_fallback(runner, tmp_path):
    """detect_order 末项不是 generic 时，识别失败就无处可去 —— 必须当场报错。"""
    spec = _terminals()
    spec["detect_order"] = ["qoder"]
    bad = tmp_path / "terminals.json"
    with io.open(str(bad), "w", encoding="utf-8") as fh:
        json.dump(spec, fh, ensure_ascii=False)
    call = run_doctor if runner == "python" else run_node_doctor
    code, out = call("--paths", "--target", str(SUITE), env={"ACS_TERMINALS_PATH": str(bad)})
    assert code == USAGE, out
    assert "generic" in out, out


def test_node_check_mirrors_install_check():
    """无 Python 环境也得能做装前清单自检，否则 Node 兜底路径等于盲装。"""
    py_code, py_out = run_gate("install_check.py", "--root", SUITE)
    nd_code, nd_out = run_node_gate("check", "--root", str(SUITE))
    assert py_code == nd_code == PASS, (py_out, nd_out)
    assert "PASS" in nd_out, nd_out


# ------------------------------------------------- L3 外部强制点（hook + CI）
HOOK = SUITE / "hooks" / "pre-commit"
CI_WORKFLOW = SUITE / ".github" / "workflows" / "acs-gates.yml"


def _find_git():
    """PATH 里没有 ≠ 机器上没有：IDE 自带的 Git 常不入 PATH。"""
    from shutil import which

    found = which("git")
    if found:
        return found
    home = Path(os.path.expanduser("~"))
    for base in (home / ".qoder" / "bin" / "git", home / ".qoderwork" / "bin" / "git",
                 Path("C:/Program Files/Git"), Path("C:/Program Files (x86)/Git")):
        for rel in ("bin/git.exe", "cmd/git.exe", "bin/git"):
            cand = base / rel
            if cand.is_file():
                return str(cand)
    return None


def _hook_repo(tmp_path, with_state=True, state_obj=None):
    """搭一个真 git 仓库 + 真 .acs 落点，用于实跑 hook。返回 (repo, env) 或 None。"""
    import shutil

    git = _find_git()
    bash = _find_bash()
    if not git or not bash:
        return None
    repo = tmp_path / "repo"
    repo.mkdir()
    proc = subprocess.run([git, "init", "-q", str(repo)], stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT)
    assert proc.returncode == 0, proc.stdout.decode("utf-8", "replace")
    acs = repo / ".acs"
    shutil.copytree(str(SCRIPTS), str(acs / "scripts"))
    # 门禁的阈值真相源必须随装 —— 缺了就是 USAGE_ERROR（它不回退内置默认值）。
    shutil.copytree(str(SUITE / "spec"), str(acs / "spec"))
    # schema 与正样本同样属于安装物：缺 schema 时 state 门直接 USAGE_ERROR。
    shutil.copytree(str(TEMPLATES), str(acs / "templates"))
    if with_state:
        obj = state_obj if state_obj is not None else load("task-state.example.json")
        with io.open(str(acs / "task-state.json"), "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False)
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PATH"] = os.path.dirname(git) + os.pathsep + env.get("PATH", "")
    return repo, env


def _run_hook(repo, env):
    proc = subprocess.run([_find_bash(), str(HOOK)], cwd=str(repo), env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def test_hook_file_present_and_syntax_ok():
    assert HOOK.is_file(), "hooks/pre-commit 不存在，L3 外部强制点只是空话"
    bash = _find_bash()
    if not bash:
        pytest.skip("本机未找到 bash，跳过 sh 语法检查")
    proc = subprocess.run([bash, "-n", str(HOOK)], stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT)
    assert proc.returncode == 0, proc.stdout.decode("utf-8", "replace")


def test_hook_keeps_native_escape_hatch_and_probes_both_runtimes():
    """hook 可以拦人，但不得把主人锁在门外；也不得只会 Python 一种运行时。"""
    src = io.open(str(HOOK), "r", encoding="utf-8").read()
    assert "--no-verify" in src, "必须告知 git 原生逃生口，否则就是接管而非增强"
    assert "git rev-parse --show-toplevel" in src
    assert "run_gates.py" in src and "acs_gates.mjs" in src, "Python 与 Node 两条路都要有"
    for cand in ("python3", "python", "py", "node"):
        assert cand in src, "hook 里没探 %s" % cand
    commands = [l.strip() for l in src.splitlines() if not l.strip().startswith("#")]
    assert not any(l.startswith("git add") for l in commands), "hook 不得自作主张改暂存区"


def test_hook_blocks_when_state_missing(tmp_path):
    """缺字段 = 无法核查 = 视同未做；此时放行等于假强制。"""
    built = _hook_repo(tmp_path, with_state=False)
    if not built:
        pytest.skip("本机未找到 git 或 bash，跳过 hook 实跑")
    repo, env = built
    code, out = _run_hook(repo, env)
    assert code == BLOCK, out
    assert "task-state" in out and "--no-verify" in out, out


def test_hook_blocks_when_gates_dir_missing(tmp_path):
    """hook 在、门禁却没了，不是「无事可做」而是装开了。"""
    built = _hook_repo(tmp_path)
    if not built:
        pytest.skip("本机未找到 git 或 bash，跳过 hook 实跑")
    repo, env = built
    env = dict(env)
    env["ACS_HOME"] = str(tmp_path / "ghost")
    code, out = _run_hook(repo, env)
    assert code == BLOCK, out


def test_hook_passes_on_positive_state(tmp_path):
    """只会拦不会放的门禁会被人直接卸掉，等于没有强制力。"""
    built = _hook_repo(tmp_path)
    if not built:
        pytest.skip("本机未找到 git 或 bash，跳过 hook 实跑")
    repo, env = built
    code, out = _run_hook(repo, env)
    assert code == PASS, out
    assert "PASS" in out, out


def test_hook_blocks_on_bad_state(tmp_path):
    bad = load("task-state.example.json")
    bad.pop("tier", None)
    built = _hook_repo(tmp_path, state_obj=bad)
    if not built:
        pytest.skip("本机未找到 git 或 bash，跳过 hook 实跑")
    repo, env = built
    code, out = _run_hook(repo, env)
    assert code in (BLOCK, USAGE), out


@pytest.mark.parametrize("script,flag", [("install.sh", "--with-hook"), ("install.ps1", "WithHook")])
def test_installers_offer_opt_in_hook(script, flag):
    """未经同意往仓库塞 hook 是接管；故必须是显式 opt-in。"""
    src = io.open(str(SUITE / script), "r", encoding="utf-8").read()
    assert flag in src, "%s 没提供 %s" % (script, flag)
    assert "hooks/pre-commit" in src or "hooks\\pre-commit" in src, script
    assert ".git" in src, "%s 必须先确认目标是 git 仓库" % script


def test_install_sh_makes_hook_executable():
    """没有可执行位的 hook 会被 git 静默忽略 —— 那是装了但没生效。"""
    src = io.open(str(SUITE / "install.sh"), "r", encoding="utf-8").read()
    assert "chmod +x" in src, "install.sh 未给 hook 加可执行位"
    assert '[ -x "$GIT_HOOKS/pre-commit" ]' in src, "加了还得验，否则仍是未验证"


def test_ci_workflow_present_and_covers_acceptance():
    assert CI_WORKFLOW.is_file(), "缺 .github/workflows/acs-gates.yml，CI 强制点不存在"
    src = io.open(str(CI_WORKFLOW), "r", encoding="utf-8").read()
    for needle in ("install_check.py", "gate_reality_scan.py", "run_gates.py",
                   "acs_doctor.py", "pytest", "pack.py",
                   "acs_gates.mjs", "install.sh", "--with-hook", "--no-verify",
                   "acs_global_verify.py"):
        assert needle in src, "CI 里缺了验收命令/证据：%s" % needle
    for needle in ("ubuntu-latest", "macos-latest", "setup-node", "setup-python"):
        assert needle in src, "CI 里缺了环境覆盖：%s" % needle


def test_ci_workflow_parses_as_yaml():
    """写错缩进的 workflow 不会报错，只会不跑 —— 那就是一个看上去很美的假强制点。"""
    yaml = pytest.importorskip("yaml", reason="本机无 pyyaml，CI 上由 GitHub 自行解析")
    with io.open(str(CI_WORKFLOW), "r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    jobs = doc["jobs"]
    assert set(["gates", "node-fallback", "installer", "pre-commit-hook", "global-verify"]).issubset(jobs), sorted(jobs)
    for name, job in jobs.items():
        assert job.get("steps"), "%s 没有 steps" % name


def test_manifest_registers_external_enforcement():
    with io.open(str(SUITE / "manifest.json"), "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    files = manifest["files"]
    for rel in ("hooks/pre-commit", ".github/workflows/acs-gates.yml"):
        assert rel in files, "manifest.files 缺 %s，打包会把强制点丢在外面" % rel
        assert (SUITE / rel).is_file(), rel
    layers = manifest["enforcement_layers"]
    assert set(["L1_soft", "L2_hard", "L3_external"]).issubset(layers), sorted(layers)
    ext = layers["L3_external"]
    assert "--no-verify" in ext["binding"], "逃生口必须写在明处"
    assert "opt_in_reason" in ext


def test_terminals_spec_external_enforcement_paths_exist():
    """spec 里声明的强制点文件必须真存在，否则就是纸面承诺。"""
    ext = _terminals()["external_enforcement"]
    declared = [v for k, v in ext.items() if not k.startswith("_")]
    assert declared
    for text in declared:
        rel = text.split("（")[0].strip()
        assert (SUITE / rel).is_file(), "spec 声明了 %s 却不存在" % rel


# ------------------------------------------------- W8 skill 卫生机检（只读审计）
#
# codex-skill-refactor 治理原则的机检落地：不留电子墓碑、本地链接必须有效、
# spec 节必须有消费者、不维护内容完全相同的两份文件。审计只读不改：
# 发现即测试失败，怎么改由人决定（疑似问题 ≠ 可删除，删除须另行确认）。

_CACHE_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "node_modules"}


def _iter_suite_files():
    for p in SUITE.rglob("*"):
        if p.is_file() and not (_CACHE_DIRS & set(p.parts)):
            yield p


def test_suite_keeps_no_tombstone_files():
    """电子墓碑（.retired/.bak/.old/.orig）：该删就删，git 里留着历史，工作区里不留尸。"""
    tombstones = [str(p.relative_to(SUITE)) for p in _iter_suite_files()
                  if p.suffix.lower() in (".retired", ".bak", ".old", ".orig")]
    assert not tombstones, "发现墓碑文件（留历史去 git，工作区不留）：\n%s" % "\n".join(tombstones)


def test_markdown_local_links_resolve():
    """本地 Markdown 链接必须指向真实文件：失效链接是「维护没发生」的化石。"""
    import re as _re

    pat = _re.compile(r"\[[^\]]*\]\(([^)]+)\)")
    broken = []
    for p in SUITE.rglob("*.md"):
        if _CACHE_DIRS & set(p.parts):
            continue
        text = io.open(str(p), "r", encoding="utf-8").read()
        for target in pat.findall(text):
            if target.startswith(("http://", "https://", "mailto:", "#", "<")):
                continue
            rel = target.split("#")[0].strip()
            if not rel:
                continue
            if not (p.parent / rel).exists():
                broken.append("%s → %s" % (p.relative_to(SUITE), target))
    assert not broken, "失效的本地链接（指向不存在的文件）：\n%s" % "\n".join(broken)


def test_spec_sections_have_consumers():
    """spec 每个行为节必须有实现消费者：没人读的配置就是死配置，改了也不生效。"""
    meta = {"_readme", "exit_codes", "tiers", "spec_version"}
    spec = _spec()
    sources = {}
    for f in list(SCRIPTS.glob("*.py")) + [NODE_GATE]:
        sources[str(f)] = io.open(str(f), "r", encoding="utf-8").read()
    dead = []
    for key in spec:
        if key in meta or key.startswith("_"):
            continue
        if not any(('spec_section("%s")' % key) in src or ("SPEC.%s" % key) in src
                   for src in sources.values()):
            dead.append(key)
    assert not dead, "spec 节无任何实现消费者（死配置：删掉或接上）：\n%s" % "\n".join(dead)


def _build_qwenwork_fixture(tmp_path):
    """构造隔离的千问办公全局目录，避免单测依赖真实用户目录。"""
    home = tmp_path / ".qwenworkcn"
    awareness = home / "awareness" / "main"
    awareness.mkdir(parents=True)
    contract = load("../spec/qwenwork-global.json")
    for filename, anchors in contract["required_anchors"].items():
        (awareness / filename).write_text("\n".join(anchors), encoding="utf-8")
    shutil.copytree(SUITE, home / "agent-core-suite")
    skills = home / "skills"
    skills.mkdir()
    for name in contract["required_skills"]:
        shutil.copytree(SUITE / "skills" / name, skills / name)
    return home


def test_qwenwork_global_verify_positive(tmp_path):
    """隔离配置的静态完整性检查必须 PASS，并创建一份不可覆盖的证据。"""
    home = _build_qwenwork_fixture(tmp_path)
    code, out = run_gate("qwenwork_global_verify.py", "--home", home, "--json")
    assert code == PASS, out
    payload = json.loads(out)
    assert payload["status"] == "STATIC_PASS"
    assert payload["verification_kind"] == "static_configuration_integrity"
    assert len(payload["skills"]) == 5
    assert all(item["identical"] for item in payload["skills"].values())


def test_qwenwork_global_verify_blocks_missing_anchor(tmp_path):
    """全局规则缺静态锚点时必须 BLOCK，禁止用文件存在冒充配置完整。"""
    home = _build_qwenwork_fixture(tmp_path)
    agents = home / "awareness" / "main" / "AGENTS.md"
    agents.write_text("不完整规则", encoding="utf-8")
    code, out = run_gate("qwenwork_global_verify.py", "--home", home)
    assert code == BLOCK, out
    assert "缺少静态锚点" in out


def test_qwenwork_global_verify_rejects_empty_contract(tmp_path):
    """空契约不能退化为零检查 PASS。"""
    contract = tmp_path / "empty.json"
    contract.write_text("{}", encoding="utf-8")
    code, out = run_gate("qwenwork_global_verify.py", "--spec", contract, "--home", tmp_path)
    assert code == USAGE, out
    assert "契约缺少字段" in out


def test_qwenwork_global_verify_is_readonly():
    """全局验证器不得含写文件或建目录副作用。"""
    src = io.open(str(SCRIPTS / "qwenwork_global_verify.py"), "r", encoding="utf-8").read()
    bad = _scan_side_effects(src, "qwenwork_global_verify.py")
    assert not bad, "\n".join(bad)


def test_qwenwork_global_verify_rejects_parent_traversal(tmp_path):
    """契约路径不得通过 .. 越出千问办公资源目录。"""
    contract = load("../spec/qwenwork-global.json")
    contract["paths"]["skills_dir"] = "../outside"
    path = dump(tmp_path, contract, "bad-global-spec.json")
    code, out = run_gate("qwenwork_global_verify.py", "--spec", path, "--home", tmp_path)
    assert code == USAGE, out
    assert "相对路径" in out


def _build_qoderwork_fixture(tmp_path):
    """构造隔离的 QoderWork 全局目录，避免单测依赖真实用户目录。"""
    home = tmp_path / ".qoderwork"
    awareness = home / "awareness" / "main"
    awareness.mkdir(parents=True)
    contract = load("../spec/qoderwork-global.json")
    for filename, anchors in contract["required_anchors"].items():
        (awareness / filename).write_text("\n".join(anchors), encoding="utf-8")
    shutil.copytree(SUITE, home / "agent-core-suite",
                    ignore=shutil.ignore_patterns("__pycache__", ".git", ".acs"))
    skills = home / "skills"
    skills.mkdir()
    for name in contract["required_skills"]:
        shutil.copytree(SUITE / "skills" / name, skills / name)
    return home


def test_qoderwork_global_verify_positive(tmp_path):
    """多产品引擎对 qoderwork 同样必须 PASS，并产出结构化静态证据。"""
    home = _build_qoderwork_fixture(tmp_path)
    code, out = run_gate("acs_global_verify.py", "--product", "qoderwork", "--home", home, "--json")
    assert code == PASS, out
    payload = json.loads(out)
    assert payload["status"] == "STATIC_PASS"
    assert payload["product"] == "qoderwork"
    assert payload["verification_kind"] == "static_configuration_integrity"
    assert len(payload["skills"]) == 5
    assert all(item["identical"] for item in payload["skills"].values())


def test_qoderwork_global_verify_blocks_missing_anchor(tmp_path):
    """QoderWork 全局规则缺静态锚点时必须 BLOCK，禁止用文件存在冒充配置完整。"""
    home = _build_qoderwork_fixture(tmp_path)
    agents = home / "awareness" / "main" / "AGENTS.md"
    agents.write_text("不完整规则", encoding="utf-8")
    code, out = run_gate("acs_global_verify.py", "--product", "qoderwork", "--home", home)
    assert code == BLOCK, out
    assert "缺少静态锚点" in out


def test_qoderwork_global_verify_rejects_empty_contract(tmp_path):
    """空契约不能退化为零检查 PASS（qoderwork 侧）。"""
    contract = tmp_path / "empty-qoderwork.json"
    contract.write_text("{}", encoding="utf-8")
    code, out = run_gate("acs_global_verify.py", "--product", "qoderwork", "--spec", contract, "--home", tmp_path)
    assert code == USAGE, out
    assert "契约缺少字段" in out


def test_qoderwork_global_verify_rejects_parent_traversal(tmp_path):
    """契约路径不得通过 .. 越出 QoderWork 资源目录。"""
    contract = load("../spec/qoderwork-global.json")
    contract["paths"]["skills_dir"] = "../outside"
    path = dump(tmp_path, contract, "bad-qoderwork-spec.json")
    code, out = run_gate("acs_global_verify.py", "--product", "qoderwork", "--spec", path, "--home", tmp_path)
    assert code == USAGE, out
    assert "相对路径" in out


def test_acs_global_verify_rejects_unknown_product(tmp_path):
    """未登记产品必须 USAGE_ERROR，而不是静默按默认产品跑。"""
    code, out = run_gate("acs_global_verify.py", "--product", "nosuchproduct", "--home", tmp_path)
    assert code == USAGE, out
    assert "未知产品" in out


def test_acs_global_verify_rejects_product_contract_mismatch(tmp_path):
    """--product 与契约 product 字段不符必须 USAGE_ERROR（防张冠李戴拿错契约）。"""
    contract = load("../spec/qoderwork-global.json")   # 其 product 字段为 qoderwork
    path = dump(tmp_path, contract, "mismatch-qoderwork.json")
    code, out = run_gate("acs_global_verify.py", "--product", "qwenworkcn", "--spec", path, "--home", tmp_path)
    assert code == USAGE, out
    assert "product 必须是" in out


def _import_engine():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import acs_global_verify as agv
    return agv


def test_multi_product_registry_contracts_all_validate():
    """PRODUCTS 登记表里每个产品都必须有一份能通过 validate_spec 的契约，且 product/版本自洽。"""
    agv = _import_engine()
    assert {"qoderwork", "qwenworkcn"}.issubset(set(agv.PRODUCTS))
    for product, filename in agv.PRODUCTS.items():
        spec_path = agv.default_spec_path(product)
        assert spec_path.name == filename, (product, spec_path.name, filename)
        assert spec_path.is_file(), "登记了 %s 却缺契约文件 %s" % (product, spec_path)
        spec = agv.validate_spec(agv.load_json(spec_path), product)
        assert spec["product"] == product
        assert spec["spec_version"] == agv.CONTRACT_VERSION


def _contract_parity_diffs(a, b, contract_version,
                           a_self="spec/qoderwork-global.json",
                           b_self="spec/qwenwork-global.json"):
    """返回两份契约「应当平行却未平行」的违规清单（空=平行）。抽成函数以便正/负样本共用。"""
    diffs = []
    if not (a["spec_version"] == b["spec_version"] == contract_version):
        diffs.append("spec_version 不对齐：%s / %s / %s" % (a["spec_version"], b["spec_version"], contract_version))
    if sorted(a["required_skills"]) != sorted(b["required_skills"]):
        diffs.append("required_skills 不平行")
    a_files = set(a["required_suite_files"]) - {a_self}
    b_files = set(b["required_suite_files"]) - {b_self}
    if a_files != b_files:
        diffs.append("required_suite_files 不平行：差集=%s" % sorted(a_files ^ b_files))
    if a_self not in a["required_suite_files"]:
        diffs.append("qoderwork 契约未自引用登记")
    if b_self not in b["required_suite_files"]:
        diffs.append("qwenworkcn 契约未自引用登记")
    common_routing = {"clarification", "progress", "handoff", "parallelism",
                      "skills", "memory", "file_delivery"}
    for label, spec in (("qoderwork", a), ("qwenworkcn", b)):
        miss = common_routing - set(spec["native_routing"])
        if miss:
            diffs.append("%s 缺公共能力路由键：%s" % (label, sorted(miss)))
        for key in common_routing & set(spec["native_routing"]):
            val = spec["native_routing"][key]
            if not (isinstance(val, str) and val.strip()):
                diffs.append("%s.%s 路由值为空" % (label, key))
        if len([k for k in spec["native_routing"] if k.endswith("_state")]) != 1:
            diffs.append("%s 的 *_state 路由键不唯一" % label)
    if a["required_anchors"]["SOUL.md"] != b["required_anchors"]["SOUL.md"]:
        diffs.append("SOUL.md 锚点不平行")
    if sorted(a["required_anchors"]) != sorted(b["required_anchors"]) or \
            sorted(a["required_anchors"]) != ["AGENTS.md", "SOUL.md"]:
        diffs.append("required_anchors 键集不平行")
    else:
        a_ag, b_ag = a["required_anchors"]["AGENTS.md"], b["required_anchors"]["AGENTS.md"]
        shared = set(a_ag) & set(b_ag)
        for anchor in ("Agent Core Suite v2.1.0", "通用任务执行守则（十八条）",
                       "Harness Engineering 质量门禁", "0=PASS"):
            if anchor not in shared:
                diffs.append("AGENTS.md 共享锚点缺失：%s" % anchor)
        a_only, b_only = set(a_ag) - shared, set(b_ag) - shared
        if not (len(a_only) == 1 and len(b_only) == 1 and
                "融合协议" in next(iter(a_only)) and "融合协议" in next(iter(b_only))):
            diffs.append("AGENTS.md 差异不止产品融合协议短语：%s vs %s" % (sorted(a_only), sorted(b_only)))
    return diffs


def test_dual_contracts_are_parallel():
    """两份产品契约必须平行。v2.0.0 曾因 qwenworkcn 契约漂移导致假阳性，本测试把
    「双产品平行」变成机检约束——但只约束**应当平行**的维度：

    平行维度（必须一致）：spec_version、required_skills、required_suite_files（各自排除
      自引用的本产品契约文件后必须逐字一致）、SOUL.md 锚点、AGENTS.md 共享锚点、
      v2.1 公共能力路由键（含 handoff）。
    产品专有维度（允许不同，且必须体现产品差异）：native_routing 里的 *_state 键名、
      qoderwork 独有的 mcp_runtime/scheduling 原生面、AGENTS.md 里那条产品融合协议短语。
    """
    agv = _import_engine()
    a = agv.load_json(agv.default_spec_path("qoderwork"))
    b = agv.load_json(agv.default_spec_path("qwenworkcn"))
    diffs = _contract_parity_diffs(a, b, agv.CONTRACT_VERSION)
    assert not diffs, "双产品契约未平行：\n" + "\n".join(diffs)


def test_contract_parity_detector_catches_drift():
    """反向自证：把平行契约人为改坏，检测器必须逐类报错——否则上面的全绿可能只是空断言。"""
    import copy as _copy

    agv = _import_engine()
    base_a = agv.load_json(agv.default_spec_path("qoderwork"))
    base_b = agv.load_json(agv.default_spec_path("qwenworkcn"))
    cv = agv.CONTRACT_VERSION
    assert _contract_parity_diffs(base_a, base_b, cv) == [], "基线本应平行"

    cases = []
    # 版本漂移
    m = _copy.deepcopy(base_b); m["spec_version"] = "9.9.9"
    cases.append((base_a, m, "spec_version"))
    # 打包面漏文件
    m = _copy.deepcopy(base_b); m["required_suite_files"].remove("scripts/acs_compress_handoff.py")
    cases.append((base_a, m, "required_suite_files"))
    # 丢 handoff 路由键
    m = _copy.deepcopy(base_b); m["native_routing"].pop("handoff")
    cases.append((base_a, m, "handoff"))
    # SOUL 锚点漂移
    m = _copy.deepcopy(base_b); m["required_anchors"]["SOUL.md"].append("多出来的一条")
    cases.append((base_a, m, "SOUL.md"))
    for a_, b_, expect in cases:
        diffs = _contract_parity_diffs(a_, b_, cv)
        assert diffs, "改了 %s 却没被检出" % expect
        assert any(expect in d for d in diffs), (expect, diffs)


def test_engine_derives_terminals_wiring_from_contract():
    """引擎从契约派生的 terminals 接线必须与 spec/terminals.json 实际接线逐字一致——这是「引擎零产品特化」的证据。"""
    agv = _import_engine()
    terminals = _terminals()
    order = terminals["detect_order"]
    assert order[-1] == "generic", "detect_order 末项必须是 generic 兜底"
    for product in agv.PRODUCTS:
        assert product in order, "detect_order 缺产品 %s" % product
        spec = agv.load_json(agv.default_spec_path(product))
        wanted = agv.expected_terminals_wiring(product, spec["paths"])
        actual = terminals["terminals"].get(product) or {}
        for key, val in wanted.items():
            assert actual.get(key) == val, "%s.%s 期望 %r 实际 %r" % (product, key, val, actual.get(key))


def test_suite_keeps_no_identical_duplicate_files():
    """内容完全相同的两份文件 = 同一条规则两处维护：改一处漏一处只是时间问题。"""
    import hashlib

    seen = {}
    dups = []
    for p in _iter_suite_files():
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        if digest in seen:
            dups.append("%s == %s" % (seen[digest], p.relative_to(SUITE)))
        else:
            seen[digest] = str(p.relative_to(SUITE))
    assert not dups, "发现内容完全相同的文件对（收敛为一份，引用指向它）：\n%s" % "\n".join(dups)



# ------------------------------------------------- v1.2 三道新门禁（rule / skillspec / honesty）
#
# 借鉴来源：ponytail check-rule-copies（规则副本漂移）、anthropics/skills quick_validate
# （SKILL.md 规范）、caveman 证据分级 + cavecrew 子代理契约 + 三臂评测、ponytail judge --selftest。
# 均为方法学迁移与本地重实现；上游收益数字未在本机复现，故不作为本套件主张。

V12_GATES = ("gate_rule_consistency.py", "gate_skill_spec.py", "gate_honesty_ledger.py")


def _suite_copy(tmp_path, name="suite"):
    """复制整棵套件树：新门禁按 --root 解析各自 spec，必须在副本上验证。"""
    dst = tmp_path / name
    shutil.copytree(str(SUITE), str(dst), ignore=shutil.ignore_patterns(
        ".git", "__pycache__", ".pytest_cache"))
    return dst


@pytest.mark.parametrize("script", V12_GATES)
def test_v12_gates_registered_in_manifest(script):
    """新门禁必须进清单：不进清单安装就不拷，装完即 USAGE_ERROR。"""
    data = json.loads(io.open(str(SUITE / "manifest.json"), "r", encoding="utf-8").read())
    rel = "scripts/%s" % script
    assert rel in data["files"], rel
    ids = [g.get("script") for g in data.get("gates") or []]
    assert rel in ids, ids


@pytest.mark.parametrize("script,spec_rel", [
    ("gate_rule_consistency.py", "spec/rule-consistency.json"),
    ("gate_skill_spec.py", "spec/skill-spec.json"),
])
def test_v12_gates_hard_fail_when_own_spec_missing(tmp_path, script, spec_rel):
    """真相源必须来自被检查的那棵树。

    首版实测缺陷：spec 路径固定解析到安装目录，检查副本时读到本机真 spec，
    副本里 spec 被删也照样 PASS —— 那是「拿不到真相源却静默继续」。
    """
    root = _suite_copy(tmp_path, script.replace(".py", ""))
    (root / spec_rel).unlink()
    code, out = run_gate(script, "--root", root)
    assert code == USAGE, out


def test_rule_consistency_positive():
    code, out = run_gate("gate_rule_consistency.py", "--root", SUITE)
    assert code == PASS, out


def test_rule_consistency_accepts_external_global_rules(tmp_path):
    """外部全局规则文件同步时必须放行；条数漂移必须拦。"""
    spec = json.loads(io.open(str(SUITE / "spec" / "rule-consistency.json"),
                              "r", encoding="utf-8").read())
    want = spec["canonical_creed"]["count_word"]
    phrases = spec["invariants"]["phrases"]
    good = tmp_path / "GOOD_SOUL.md"
    io.open(str(good), "w", encoding="utf-8").write(
        "守则（%s）\n%s\n" % (want, "\n".join(phrases)))
    code, out = run_gate("gate_rule_consistency.py", "--root", SUITE, "--external", good)
    assert code == PASS, out
    bad = tmp_path / "BAD_SOUL.md"
    io.open(str(bad), "w", encoding="utf-8").write(
        "守则（十五条）\n%s\n" % "\n".join(phrases))
    code, out = run_gate("gate_rule_consistency.py", "--root", SUITE, "--external", bad)
    assert code == BLOCK, out


@pytest.mark.parametrize("rel,old,new,keyword", [
    ("skills/universal-task-code/SKILL.md", "十八条守则", "十五条守则", "过时条数声明"),
    ("skills/universal-task-code/reference-creed.md", "零降级实现容忍", "弹性降级", "承重短语丢失"),
    ("AGENTS.md", "two-strike", "兜底策略", "副本缺少"),
])
def test_rule_consistency_negative(tmp_path, rel, old, new, keyword):
    """规则漂移的三种形态都必须被抓住，否则 PASS 毫无意义。"""
    root = _suite_copy(tmp_path, "drift")
    target = root / rel
    text = io.open(str(target), "r", encoding="utf-8").read()
    assert old in text, "测试前提不成立：%s 里没有该锚点" % rel
    io.open(str(target), "w", encoding="utf-8").write(text.replace(old, new))
    code, out = run_gate("gate_rule_consistency.py", "--root", root)
    assert code == BLOCK, out
    assert keyword in out, out


def test_skill_spec_positive():
    code, out = run_gate("gate_skill_spec.py", "--root", SUITE)
    assert code == PASS, out


def test_skill_spec_entry_must_declare_skip_boundary(tmp_path):
    """入口技能不写 SKIP 边界必须拦。

    官方明确 description 是唯一触发机制；只写「何时用」不写「何时不用」，
    会把 T0 闲聊也拖进重流水线。
    """
    root = _suite_copy(tmp_path, "noskip")
    spec = json.loads(io.open(str(root / "spec" / "skill-spec.json"),
                              "r", encoding="utf-8").read())
    target = root / "skills" / spec["entry_skills"][0] / "SKILL.md"
    text = io.open(str(target), "r", encoding="utf-8").read()
    cleaned = []
    for line in text.splitlines():
        if line.startswith("description:"):
            for kw in spec["description"]["skip_keywords"]:
                line = line.replace(kw, "见正文")
        cleaned.append(line)
    io.open(str(target), "w", encoding="utf-8").write("\n".join(cleaned))
    code, out = run_gate("gate_skill_spec.py", "--root", root)
    assert code == BLOCK, out
    assert "SKIP" in out, out


@pytest.mark.parametrize("old,new,keyword", [
    ("name: token-thrift", "name: token-thrift\nauthor: x", "非白名单键"),
    ("name: token-thrift", "name: token-thrifty", "与目录名"),
    ("Context 层省 token 工程", "Context <层> 省 token 工程", "禁用字符"),
])
def test_skill_spec_negative(tmp_path, old, new, keyword):
    root = _suite_copy(tmp_path, "skillbad")
    target = root / "skills" / "token-thrift" / "SKILL.md"
    text = io.open(str(target), "r", encoding="utf-8").read()
    assert old in text, old
    io.open(str(target), "w", encoding="utf-8").write(text.replace(old, new, 1))
    code, out = run_gate("gate_skill_spec.py", "--root", root)
    assert code == BLOCK, out
    assert keyword in out, out


def test_honesty_positive(state, tmp_path):
    code, out = run_gate("gate_honesty_ledger.py", "--state",
                         dump(tmp_path, state), "--tier", "T2")
    assert code == PASS, out


def _set_basis_inferred(s):
    s["cost_metering"]["basis"] = "inferred"


def _upgrade_component(s):
    s["cost_metering"]["components"][0]["basis"] = "inferred"


def _drop_evidence_ref(s):
    s["cost_metering"].pop("evidence_ref")


def _drop_upgrade_path(s):
    s["degradations"][0].pop("upgrade_path")


def _drop_terse_arm(s):
    runs = [r for r in s["behavior_eval"]["runs"] if r["arm"] != "baseline_terse"]
    s["behavior_eval"]["runs"] = runs


def _judge_ranked_wrong(s):
    s["judge_selftest"]["ranked_correctly"] = False


@pytest.mark.parametrize("mutate,keyword,tier", [
    (_set_basis_inferred, "不得冒充计量", "T2"),
    (_upgrade_component, "静默换档", "T2"),
    (_drop_evidence_ref, "evidence_ref", "T2"),
    (_drop_upgrade_path, "掩盖", "T2"),
    (_drop_terse_arm, "baseline_terse", "T2"),
    (_judge_ranked_wrong, "refusing to rank", "T3"),
])
def test_honesty_negative(state, tmp_path, mutate, keyword, tier):
    mutate(state)
    if tier == "T3":
        state["tier"] = "T3"
    code, out = run_gate("gate_honesty_ledger.py", "--state",
                         dump(tmp_path, state), "--tier", tier)
    assert code == BLOCK, out
    assert keyword in out, out


def test_honesty_t2_requires_cost_metering(state, tmp_path):
    """T2+ 必须显式声明计量来源：沉默不算声明。"""
    state.pop("cost_metering")
    code, out = run_gate("gate_honesty_ledger.py", "--state",
                         dump(tmp_path, state), "--tier", "T2")
    assert code == BLOCK, out
    assert "沉默不算声明" in out, out


def test_honesty_subagent_builder_file_cap(state, tmp_path):
    """builder 子代理拒绝超上限任务：过大的任务必须先拆，不是硬做。"""
    state["tier"] = "T3"
    node = state["graph"]["nodes"][1]
    node["subagent"] = {"role": "builder", "empty_literal": "No issues."}
    node["outputs"] = ["a.py", "b.py", "c.py", "d.py"]
    code, out = run_gate("gate_honesty_ledger.py", "--state",
                         dump(tmp_path, state), "--tier", "T3")
    assert code == BLOCK, out
    assert "必须先拆" in out, out


def test_run_gates_includes_v12_gates(tmp_path, state):
    """编排器必须真的跑到新门禁，否则新门禁形同不存在。"""
    code, out = run_gate("run_gates.py", "--state", dump(tmp_path, state),
                         "--root", SUITE, "--tier", "T2")
    assert code == PASS, out
    for name in ("honesty", "rule", "skillspec"):
        assert name in out, (name, out)


def test_run_gates_states_skip_honestly_without_spec(tmp_path, state):
    """业务工作区没有规则/技能 spec 时，必须显式说跳过而不是假装检查过。"""
    workspace = tmp_path / "biz"
    workspace.mkdir()
    code, out = run_gate("run_gates.py", "--state", dump(tmp_path, state),
                         "--root", workspace, "--tier", "T2")
    assert "非通过" in out, out


# ------------------------------------------------- v1.2 可移植性与 bootstrap 注入层
#
# 来源：superpowers docs/porting-to-a-new-harness.md —— 「The bootstrap is the entire
# integration」。没有注入，技能只是磁盘上的死文件。本套件原先只靠终端被动加载规则，
# 无 hook 保底、也无任何断言证明「规则真的到了模型面前」，这一组测试补的正是那一环。

BOOTSTRAP_PROFILE_SHAPES = [
    ("claude", "hookSpecificOutput"),
    ("cursor", "additional_context"),
    ("qwenworkcn", "additionalContext"),
    ("qoderwork", "additionalContext"),
    ("generic", "additionalContext"),
]


def _adapters():
    return json.loads(io.open(str(SUITE / "spec" / "adapters.json"),
                              "r", encoding="utf-8").read())


def _bootstrap_json(profile):
    code, out = run_gate("acs_bootstrap.py", "--root", SUITE, "--profile", profile)
    assert code == PASS, out
    return json.loads(out)


@pytest.mark.parametrize("profile,shape_key", BOOTSTRAP_PROFILE_SHAPES)
def test_bootstrap_emits_terminal_specific_shape(profile, shape_key):
    """各终端读不同字段名，形状写错等于没注入 —— 这是移植时最易错的一步。"""
    payload = _bootstrap_json(profile)
    assert shape_key in payload, (profile, sorted(payload))
    inner = (payload["hookSpecificOutput"]["additionalContext"]
             if profile == "claude" else payload[shape_key])
    assert "EXTREMELY_IMPORTANT_AGENT_CORE_SUITE" in inner, profile
    assert payload["_acs"]["enforcement_level"] in ("full", "partial", "soft_only")


def test_bootstrap_payload_carries_portable_rules():
    """注入载荷必须真的含规则正文，不能只有壳。"""
    adapters = _adapters()
    payload = _bootstrap_json("qwenworkcn")
    assert payload["_acs"]["rule_files"] == adapters["portable_core"]["rules"], payload["_acs"]
    inner = payload["additionalContext"]
    assert len(inner) > 1000, len(inner)


def test_bootstrap_declares_capability_gaps_for_generic():
    """未识别终端必须如实列出能力缺口与 fallback，而不是假装全都有。"""
    payload = _bootstrap_json("generic")
    gaps = {g["capability"] for g in payload["_acs"]["capability_gaps"]}
    assert "clarify" in gaps and "parallel" in gaps, payload["_acs"]
    for gap in payload["_acs"]["capability_gaps"]:
        assert gap["fallback"].strip(), gap


def test_bootstrap_blocks_when_rules_missing(tmp_path):
    """规则缺失即不可注入：此时必须 BLOCK，不能输出半截载荷。"""
    root = _suite_copy(tmp_path, "norules")
    (root / "AGENTS.md").unlink()
    code, out = run_gate("acs_bootstrap.py", "--root", root, "--verify")
    assert code == BLOCK, out
    assert "无法注入" in out, out


def test_bootstrap_rejects_unknown_profile():
    code, out = run_gate("acs_bootstrap.py", "--root", SUITE, "--profile", "no-such-terminal")
    assert code == USAGE, out


def test_bootstrap_is_readonly():
    """注入层必须纯只读：它产出载荷，注入动作交给 hook/CI/人工。"""
    src = io.open(str(SCRIPTS / "acs_bootstrap.py"), "r", encoding="utf-8").read()
    assert not _scan_side_effects(src, "acs_bootstrap.py")


def test_adapters_spec_declares_model_agnostic_facts():
    """跨模型的前提必须落在 spec 里并可核查，而不是靠宣传语。"""
    adapters = _adapters()
    ma = adapters["model_agnostic"]
    assert ma["no_model_api_calls"] is True
    assert ma["no_provider_endpoints"] is True
    assert ma["logits_dependency"] == "none"


def test_portable_core_files_all_exist():
    """portable_core 声明的每一项都必须真实存在，否则「跨终端可用」是空话。

    遍历除 _note 外的全部键（rules/skills/gates/specs/tools/hooks/bundled_skills/contracts），
    不预设固定键名清单——将来新增一类便携核心而忘了核验存在性，本测试会因未覆盖键而失败。
    """
    adapters = _adapters()
    core = adapters["portable_core"]
    covered = set()
    missing = []
    for key, val in core.items():
        if key == "_note":
            continue
        assert isinstance(val, list), "portable_core[%s] 必须是路径清单，实际 %r" % (key, type(val))
        covered.add(key)
        for rel in val:
            if not (SUITE / rel).exists():
                missing.append("%s -> %s" % (key, rel))
    expected = {"rules", "skills", "gates", "specs", "tools", "hooks", "bundled_skills", "contracts"}
    assert covered == expected, \
        "portable_core 键集变化：新增键须纳入存在性核验，删除键须同步本测试。差集=%s" % (covered ^ expected)
    assert not missing, "便携核心声明的文件缺失：%s" % missing


def test_manifest_registers_portability_and_bootstrap():
    data = json.loads(io.open(str(SUITE / "manifest.json"), "r", encoding="utf-8").read())
    assert data["entry"].get("bootstrap") == "scripts/acs_bootstrap.py", data["entry"]
    assert "scripts/acs_bootstrap.py" in data["files"]
    assert "spec/adapters.json" in data["files"]
    assert data["adapters_spec"]["role"] == "single-source-of-truth", data.get("adapters_spec")
    levels = data["portability"]["enforcement_levels"]
    assert any("soft_only" in x for x in levels), levels


def test_no_forbidden_overclaims_in_docs():
    """禁止夸大声明必须真的不出现在规则与文档里。

    spec/adapters.json 自身列出这些禁止项（那是定义），故排除它自己。
    """
    adapters = _adapters()
    forbidden = adapters["enforcement_honesty"]["forbidden_claims"]
    assert forbidden, "禁止声明清单不得为空"
    targets = ["AGENTS.md", "rules/agent-core-suite.md",
               "skills/universal-task-code/SKILL.md"]
    for rel in targets:
        text = io.open(str(SUITE / rel), "r", encoding="utf-8").read()
        # 反向断言：文档必须明确说 L3 不覆盖非 Git / 未公开 hook，而不是宣称覆盖
        assert "全局 Hook" not in text or "不" in text, rel


# ------------------------------------------------- v1.2 能力覆盖清单（打包范围的诚实边界）
#
# 回答「能不能把 qoder/qoderwork/qwenwork 全部能力打包进来」：便携包物理上无法打包
# 另一产品的运行时（MCP/toolcall/编排引擎/记忆索引/任务库）。能打包的是每个能力的
# 可移植纪律层+契约层+机检门禁；运行时缺位走 fallback 并声明强制力。本组测试把这一
# 边界变成机检约束，防止「便携包宣称自足包含运行时」的夸大。

RUNTIME_ONLY_DOMAINS = {"mcp 连接器", "toolcall 工具调用", "跨对话记忆 cross-conversation memory",
                        "长期记忆 long-term memory", "qoderwork connector 应用编排",
                        "定时任务 scheduling/cron"}


def _capability_coverage():
    return json.loads(io.open(str(SUITE / "spec" / "capability-coverage.json"),
                              "r", encoding="utf-8").read())


def test_capability_coverage_registered():
    data = json.loads(io.open(str(SUITE / "manifest.json"), "r", encoding="utf-8").read())
    assert "spec/capability-coverage.json" in data["files"]
    assert data["capability_coverage_spec"]["role"] == "single-source-of-truth"


def test_capability_coverage_shape_and_counts():
    """每个能力域必须四项齐全，且汇总计数与逐项一致（防清单注水）。"""
    cov = _capability_coverage()
    caps = cov["capabilities"]
    required = ("domain", "portable_layer", "runtime_layer", "in_pack", "fallback")
    for cap in caps:
        for field in required:
            assert field in cap, (cap.get("domain"), field)
        assert isinstance(cap["in_pack"], bool), cap
        assert cap["fallback"].strip(), cap
    s = cov["summary"]
    assert s["total_domains"] == len(caps), (s["total_domains"], len(caps))
    assert s["in_pack_true"] == sum(1 for c in caps if c["in_pack"]), s
    assert s["in_pack_false"] == sum(1 for c in caps if not c["in_pack"]), s


def test_capability_coverage_no_false_runtime_claim():
    """in_pack=false 必须是纯运行时域；in_pack=true 的 portable_layer 不得宣称打包运行时。"""
    cov = _capability_coverage()
    forbidden_runtime_words = ("MCP 服务器本体", "toolcall 引擎本体", "agents.db",
                               "SQLite 记忆索引本体")
    for cap in cov["capabilities"]:
        if not cap["in_pack"]:
            assert cap["domain"] in RUNTIME_ONLY_DOMAINS,                 ("in_pack=false 但不是已知运行时域：%s" % cap["domain"])
        for word in forbidden_runtime_words:
            assert word not in cap["portable_layer"],                 ("%s 的 portable_layer 宣称打包运行时 %s" % (cap["domain"], word))


def test_capability_coverage_has_forbidden_claim_guard():
    """必须显式声明禁止夸大：便携包不自足包含另一产品运行时。"""
    cov = _capability_coverage()
    fc = cov["summary"]["forbidden_claim"]
    assert "运行时" in fc and "禁止" in fc, fc
    rule = cov["packaging_rule"]
    assert "not_portable" in rule and "native-first" in rule["handling"], rule


def test_capability_coverage_runtime_domains_all_have_fallback():
    """运行时域虽不可打包，但必须给可移植降级载体，否则守则在该终端落空。"""
    cov = _capability_coverage()
    for cap in cov["capabilities"]:
        if cap["domain"] in RUNTIME_ONLY_DOMAINS:
            assert cap["in_pack"] is False, cap["domain"]
            assert len(cap["fallback"]) >= 4, cap["fallback"]


# ------------------------------------------------- v2.1 方向1：压缩交接载体渲染/校验器
#
# acs_compress_handoff.py 只做**机械渲染 + 形状机检**，不做语义压缩（那是 agent 原生职责）。
# 这组测试证明：渲染幂等、六段结构齐、无镜像时拒绝伪造（exit 2）、载体超硬上限必 BLOCK、
# 模式互斥、以及它被登记进清单与便携核心（否则装完即缺失）。

_COMPRESS = "acs_compress_handoff.py"
_SIX_SECTIONS = ("当前目标", "已确认事实", "已完成产出", "未解决问题", "下一步入口", "状态指针")


def test_compress_handoff_render_positive_and_idempotent(tmp_path):
    """从 example 末步镜像渲染 → PASS；同状态重渲必须逐字节相同（幂等=可安全反复调用）。"""
    state_path = tmp_path / "state.json"
    shutil.copy(str(TEMPLATES / "task-state.example.json"), str(state_path))
    out1 = tmp_path / "h1.md"
    out2 = tmp_path / "h2.md"
    code1, o1 = run_gate(_COMPRESS, "--render", "--state", state_path, "--out", out1, "--tier", "T2")
    code2, o2 = run_gate(_COMPRESS, "--render", "--state", state_path, "--out", out2, "--tier", "T2")
    assert code1 == PASS == code2, (o1, o2)
    assert out1.is_file() and out2.is_file()
    assert out1.read_bytes() == out2.read_bytes(), "同状态两次渲染字节不一致：非幂等，反复调用会漂移"


def test_compress_handoff_render_writes_six_sections(tmp_path):
    """载体必须含六段锚点结构——缺段就是有损交接。"""
    state_path = tmp_path / "state.json"
    shutil.copy(str(TEMPLATES / "task-state.example.json"), str(state_path))
    out = tmp_path / "handoff.md"
    code, o = run_gate(_COMPRESS, "--render", "--state", state_path, "--out", out)
    assert code == PASS, o
    text = out.read_text(encoding="utf-8")
    for sec in _SIX_SECTIONS:
        assert sec in text, "渲染载体缺段落锚点：%s\n%s" % (sec, text)


def test_compress_handoff_render_refuses_to_fabricate_without_mirror(tmp_path):
    """末步无 handoff 镜像时，渲染器必须 exit 2（绝不凭空伪造交接内容）。"""
    state = copy.deepcopy(load("task-state.example.json"))
    for step in state.get("steps", []):
        step.pop("handoff", None)
    state_path = dump(tmp_path, state)
    out = tmp_path / "handoff.md"
    code, o = run_gate(_COMPRESS, "--render", "--state", state_path, "--out", out)
    assert code == USAGE, "无镜像却未 USAGE_ERROR（可能伪造了交接）：%s" % o


def test_compress_handoff_validate_positive(tmp_path):
    """渲染出的合法载体经 --validate 必须 PASS（可含字数>目标的 WARN，但不得 BLOCK）。"""
    state_path = tmp_path / "state.json"
    shutil.copy(str(TEMPLATES / "task-state.example.json"), str(state_path))
    out = tmp_path / "handoff.md"
    run_gate(_COMPRESS, "--render", "--state", state_path, "--out", out)
    code, o = run_gate(_COMPRESS, "--validate", "--handoff", out, "--state", state_path, "--tier", "T2")
    assert code == PASS, o


def test_compress_handoff_validate_bloated_blocks(tmp_path):
    """载体正文突破硬上限 → BLOCK：总结膨胀成第二份历史，回灌禁令失效。"""
    big = tmp_path / "big.md"
    body = ("## 0 当前目标\n- " + "目标锚点内容" * 3 + "\n"
            + "## 4 下一步入口\n- " + "填充以突破硬上限字数天花板内容" * 120)
    big.write_text(body, encoding="utf-8")
    code, o = run_gate(_COMPRESS, "--validate", "--handoff", big)
    assert code == BLOCK, o
    assert "硬上限" in o, o


def test_compress_handoff_validate_missing_file_usage_error(tmp_path):
    code, o = run_gate(_COMPRESS, "--validate", "--handoff", tmp_path / "nope.md")
    assert code == USAGE, o


@pytest.mark.parametrize("args", [
    ("--render", "--validate"),   # 两模式同给
    (),                            # 一个模式都不给
])
def test_compress_handoff_mode_exclusivity(tmp_path, args):
    """--render / --validate 互斥且必居其一，否则 USAGE_ERROR。"""
    state_path = tmp_path / "state.json"
    shutil.copy(str(TEMPLATES / "task-state.example.json"), str(state_path))
    extra = ["--state", str(state_path), "--handoff", str(state_path)]
    code, o = run_gate(_COMPRESS, *(list(args) + extra))
    assert code == USAGE, o


def test_compress_handoff_registered_in_manifest_and_portable_core():
    """渲染器必须进清单与便携核心 tools——不进则安装不拷，跨终端「每步压缩」能力落空。"""
    data = json.loads(io.open(str(SUITE / "manifest.json"), "r", encoding="utf-8").read())
    assert "scripts/%s" % _COMPRESS in data["files"], "压缩渲染器未进 manifest.files"
    assert "scripts/%s" % _COMPRESS in _adapters()["portable_core"]["tools"], "压缩渲染器未进便携核心 tools"


# ------------------------------------------------- v2.1：每步双AI自审 T3 轮次隔离验证


def test_step_self_review_t3_rounds_isolated(tmp_path):
    """T3 每步自审须 ≥t3_min_rounds 轮：从 T3 合法态出发，单独把一步轮次降到阈值下必 BLOCK，
    且 Python 与 Node 双实现一致——隔离验证，避免被 example 的其它 T3 缺项污染。"""
    min_rounds = _spec()["per_step_review"]["t3_min_rounds"]
    base = copy.deepcopy(load("task-state.example.json"))
    base["tier"] = "T3"
    for step in base["steps"]:
        if isinstance(step.get("review"), dict):
            step["review"]["rounds"] = min_rounds
    # 先确认基线 T3 合法
    ok_path = dump(tmp_path, copy.deepcopy(base), name="ok.json")
    code, out = run_gate("gate_checklist.py", "--state", ok_path, "--tier", "T3")
    assert code == PASS, "T3 基线应 PASS：%s" % out
    # 单独降一步轮次
    bad = copy.deepcopy(base)
    bad["steps"][0]["review"]["rounds"] = min_rounds - 1
    bad_path = dump(tmp_path, bad, name="bad.json")
    py_code, py_out = run_gate("gate_checklist.py", "--state", bad_path, "--tier", "T3")
    nd_code, nd_out = run_node_gate("checklist", "--state", str(bad_path), "--tier", "T3")
    assert py_code == BLOCK == nd_code, "py=%s node=%s\n%s\n%s" % (py_code, nd_code, py_out, nd_out)
    assert "每步自对抗审核须" in py_out, py_out


# ------------------------------------------------- v2.1 方向2：技能清单（打包范围的诚实边界）
#
# 回答「把三终端全部技能打包进来」：便携包只 verbatim 打包真·可迁移纯文档技能；env-bound /
# 阿里内部专有技能做清单化分类 + 安装/降级指引，绝不塞二进制或运行时。这组测试把该边界机检化。


def _inventory():
    return json.loads(io.open(str(SUITE / "spec" / "skill-inventory.json"), "r", encoding="utf-8").read())


def test_skill_inventory_wellformed_and_self_consistent():
    """清单摘要必须与逐条分类真实一致，且版本对齐契约版本——摘要与明细各说各话等于没有清单。"""
    inv = _inventory()
    skills = inv["skills"]
    summ = inv["summary"]
    assert inv["spec_version"] == _import_engine().CONTRACT_VERSION, inv["spec_version"]
    assert summ["total_skills"] == len(skills), (summ["total_skills"], len(skills))
    tally = {}
    for s in skills:
        tally[s["classification"]] = tally.get(s["classification"], 0) + 1
    for cls in ("portable_doc", "already_in_acs", "env_bound", "internal_proprietary", "junk"):
        assert summ[cls] == tally.get(cls, 0), "%s 摘要=%s 实际=%s" % (cls, summ[cls], tally.get(cls, 0))
    assert inv["scanned_dirs"], "必须记录扫过哪些终端技能目录（可复现性）"
    assert (SUITE / inv["generated_by"]).exists(), "生成器脚本缺失：%s" % inv["generated_by"]


def test_skill_inventory_portable_doc_bundles_physically_exist():
    """标为 portable_doc 的技能必须真的 verbatim 落进 bundled-skills/ 且在清单里——否则「已打包」是空话。"""
    inv = _inventory()
    data = json.loads(io.open(str(SUITE / "manifest.json"), "r", encoding="utf-8").read())
    portable = [s for s in inv["skills"] if s["classification"] == "portable_doc"]
    assert portable, "至少应打出一个可迁移纯文档技能"
    bundled_count = 0
    for s in portable:
        bp = s["bundle_path"]
        assert bp and (SUITE / bp).is_dir(), "%s 的 bundle_path 不存在：%s" % (s["name"], bp)
        for md in s["md_files"]:
            rel = "%s/%s" % (bp, md)
            assert (SUITE / rel).is_file(), "打包文件缺失：%s" % rel
            assert rel in data["files"], "打包文件未登记进 manifest.files：%s" % rel
            bundled_count += 1
    assert bundled_count == inv["summary"]["bundled_files"], (bundled_count, inv["summary"]["bundled_files"])


def test_skill_inventory_env_bound_and_proprietary_pack_no_blobs():
    """env-bound / 内部专有技能只做清单化：必须给 fallback 指引，且 bundle_path 为空（不塞二进制/运行时）。"""
    inv = _inventory()
    for s in inv["skills"]:
        if s["classification"] in ("env_bound", "internal_proprietary"):
            assert s["bundle_path"] is None, \
                "%s(%s) 不该 verbatim 打包，却给了 bundle_path=%s" % (
                    s["name"], s["classification"], s["bundle_path"])
            fb = s.get("fallback")
            assert isinstance(fb, str) and fb.strip(), \
                "%s 缺 fallback 指引：清单化却不给降级路径=该终端能力落空" % s["name"]


def test_skill_inventory_registered_in_manifest_and_portable_core():
    data = json.loads(io.open(str(SUITE / "manifest.json"), "r", encoding="utf-8").read())
    assert "spec/skill-inventory.json" in data["files"], "技能清单未进 manifest.files"
    assert "spec/skill-inventory.json" in _adapters()["portable_core"]["specs"], "技能清单未进便携核心 specs"
    assert "scripts/acs_build_skill_inventory.py" in _adapters()["portable_core"]["tools"], \
        "清单生成器未进便携核心 tools"


# ------------------------------------------------- v2.2 能力治理门（与每步双动作共存）

def _cap_registry_v22():
    return json.loads(io.open(str(SUITE / "spec" / "capability-registry.json"),
                              "r", encoding="utf-8").read())


def _mutated_registry_v22(tmp_path, mutate):
    root = _suite_copy(tmp_path, "capreg22")
    reg = json.loads(io.open(str(root / "spec" / "capability-registry.json"),
                             "r", encoding="utf-8").read())
    mutate(reg)
    io.open(str(root / "spec" / "capability-registry.json"), "w",
            encoding="utf-8").write(json.dumps(reg, ensure_ascii=False, indent=2))
    return root


def test_capability_registry_positive_v22():
    code, out = run_gate("gate_capability_registry.py", "--root", SUITE)
    assert code == PASS, out


def test_capability_registry_registered_in_manifest_v22():
    data = json.loads(io.open(str(SUITE / "manifest.json"), "r", encoding="utf-8").read())
    assert "spec/capability-registry.json" in data["files"]
    assert "scripts/gate_capability_registry.py" in data["files"]
    assert any(g.get("id") == "capability" for g in data["gates"]), [g.get("id") for g in data["gates"]]


def test_capability_registry_spec_missing_usage_error_v22(tmp_path):
    root = _suite_copy(tmp_path, "capreg22-missing")
    (root / "spec" / "capability-registry.json").unlink()
    code, out = run_gate("gate_capability_registry.py", "--root", root)
    assert code == USAGE, out


def test_capability_registry_runtime_false_bundled_blocked_v22(tmp_path):
    """runtime 域谎称已打包（in_pack=true）必须 BLOCK——核心诚实边界。"""
    def mutate(reg):
        for item in reg["registry"]:
            if item["domain"] == "mcp":
                item["in_pack"] = True
    root = _mutated_registry_v22(tmp_path, mutate)
    code, out = run_gate("gate_capability_registry.py", "--root", root)
    assert code == BLOCK, out
    assert "禁止谎称" in out, out


def test_capability_registry_runtime_missing_fallback_blocked_v22(tmp_path):
    def mutate(reg):
        for item in reg["registry"]:
            if item["domain"] == "toolcalling":
                item["fallback"] = ""
    root = _mutated_registry_v22(tmp_path, mutate)
    code, out = run_gate("gate_capability_registry.py", "--root", root)
    assert code == BLOCK, out
    assert "fallback" in out, out


def test_capability_registry_portable_not_in_pack_blocked_v22(tmp_path):
    def mutate(reg):
        for item in reg["registry"]:
            if item["domain"] == "skill":
                item["in_pack"] = False
    root = _mutated_registry_v22(tmp_path, mutate)
    code, out = run_gate("gate_capability_registry.py", "--root", root)
    assert code == BLOCK, out
    assert "未登记入包" in out, out


def test_capability_registry_governance_blind_spot_blocked_v22(tmp_path):
    def mutate(reg):
        reg["registry"][0]["governed_by"] = []
    root = _mutated_registry_v22(tmp_path, mutate)
    code, out = run_gate("gate_capability_registry.py", "--root", root)
    assert code == BLOCK, out
    assert "治理盲区" in out, out


def test_capability_registry_coverage_map_broken_blocked_v22(tmp_path):
    def mutate(reg):
        reg["coverage_domain_map"]["mcp"] = "不存在的域"
    root = _mutated_registry_v22(tmp_path, mutate)
    code, out = run_gate("gate_capability_registry.py", "--root", root)
    assert code == BLOCK, out
    assert "coverage" in out.lower() or "映射" in out, out


def test_capability_registry_portable_carrier_must_exist_v22(tmp_path):
    def mutate(reg):
        for item in reg["registry"]:
            if item["domain"] == "harness":
                item["carrier"] = "scripts/不存在的脚本.py"
    root = _mutated_registry_v22(tmp_path, mutate)
    code, out = run_gate("gate_capability_registry.py", "--root", root)
    assert code == BLOCK, out
    assert "carrier" in out, out


def test_run_gates_includes_capability_gate_v22(tmp_path, state):
    """v2.2 merge：编排器必须真的跑到 capability 门（与每步双动作共存）。"""
    code, out = run_gate("run_gates.py", "--state", dump(tmp_path, state),
                         "--root", SUITE, "--tier", "T2")
    assert code == PASS, out
    assert "capability" in out, out


def test_capability_and_step_dual_action_coexist_v22(tmp_path, state):
    """v2.2 核心：能力治理门（静态能力面）与每步双动作（动态执行）共存，run_gates 同时跑两者且都 PASS。"""
    code, out = run_gate("run_gates.py", "--state", dump(tmp_path, state),
                         "--root", SUITE, "--tier", "T2")
    assert code == PASS, out
    # 能力治理门（静态能力面诚实性）
    assert "capability" in out, "能力治理门未跑"
    # 每步双动作（gate_checklist 的 STEP_handoff/STEP_self_review）
    assert "checklist" in out, "每步双动作门未跑"
