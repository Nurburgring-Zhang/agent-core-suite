"""Agent Core Suite 门禁自测：每道门在正样本 PASS、负样本 BLOCK 双向验证。

运行：
    python -X utf8 -m pytest c:/qoder/agent-core-suite/tests/test_gates.py -q
"""

import copy
import io
import json
import os
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
    """v1.1 的 T3-only 检查必须有全绿正样本，且双实现一致放行（否则 T3 就是只能拦不能过）。"""
    state["tier"] = "T3"
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
    """example 必须持续充当 v1.1 正样本载体：这些示范被删，T3-only 检查就测不到。"""
    s = load("task-state.example.json")
    assert s["contract_version"] == "1.1.0", s.get("contract_version")
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
    "copy", "copy2", "copytree", "move", "chmod", "kill", "killpg", "system", "putenv",
)

# 只盯模块限定调用（os.remove / shutil.rmtree ……）；字符串的 .replace() 与列表的 .copy() 不是副作用，不得误报
_WRITE_MODULES = ("os", "shutil", "path", "subprocess", "Path", "pathlib")


def _gate_sources():
    names = [p for p in sorted(os.listdir(str(SCRIPTS))) if p.endswith(".py")]
    assert names, "scripts/ 下没有 .py，测试前提不成立"
    return names


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
    """
    src = io.open(str(SCRIPTS / name), "r", encoding="utf-8").read()
    bad = _scan_side_effects(src, name)
    assert not bad, "\n".join(bad)


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
    assert runtimes.get("node", {}).get("not_covered") == ["reality_scan"], runtimes


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
                   "acs_gates.mjs", "install.sh", "--with-hook", "--no-verify"):
        assert needle in src, "CI 里缺了验收命令/证据：%s" % needle
    for needle in ("ubuntu-latest", "macos-latest", "setup-node", "setup-python"):
        assert needle in src, "CI 里缺了环境覆盖：%s" % needle


def test_ci_workflow_parses_as_yaml():
    """写错缩进的 workflow 不会报错，只会不跑 —— 那就是一个看上去很美的假强制点。"""
    yaml = pytest.importorskip("yaml", reason="本机无 pyyaml，CI 上由 GitHub 自行解析")
    with io.open(str(CI_WORKFLOW), "r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    jobs = doc["jobs"]
    assert set(["gates", "node-fallback", "installer", "pre-commit-hook"]).issubset(jobs), sorted(jobs)
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

