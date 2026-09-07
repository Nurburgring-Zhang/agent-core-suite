"""ACS v2.2.0 新增双向验证（merge 版：能力治理门 + 每步双动作 + qoder profile）。

正样本 PASS / 负样本 BLOCK 双向覆盖：
- A19 qoder profile 能力映射（非空 + 与实证工具名一致）
- A21 新技能 qoder-native-integration 过 gate_skill_spec
- A22 capability-coverage 新增域与 summary 计数一致
- A23 契约向后兼容（run_gates 转发 --root）+ 版本 2.2.0
- A24 能力治理门（v2.2 merge 核心，与每步双动作共存）

注：原 v2.0.0 的 creed_evidence 逐条守则留痕门已被 v2.1 的 STEP_handoff/STEP_self_review
（每步压缩交接 + 双 AI 自对抗审核，gate_checklist 机检）取代，故移除 creed_evidence 陈旧断言；
每步双动作的正/负样本验证在 test_gates.py（handoff 系列）。
"""

import io
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def _read_json(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return json.load(f)


def _run_gate(script, args):
    cmd = [PY, "-X", "utf8", os.path.join(ROOT, "scripts", script)] + args
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    return p.returncode, p.stdout + p.stderr


# ---------------------------------------------------------------- A19 qoder profile

REQUIRED_CAPS = ["clarify", "progress", "parallel", "skill_load",
                 "memory", "file_delivery", "shell"]


def test_a19_qoder_profile_exists_and_required_caps_nonempty():
    adapters = _read_json("spec/adapters.json")
    profiles = adapters["capability_adapters"]["profiles"]
    assert "qoder" in profiles, "adapters profiles 独缺 qoder 是缺口，v2.2 必须补上"
    q = profiles["qoder"]
    for cap in REQUIRED_CAPS:
        v = q.get(cap)
        assert isinstance(v, str) and v.strip(), "qoder.%s 能力映射不得为空" % cap


def test_a19_qoder_profile_mappings_match_field_proven_tools():
    q = _read_json("spec/adapters.json")["capability_adapters"]["profiles"]["qoder"]
    assert "AskUserQuestion" in q["clarify"]
    assert "TaskCreate" in q["progress"]
    assert "Agent" in q["parallel"]
    assert "Skill" in q["skill_load"]
    assert "Bash" in q["shell"]
    mcp = q.get("mcp_runtime", "")
    assert "mcp_list" in mcp and "mcp_get" in mcp and "mcp_call" in mcp, \
        "MCP 惰性加载三件套顺序是 Qoder 实证机制，缺一不可"


# ---------------------------------------------------------------- A21 新技能


def test_a21_qoder_native_skill_present_and_passes_skill_spec():
    skill = os.path.join(ROOT, "skills", "qoder-native-integration", "SKILL.md")
    assert os.path.isfile(skill), "必须存在 qoder-native-integration 技能"
    rc, out = _run_gate("gate_skill_spec.py", ["--root", ROOT])
    assert rc == 0, "gate_skill_spec 必须全绿：\n%s" % out


# ---------------------------------------------------------------- A22 coverage


def test_a22_capability_coverage_includes_qoder_domain():
    cov = _read_json("spec/capability-coverage.json")
    domains = [c["domain"] for c in cov["capabilities"]]
    assert any("qoder" in d for d in domains), "capability-coverage 必须登记 qoder 原生融合域"
    assert cov["summary"]["total_domains"] == len(cov["capabilities"]), \
        "summary.total_domains 必须与 capabilities 实际条数一致"
    in_pack = sum(1 for c in cov["capabilities"] if c.get("in_pack"))
    assert cov["summary"]["in_pack_true"] == in_pack, "summary.in_pack_true 计数漂移"


# ---------------------------------------------------------------- A23 向后兼容 + 版本


def test_a23_run_gates_forwards_root_to_checklist():
    with open(os.path.join(ROOT, "scripts", "run_gates.py"), encoding="utf-8") as f:
        src = f.read()
    assert '"--root", root' in src and "gate_checklist.py" in src, \
        "run_gates 必须把 --root 转发给 gate_checklist"


def test_a23_version_bumped_to_2_2_0():
    with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
        assert f.read().strip() == "2.2.0"
    manifest = _read_json("manifest.json")
    assert manifest["version"] == "2.2.0"


# ---------------------------------------------------------------- A24 能力治理门（v2.2 merge 核心）


def test_a24_capability_gate_wired_and_passes():
    """v2.2 merge：能力治理门接入 run_gates 且正样本 PASS。"""
    rc, out = _run_gate("gate_capability_registry.py", ["--root", ROOT])
    assert rc == 0, "能力治理门正样本必须 PASS：\n%s" % out
    with open(os.path.join(ROOT, "scripts", "run_gates.py"), encoding="utf-8") as f:
        src = f.read()
    assert "gate_capability_registry.py" in src, "能力治理门必须接入 run_gates"


def test_a24_capability_gate_coexists_with_step_dual_action():
    """v2.2 核心：能力治理门（静态能力面）与每步双动作（动态执行）共存，run_gates 同跑两者。"""
    rc, out = _run_gate("run_gates.py", ["--state", "templates/task-state.example.json",
                                         "--root", ".", "--tier", "T2"])
    assert rc == 0, "run_gates 全链路必须 PASS：\n%s" % out
    assert "capability" in out, "能力治理门未跑"
    assert "checklist" in out, "每步双动作门未跑"


def test_a24_capability_registry_runtime_not_bundled():
    """能力治理清单：runtime 域必须 in_pack=false（禁止谎称打包运行时本体）。"""
    reg = _read_json("spec/capability-registry.json")
    for item in reg["registry"]:
        if item["layer"] == "runtime":
            assert item["in_pack"] is False, "runtime 域 %s 谎称打包" % item["domain"]
            assert item["carrier"] is None, "runtime 域 %s carrier 必须为 null" % item["domain"]
            assert isinstance(item.get("fallback"), str) and item["fallback"].strip(), \
                "runtime 域 %s 缺 fallback" % item["domain"]
