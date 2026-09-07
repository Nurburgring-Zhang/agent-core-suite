"""ACS v2.0.0 新增双向验证（DESIGN.md 5.4 验收标准 A19-A23）。

正样本 PASS / 负样本 BLOCK 双向覆盖：
- A19 qoder profile 能力映射（非空 + 与实证工具名一致）
- A20 creed_evidence 门禁（存在/非空/指针可达/分级豁免）
- A21 新技能 qoder-native-integration 过 gate_skill_spec
- A22 capability-coverage 新增域与 summary 计数一致
- A23 契约向后兼容（schema 可选字段 + run_gates 转发 --root）
"""

import importlib.util
import io
import contextlib
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


def _load_checklist():
    path = os.path.join(ROOT, "scripts", "gate_checklist.py")
    spec = importlib.util.spec_from_file_location("gate_checklist_v2", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _creed_exit(mod, entries, tier):
    state = {} if entries is None else {"creed_evidence": entries}
    report = mod.Report("v2-smoke")
    mod.check_creed_evidence(state, tier, ROOT, report)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return report.finish()


def _entry(article, ref, note="证明对应守则已执行并留痕"):
    return {"article": article, "evidence_ref": ref, "note": note}


GOOD10 = [
    _entry(1, "DESIGN.md"), _entry(2, "AGENTS.md"), _entry(3, "README.md"),
    _entry(4, "VERSION"), _entry(5, "manifest.json"), _entry(6, "pack.py"),
    _entry(7, "LICENSE"), _entry(8, "install.sh"),
    _entry(9, "https://example.com/plan-review"),
    _entry(10, "cmd:python -X utf8 scripts/run_gates.py --state s --tier T3"),
]

# ---------------------------------------------------------------- A19 qoder profile

REQUIRED_CAPS = ["clarify", "progress", "parallel", "skill_load",
                 "memory", "file_delivery", "shell"]


def test_a19_qoder_profile_exists_and_required_caps_nonempty():
    adapters = _read_json("spec/adapters.json")
    profiles = adapters["capability_adapters"]["profiles"]
    assert "qoder" in profiles, "adapters profiles 独缺 qoder 是 v1.2 最大缺口，v2.0 必须补上"
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
    mcp = q.get("mcp", "")
    assert "mcp_list" in mcp and "mcp_get" in mcp and "mcp_call" in mcp, \
        "MCP 惰性加载三件套顺序是 Qoder 实证机制（E32），缺一不可"


# ---------------------------------------------------------------- A20 creed_evidence


@pytest.fixture(scope="module")
def checklist_mod():
    return _load_checklist()


def test_a20_t3_positive_ten_reachable_entries(checklist_mod):
    assert _creed_exit(checklist_mod, GOOD10, "T3") == 0


def test_a20_t3_missing_field_blocks(checklist_mod):
    assert _creed_exit(checklist_mod, None, "T3") == 1


def test_a20_t3_insufficient_count_blocks(checklist_mod):
    assert _creed_exit(checklist_mod, GOOD10[:9], "T3") == 1


def test_a20_t3_unreachable_pointer_blocks(checklist_mod):
    assert _creed_exit(checklist_mod, [_entry(1, "no/such/file.md")] + GOOD10[1:], "T3") == 1


def test_a20_t3_duplicate_article_blocks(checklist_mod):
    assert _creed_exit(checklist_mod, [_entry(1, "DESIGN.md"), _entry(1, "AGENTS.md")] + GOOD10[2:], "T3") == 1


def test_a20_t3_article_out_of_range_blocks(checklist_mod):
    assert _creed_exit(checklist_mod, [_entry(19, "DESIGN.md")] + GOOD10[1:], "T3") == 1


def test_a20_t3_empty_note_blocks(checklist_mod):
    bad = {"article": 1, "evidence_ref": "DESIGN.md", "note": "   "}
    assert _creed_exit(checklist_mod, [bad] + GOOD10[1:], "T3") == 1


def test_a20_t3_bad_url_scheme_blocks(checklist_mod):
    assert _creed_exit(checklist_mod, [_entry(1, "ftp://x/y")] + GOOD10[1:], "T3") == 1


def test_a20_t2_missing_field_blocks(checklist_mod):
    assert _creed_exit(checklist_mod, None, "T2") == 1, "T2 不豁免（阈值 T2≥6），豁免仅限 T0/T1"


def test_a20_t2_positive_six_entries(checklist_mod):
    assert _creed_exit(checklist_mod, GOOD10[:6], "T2") == 0


def test_a20_t1_t0_exempt_when_field_absent(checklist_mod):
    assert _creed_exit(checklist_mod, None, "T1") == 0
    assert _creed_exit(checklist_mod, None, "T0") == 0


# ---------------------------------------------------------------- A21 新技能


def test_a21_qoder_native_skill_present_and_passes_skill_spec():
    skill = os.path.join(ROOT, "skills", "qoder-native-integration", "SKILL.md")
    assert os.path.isfile(skill), "v2.0 必须新增 qoder-native-integration 技能"
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


# ---------------------------------------------------------------- A23 向后兼容


def test_a23_schema_creed_evidence_is_optional():
    schema = _read_json("templates/task-state.schema.json")
    assert "creed_evidence" in schema["properties"], "v2.0 必须在 schema 登记 creed_evidence"
    assert "creed_evidence" not in schema.get("required", []), \
        "creed_evidence 必须是可选字段（旧 state 不填不报错，向后兼容）"


def test_a23_thresholds_creed_evidence_section():
    th = _read_json("spec/thresholds.json")
    ce = th["creed_evidence"]
    assert ce["min_articles"] == {"T2": 6, "T3": 10}
    assert ce["article_range"] == [1, 18]


def test_a23_run_gates_forwards_root_to_checklist():
    with open(os.path.join(ROOT, "scripts", "run_gates.py"), encoding="utf-8") as f:
        src = f.read()
    assert '"--root", root' in src and "gate_checklist.py" in src, \
        "run_gates 必须把 --root 转发给 gate_checklist（creed_evidence 指针可达性依赖它）"


def test_a23_version_bumped_to_2_0_0():
    with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
        assert f.read().strip() == "2.0.0"
    manifest = _read_json("manifest.json")
    assert manifest["version"] == "2.0.0"
