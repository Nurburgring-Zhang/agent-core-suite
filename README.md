# Agent Core Suite

**Five skills + six machine-checkable gates + zero dependencies — the engineering discipline agent terminals are missing.**

**五个技能 + 六道可机检硬门禁 + 零依赖 —— 给 Agent 终端补上缺失的工程纪律。**

[![CI](https://img.shields.io/github/actions/workflow/status/Nurburgring-Zhang/agent-core-suite/acs-gates.yml?label=CI&logo=githubactions)](https://github.com/Nurburgring-Zhang/agent-core-suite/actions)
![Python ≥ 3.8](https://img.shields.io/badge/Python-%E2%89%A53.8-blue?logo=python)
![Dependencies: none](https://img.shields.io/badge/dependencies-none-success)
![Tests](https://img.shields.io/badge/tests-377%20passed-brightgreen)
![License: MIT](https://img.shields.io/badge/license-MIT-yellow)

> **Turn "I think it's done" into exit code 0.**
>
> **把「我觉得做完了」，变成退出码 0。**

Agent Core Suite is a universal augmentation pack for agent terminals. Any agent — on any terminal — tends to drift into the same failure mode: over-heavy steps, vague completion criteria, context re-fed round after round, and a confident *"done!"* with no proof. This suite injects the missing constraints: a tiered execution SOP, hard gates with real exit codes, and quantified cost ceilings. Skill (can do) + harness (can prove it did) + loop engineering (knows when to stop) + graph engineering (knows how to split).

[English](#english) | [简体中文](#简体中文)

---

# English

## Positioning: an augmentation layer, not a replacement

The suite **stacks on top of your terminal's native abilities and only adds**:

| | What it means |
| --- | --- |
| **What it never does** | Never disable, hijack, or rewrite the terminal's native tools, search, task lists, memory, permission or approval mechanisms |
| **Native first** | If the terminal already has an equivalent (native todo/planning, native search subagents, native memory), use it; `templates/` is a fallback only when the native side is absent. No parallel systems |
| **Adds only three things** | ① a tiered execution SOP (when to go heavy, when to go light) ② machine-checkable exit codes (turning "I think it's done" into 0/1) ③ quantified cost-gate thresholds |
| **Conflict precedence** | Owner's explicit instruction > terminal-native safety/permission/approval constraints > this suite's hard gates > this suite's soft rules. **Never overrides the first two** |
| **What is enforced** | Standards and evidence — not specific tool paths. Any equivalent means is fine, as long as the evidence is machine-checkable |

- **Soft layer**: `skills/`, `rules/`, `AGENTS.md` — injected into context so the agent remembers how to behave.
- **Hard layer**: six gate scripts in `scripts/` — actually executed, with exit codes; the **only provable** basis for passing.
- **External enforcement (opt-in)**: `hooks/pre-commit` and `.github/workflows/acs-gates.yml` — triggered by git/CI, **independent of the model's cooperation**.
- The three are not interchangeable. **When the scripts can run, "I already checked" is not evidence.**

## Why it exists — a real bill

A certain 27B open-source model wired into an Agent Harness completed 9 tasks:

| Metric | That model | Control model, same harness | Ratio |
| --- | --- | --- | --- |
| Total tokens | 13,995,350 | 956,630 | 14.6× |
| Input tokens | 13,718,510 (98%) | 927,401 | 14.8× |
| Wall time | 22,564s (6h17m) | 1,343.75s (22min) | 16.8× |
| Requests | 197 | 87 | 2.3× |
| Worst task (3D website) | 11,533,959 tokens / 124 reqs / 18,639s | 161,019 / 15 / 541s | tokens 71.6× |
| Split of that task | 75 steps / 3 turns | 8 steps | — |
| Thinking-time share | 55.6% (60.7% of tokens) | 29.6% (36.6%) | — |
| "Other runtime" on that task | 12,632s — 59% of its wall time | — | — |

It still scored 18/18 on quality; the control scored 14/18. **Capability was never the bottleneck — engineering constraints were.** Four root causes: splits that trend needlessly complex, **over-heavy single steps**, **unclear completion criteria**, and **context re-fed again and again** — producing massive spinning. Every hard threshold in this suite targets one of these four. Full evidence trail (E1–E27) in [DESIGN.md](DESIGN.md).

## 30-second setup

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Target C:\path\to\workspace
```

```bash
# Linux / macOS
chmod +x install.sh && ./install.sh --target /path/to/workspace
```

Both installers default to `--mode auto`: they ask the `acs_doctor` probe what this machine has and which terminal this workspace belongs to, then decide install targets. Currently recognized: qoder / qoderwork / claude / cursor / windsurf / codex; if none matches, it lands explicitly on generic (it never pretends to have recognized something). To see the health check first:

```bash
python -X utf8 scripts/acs_doctor.py --target .        # no Python? node scripts/node/acs_gates.mjs doctor --target .
```

It tells you the enforcement level directly: **full** (Python present, 5/5 gates) / **partial** (Node only, 4/5) / **soft_only** (neither — enforcement is zero, exit=1). The probe itself is dual-implemented: its job is to answer "is Python available?", so if it only ran under Python, the one scenario that needs it most would be exactly the one it can't serve.

Run `-DryRun` / `--dry-run` first to preview the plan, then install for real. After copying, the installer **actually runs the gates once**: only a positive sample judged PASS (exit=0) counts as installed — no "copied, therefore success".

**Upgrades require `-Force` / `--force`**: identical files report `same` (nothing to do); **a differing file without `-Force` names the stale file and exits 1** — it never skips silently. Otherwise a stale version would pass "file exists" style post-install verification and produce a fake success (a trap we hit in real testing and then fixed).

Before starting work, create the state file:

```bash
mkdir .acs
cp .acs/templates/task-state.example.json .acs/task-state.json   # then rewrite it for the real task
```

Run the gates:

```bash
python -X utf8 .acs/scripts/run_gates.py --state .acs/task-state.json --root . --tier T2
```

`0=PASS` ｜ `1=BLOCK` ｜ `2=USAGE_ERROR`. **The latter two both mean "unverified = not done".**

## Layout

```
AGENTS.md                cross-terminal entry point (auto-read by mainstream agent terminals)
DESIGN.md                design baseline + evidence table E1-E27 + acceptance criteria A1-A18 + the three enforcement layers
hooks/pre-commit         external enforcement (opt-in, not installed by default; dual runtime, exit codes passed through)
.github/workflows/       CI, four jobs: gates / node-fallback / installer / pre-commit-hook
manifest.json / VERSION  inventory & version (install_check.py verifies completeness against it)
install.ps1 / install.sh installers (pre-check → copy → real post-install verification)

skills/
  universal-task-code/   main entry (lean, 76 lines) + reference-gates.md + reference-creed.md
  loop-engineering/      stop protocol, four cost gates, safety valves, loop-until-dry
  graph-engineering/     node contracts, the four-way split, parallel structures, subagent context isolation
  self-verify-scaling/   1-20 rubric, three-dimension tandem scaling, pivot ranking, drift detection
  token-thrift/          input-token thrift, stable-prefix discipline, read budgets, compressed summaries

scripts/                 six gates + orchestrator + install check, pure stdlib, zero deps (Node mirror covers the same)
templates/               two schemas + two positive samples + handoff-summary template
tests/test_gates.py      377 two-way tests (positive sample PASS / negative sample BLOCK, including "gates must be read-only" position checks; v1.1 adds: T3-only negative samples against tier bypass, dual-impl T3 positives, suite hygiene checks — tombstone files / dead local links / spec sections without consumers / duplicate files)
```

## The five skills (progressive disclosure — never load all at once)

| Situation | Load |
| --- | --- |
| Any non-chitchat task, at kickoff | `universal-task-code` (the resident main entry) |
| Defining stop conditions / convergence / retries / spin detection | `loop-engineering` |
| Splitting parallel subtasks / node contracts / dispatching subagents | `graph-engineering` |
| T3 candidate competition / scoring & ranking / direction calibration | `self-verify-scaling` |
| Context pressure / compressed summaries / clean-context continuation | `token-thrift` |

## The six hard gates

| Script | What it blocks |
| --- | --- |
| `gate_state_validate.py` | Malformed state contracts, cyclic/orphan task graphs, nodes without completion criteria, `owner==verifier`, deterministic nodes calling models, inputs written as "the context above"; **v1.1**: edge gates with `kind=cmd` missing `expect_exit`, T3 graphs with zero edge gates, `barriers` with `policy=min_success` missing/out-of-range `min_count`, T3 artifact registry missing / `produced_by` pointing nowhere / `supersedes` lineage broken or cyclic, illegal `contract_version`, T3 model-calling nodes missing `model_tier`, acceptance commands not bound to declared artifacts (the blank-paper blind spot) |
| `gate_loop_guard.py` | Missing metering fields (deleting fields to bypass gates), missing safety-valve ceilings, multiple outputs per step, budget > 30min, summary > 400 chars, context > 20,000 bytes, files-read > 5, two consecutive steps with no new evidence, thinking ratio > 0.40, rounds/duration beyond the safety valve; **v1.1**: bounded retry (`retries` over ceiling, missing `retry_reason`/`delta_from_last` — retrying the same way is spinning) |
| `gate_verify_rank.py` | Fewer than 5 criteria, weights not summing to 1, wrong repeat counts, spread > 5, fabricated medians, hand-edited weighted scores, ranking contradicting scores, unresolved ties, missing threshold line, progress score stuck flat; **T3 self-consistency**: top-two gap < 1.5 without resampling, resampling registered without actually adding samples, hand-edited gap |
| `cost_metering` inside `gate_loop_guard.py` | T2+ without a declared metering source, auditable claims with no numbers/evidence, evidence made of empty phrases, `tokens_actual` present while the source is not auditable, line items not reconciling with totals, real bills over tier ceilings |
| `gate_reality_scan.py` | Leftover todo markers, degradation phrasing, stand-in implementations, empty-shell functions (Python AST), unparseable syntax |
| `gate_checklist.py` | Tier-required gates not filled, empty-phrase evidence, fewer than 2 adversarial review rounds, fewer than 2 release-test rounds, skipped gates without an approver; **seven formerly-soft items hardened**: steelman counts & basis both ways, six cognition fields, four planning fields, review closure (found→fixed→reverified, no self-sign-off), measured `actual` values for acceptance, four-element delivery report, T3 RSI loop — misfiled fields are blocked too; **v1.1**: T3 double-blind review (≥2 independent reviewers, builder/duplicates banned, verdict conflicts must be arbitrated on record) |
| `run_gates.py` | Orchestrates the five gates above and aggregates (USAGE > BLOCK > PASS precedence) |

Tier thresholds (`T0/T1/T2/T3`): candidates `0/1/1/3~5`, repeat evaluations `0/1/2/3`, score floors `—/14/16/17`, safety valves `1 round·5min / 3·15 / 6·45 / 10·120`. T3 additionally has hard floors: `correctness≥17` and `risk≥16`.

## The three enforcement layers (L3 = external)

Without layers, "written into the docs" gets mistaken for "enforced":

| Layer | Where | Binding force | Bypass |
| --- | --- | --- | --- |
| L1 soft | `skills/` `rules/` `AGENTS.md` | Context injection, no exit codes | Dies with model drift, and nobody reports an error |
| L2 hard gates | `scripts/run_gates.py`, `scripts/node/acs_gates.mjs` | Provable exit codes | Effectively nonexistent if the model never calls it |
| L3 external | `hooks/pre-commit`, `.github/workflows/acs-gates.yml` | Triggered by git / CI — **does not depend on model cooperation** | The hook can be bypassed with `--no-verify` (kept on purpose); the CI layer cannot |

**The hook is opt-in and not installed by default** — dropping a hook into someone's repo without consent is a takeover, contrary to the augmentation-layer position:

```bash
./install.sh --target /path/to/repo --with-hook       # Linux / macOS (chmod +x, re-verified with -x)
```

```powershell
powershell -File .\install.ps1 -Target C:\path\to\repo -WithHook
```

If the target is not a git repo it **fails explicitly** (exit=1) instead of skipping silently — "installed but inactive" is more dangerous than not installed. The hook itself is pure read-only and **never touches the staging area**; Python first (5/5 gates), Node fallback (4/5), and if neither exists it blocks rather than passing silently. When a commit truly has nothing to do with the task (fixing a README typo), use git's native `git commit --no-verify` — **the escape hatch must be pulled by the owner explicitly, never loosened by the script itself**.

CI (`.github/workflows/acs-gates.yml`) runs four jobs: `gates` (ubuntu 3.8 / ubuntu 3.12 / macos 3.12, full gates + pytest), `node-fallback` (node 16 / 20, asserting reality must exit=2), `installer` (**install.sh really executed on ubuntu + macos**, including `--force` semantics), `pre-commit-hook` (a real git-init'd repo: missing state must block, `--no-verify` must pass, positive sample must go through).

## Prove it yourself

```bash
python -X utf8 scripts/install_check.py --root .                      # inventory completeness
python -X utf8 -m pytest tests/test_gates.py -q                       # 377 passed
python -X utf8 scripts/gate_reality_scan.py --root .                  # the suite passes its own reality scan
python -X utf8 scripts/run_gates.py --state templates/task-state.example.json --root . --tier T2
python -X utf8 scripts/gate_verify_rank.py --record templates/verify-record.example.json --tier T3
```

## Known limitations (declared honestly, no whitewash)

1. **[Dimensionality-reduced implementation]**: self-verification scoring does not use model logits (unobtainable agent-side); it uses an explicit 1-20 rubric instead. **No claim of reproducing paper-grade precision.** Compensation: without logits, disagreement across repeated scorings under the same rubric is the only available confidence proxy. When the weighted top-two gap is `< 1.5` (`spec.self_consistency`), the result sits in the "could-flip" zone — T3 must resample before deciding, and the sample count must actually become `repeats + extra_samples`. Triggered only at T3, only for small gaps — resampling costs tokens.
2. Token/time defaults are **proxy metrics** (`*_proxy`), self-reported by the agent; the scripts **cannot independently reconcile against the platform**. Compensation: `cost_metering` is the real-bill back-fill port. T2+ **must explicitly declare** `source` (`unavailable` / `self_reported` / `terminal_ui` / `api_usage` / `cli_usage`); if you can't get one, write `unavailable` — **silence is not a declaration**. Claiming auditable (`api_usage`/`cli_usage`) requires `tokens_actual` + a checkable `evidence_ref` (empty phrases don't count); when the source is not auditable, `tokens_actual` **must not appear**. With real bills, ceilings enforce by tier (T1 100k / T2 400k / T3 1.2M).
3. `gate_reality_scan` is lexical + Python-AST static scanning; no cross-language semantic analysis. Non-Python files get lexical and non-empty checks only.
4. The built-in JSON Schema validator is a **subset implementation**; standard `$ref` is unsupported — use the `$ref_inline` / `$defs_inline` convention.
5. Soft constraints (skills & rules) **can be drifted around by the model**; only `scripts/` exit codes are provable. This is a design tradeoff, not concealment. The 7 formerly self-enforced items now live as structured fields in `task-state.json`, machine-checked by `gate_checklist.py` (mapping in `reference-gates.md`); but that layer only blocks "skipped wholesale" and "written as empty phrases" — **it cannot grade the quality of thinking** (that stays native). Unhardened `[soft]` items still rely on conscience.
6. Install performs **no hash verification** — existence and non-emptiness against the inventory only. Per-file sha256 checks happen **only at `pack.py` packaging time**, so post-distribution tampering is undetectable.
7. **No Python no longer means zero enforcement, but not full either**: Node only runs 4/5 gates (`reality_scan` needs Python AST; the Node side returns `USAGE_ERROR` explicitly instead of pretending). Only when both are missing is it `soft_only`, and `acs_doctor` says so with exit=1 — **an explicit verdict, not a silent downgrade**.
8. **Multi-terminal targets are path-verified, not load-verified**: claude / cursor / windsurf / codex / qoderwork targets are written into `spec/terminals.json` per each terminal's published conventions, with dual-implementation byte-identical path resolution and copy verification — but rule loading has not been empirically confirmed inside those terminals. Cursor's `.mdc` needs its frontmatter added by you; this suite does not write it.
9. **L3 is not airtight**: `hooks/pre-commit` is off by default (opt-in), and even installed it can be bypassed with `git commit --no-verify` — **an intentionally preserved owner escape hatch**, not a vulnerability. It only intercepts `git commit`, not `git push`, rebase, or other write paths. The truly unbypassable layer is CI.
10. `hooks/pre-commit` and the CI yml are **outside `gate_reality_scan`'s coverage** (no extension / `.yml` not in `DEFAULT_EXTS`); covered instead by `bash -n` and YAML-parse tests. An indented-wrong workflow doesn't error — it simply never runs — so that assertion is mandatory.
11. **v1.1's T3-only contract items trigger only at T3** (edge-gate coverage / artifact registry / model tier / double-blind review; acceptance binding is the exception, active from T1): tiers below T2 don't check them — the tiering itself is the token-saving design. The cost: T2 deliverables get no artifact lineage or double-blind protection. If a T2 delivery needs equal strength, set `tier` to T3 and re-run the gates — no extra rules needed.
12. **Suite hygiene checks are read-only audits** (v1.1, from codex-skill-refactor's governance principles): tombstone files / dead local links / spec sections without consumers / byte-identical duplicate files — four items pinned in pytest. "Suspected orphans" are reported, never deleted — deletion is the owner's call; scripts never auto-clean.

## Environment

Python ≥ 3.8, **zero third-party dependencies** for the gates, installer and skills (the self-test suite itself uses pytest — CI installs it explicitly). On Windows prefer `$env:PYTHONUTF8='1'` and `python -X utf8` to avoid mojibake in Chinese output. PowerShell separates statements with `;` (no `&&`); `install.ps1` uses basic cmdlets only and is compatible with ConstrainedLanguage mode.

The two installers **default to different Python interpreters** (a platform-convention mismatch, not a bug): `install.ps1` defaults to `python`, `install.sh` defaults to `python3`; override with `-PythonExe` / `--python` when needed.

`install.ps1` messages are **deliberately all ASCII English**: Windows PowerShell 5.1 parses BOM-less `.ps1` as ANSI, and some multibyte characters end in byte `0x5C` (`\`), breaking string closure and failing the parse — a trap hit in real testing and fixed, not laziness. `install.sh` runs under `bash` (POSIX-sh-compatible style). Development machine is Windows, but it passed `bash -n` and real installs (including seven end-to-end `--with-hook` runs) under Git Bash and is pinned in pytest; **real Linux/macOS runs are the CI `installer` job's responsibility** (ubuntu-latest + macos-latest). First run in a new environment: `--dry-run` first, then install.

## What's new in v1.1.0

- **Bounded retry, machine-checked**: `retries` ceiling + mandatory `retry_reason` + `delta_from_last` — regenerate-and-hope loops are now structurally impossible.
- **T3 task-graph contracts**: edge gates with `expect_exit`, explicit barrier policies, artifact registry with `produced_by`/`supersedes` lineage, `contract_version` semver checks, `model_tier` registration on model-calling nodes.
- **Acceptance binding (T1+)**: acceptance commands must hit the declared artifacts — green tests on an empty diff no longer count as delivery (the SWE Refactor Bench blind spot).
- **T3 double-blind review**: ≥2 independent reviewers who can't see each other's reasoning; builder banned; conflicting verdicts must be arbitrated on record.
- **T3 self-consistency resampling**: top-two gaps < 1.5 force real extra samples before a verdict.
- **Suite hygiene pinned in pytest**: tombstones, dead links, orphaned spec sections, duplicate files — read-only audit, report never deletes.
- **QoderWork terminal support** added to `spec/terminals.json` (detect order, skills dir, rules target).
- Cross-implementation exit-code consistency expanded to T3 positive samples; suite self-test grew to 377.

## License

[MIT](LICENSE) © 2026 Nurburgring-Zhang

---

# 简体中文

**一句话**：给 agent 装上「skill（会做） + harness（能证明做了） + loop engineering（知道何时停） + graph engineering（知道怎么拆）」四件套，在强化执行能力的同时把 token 与时间成本压下来。

## 定位：增强层，不是替代层

本套件**叠加在 agent 终端原生能力之上，只做加法**：

| | 说明 |
| --- | --- |
| **不做什么** | 不禁用、不接管、不改写终端原生的工具、检索能力、任务清单、记忆系统、权限与审批机制 |
| **原生优先** | 终端已有等价能力（原生任务清单/计划、原生检索子代理、原生记忆）就用原生的；`templates/` 仅在原生缺位时兜底，禁止另起并行体系 |
| **只补三样** | ① 分级触发的执行 SOP（何时该重、何时该轻）② 可机检的准出退出码（把「我觉得做完了」变成 0/1）③ 成本闸门的量化阈值 |
| **冲突裁决** | 主人显式指令 > 终端原生安全/权限/审批约束 > 本套件硬门禁 > 本套件软约束。**永不覆盖前两者** |
| **强制的边界** | 强制对象是**标准与证据**，不是具体工具路径；等价手段达成均可，只要证据可机检 |

- **软约束**：`skills/`、`rules/`、`AGENTS.md` —— 注入 Context，让 agent 想起来该怎么做。
- **硬约束**：`scripts/` 六个门禁脚本 —— 真实运行、有退出码，是**唯一可证明**的准出依据。
- **外部强制点**（可选）：`hooks/pre-commit` 与 `.github/workflows/acs-gates.yml` —— 由 git / CI 触发，**不依赖模型自觉**。
- 三者不可互替。**能跑脚本时，「我已检查」不算证据。**

## 为什么需要它（反面基线，真实实测数据）

某 27B 开源模型接入 Agent Harness 完成 9 个任务：

| 指标 | 该模型 | 同框架对照模型 | 倍数 |
| --- | --- | --- | --- |
| 总 token | 13,995,350 | 956,630 | 14.6× |
| 其中输入 token | 13,718,510（占 98%） | 927,401 | 14.8× |
| 总耗时 | 22,564.37s（6h17m） | 1,343.75s（22min） | 16.8× |
| 请求次数 | 197 | 87 | 2.3× |
| 单题峰值（3D 网站） | 11,533,959 token / 124 请求 / 18,639.20s | 161,019 / 15 / 540.95s | token 71.6× |
| 该单题拆分 | 75 step / 3 turn | 8 step | —— |
| 思考时间占比 | 55.6%（token 占比 60.7%） | 29.6%（36.6%） | —— |
| 该单题「其它运行时间」 | 12,632s，占总时长 59% | —— | —— |

质量上它拿到 18/18 满分，对照组 14/18。**能力不是瓶颈，工程约束才是。**
根因四条：任务拆分归于复杂、**单步目标过重**、**完成标准不清晰**、**上下文反复回灌**，由此产生大量空转。

本套件的每一条硬阈值都直接对症这四条。完整证据表（E1-E27）见 [DESIGN.md](DESIGN.md)。

## 30 秒上手

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Target C:\path\to\workspace
```

```bash
# Linux / macOS
chmod +x install.sh && ./install.sh --target /path/to/workspace
```

两个安装器默认 `--mode auto`：先跟 `acs_doctor` 探针问清楚「这台机器有什么、这个工作区是哪个终端」，
再决定落点。当前识别 qoder / qoderwork / claude / cursor / windsurf / codex，全不命中则显式落到 generic（不假装识别）。
想先看体检结论：

```bash
python -X utf8 scripts/acs_doctor.py --target .        # 无 Python 时：node scripts/node/acs_gates.mjs doctor --target .
```

它会直接告诉你执行力等级：**full**（Python，5/5 道门）/ **partial**（只有 Node，4/5）/ **soft_only**
（两者都无，强制力归零，exit=1）。探针本身也是双实现 —— 它要回答的正是「有没有 Python」，
若只能用 Python 跑，最需要它的场景恰好用不上。

先跑 `-DryRun` / `--dry-run` 看计划，再实装。安装脚本会在**装完后真实跑一次门禁**，
正样本判定 PASS（exit=0）才算安装生效——不做「拷完就宣布成功」这种事。

**升级必须加 `-Force` / `--force`**：目标文件内容相同时报 `same`（无需做事）；
**内容不同却未加 `-Force` 会点名旧文件并 exit=1**，不会静默跳过 —— 否则旧版本会直接通过「文件存在」式的装后验证，得到一个假成功（这是实测踩到并已修掉的坑）。

开工前建状态文件：

```bash
mkdir .acs
cp .acs/templates/task-state.example.json .acs/task-state.json   # 按真实任务改写
```

跑门禁：

```bash
python -X utf8 .acs/scripts/run_gates.py --state .acs/task-state.json --root . --tier T2
```

`0=PASS` ｜ `1=BLOCK` ｜ `2=USAGE_ERROR`。**后两者一律视为「未验证 = 未完成」。**

## 目录

```
AGENTS.md                跨终端通用入口（被主流 agent 终端自动读取）
DESIGN.md                设计基线 + 证据表 E1-E27 + 验收标准 A1-A18 + 执行力三层
hooks/pre-commit         外部强制点（opt-in，默认不装；双运行时，退出码原样传导）
.github/workflows/       CI 四作业：gates / node-fallback / installer / pre-commit-hook
manifest.json / VERSION  清单与版本（install_check.py 据此校验齐全性）
install.ps1 / install.sh 安装脚本（装前自检 → 拷贝 → 装后真实验证）

skills/
  universal-task-code/   主入口（瘦，76 行）+ reference-gates.md + reference-creed.md
  loop-engineering/      停止协议、四道成本闸门、安全阀、loop-until-dry
  graph-engineering/     节点契约、拆分四切法、并行结构、子代理上下文隔离
  self-verify-scaling/   1-20 分 rubric、三维协同扩展、pivot 排序、方向偏离检测
  token-thrift/          输入 token 节流、稳定前缀纪律、读取预算、压缩总结

scripts/                 六门 + 编排 + 安装自检，纯 stdlib 零依赖（Node 镜像同覆盖）
templates/               两份 schema + 两份正样本 + 交接总结模板
tests/test_gates.py      377 项双向验证（正样本 PASS / 负样本 BLOCK，含「门禁须纯只读」的定位机检；v1.1 新增：T3 专属负样本分级失灵检测、双实现 T3 正样本、套件卫生机检——墓碑文件/失效本地链接/无消费者 spec 节/重复文件）
```

## 五个技能怎么用（渐进披露，禁止一次全装）

| 场景 | 加载 |
| --- | --- |
| 任何非闲聊任务的起手 | `universal-task-code`（常驻主入口） |
| 定停止条件 / 收敛 / 重试 / 判空转 | `loop-engineering` |
| 拆并行子任务 / 写节点契约 / 派子 agent | `graph-engineering` |
| T3 方案竞标 / 打分排序 / 方向校准 | `self-verify-scaling` |
| 上下文吃紧 / 压缩总结 / 洁净续跑 | `token-thrift` |

## 六道硬门禁

| 脚本 | 拦什么 |
| --- | --- |
| `gate_state_validate.py` | 状态契约不合规、任务图有环/孤岛、节点缺完成标准、`owner==verifier`、确定性节点却要调模型、inputs 写「上文」；**v1.1**：边门 `kind=cmd` 缺 `expect_exit`、T3 图零边门、汇聚 `policy=min_success` 缺 `min_count` 或超出等待分支数、T3 工件登记缺失/`produced_by` 指向不存在节点/`supersedes` 谱系断链或成环、`contract_version` 非法、T3 调模型节点缺 `model_tier`、验收命令未绑定声明的产出物（交白卷盲区） |
| `gate_loop_guard.py` | 计量字段缺失（删字段绕过闸门）、安全阀上限缺失、单步多产出、预算超 30min、总结超 400 字、上下文超 20000 字节、读文件超 5 个、连续 2 步无新证据、思考占比 >0.40、轮次/时长超安全阀；**v1.1**：有界重试（`retries` 超上限、重试缺 `retry_reason`/`delta_from_last`——同法重试即空转） |
| `gate_verify_rank.py` | 评分标准不足 5 项、权重和≠1、重复评估次数不符、极差>5、中位数造假、加权分手改、排序与分数矛盾、平局未处置、未过门槛线、进度分连续不升；**T3 自一致性**：冠亚分差 <1.5 未重采样、重采样只登记不加样本、gap 手改 |
| `gate_loop_guard.py` 内的 `cost_metering` | T2+ 未声明计量来源、声称可审计却无数字/无证据、证据是空话、来源不可审计却填 `tokens_actual`、分项与总量对不上账、真账超分级上限 |
| `gate_reality_scan.py` | 残留待办标记、降级措辞、替身实现、空壳函数（Python AST）、语法不可解析 |
| `gate_checklist.py` | 分级必填门未填、证据是空话、对抗审核 <2 轮、上线测试 <2 轮、跳门无审批人；**七项软约束硬化**：双向钢人条数/依据、认知六要素、规划四要素、互审闭环（发现→修复→复验、禁自评签字）、验收 `actual` 实测值、交付报告四要素、T3 的 RSI 闭环，字段写错门也拦；**v1.1**：T3 双盲审查（≥2 名独立审查者、禁建造者/重复、verdict 冲突必须仲裁留痕） |
| `run_gates.py` | 一次编排以上五门并汇总（USAGE > BLOCK > PASS 优先级） |

分级阈值（`T0/T1/T2/T3`）：候选数 `0/1/1/3~5`，重复评估 `0/1/2/3`，门槛线 `—/14/16/17`，
安全阀 `1轮5min / 3轮15min / 6轮45min / 10轮120min`。T3 另有硬地板：`correctness≥17` 且 `risk≥16`。

## 执行力三层（L3 外部强制点）

不分层就会把「写进文档了」当成「强制住了」：

| 层 | 落在哪 | 约束力 | 绕过方式 |
| --- | --- | --- | --- |
| L1 软约束 | `skills/` `rules/` `AGENTS.md` | Context 注入，无退出码 | 模型漂移即失效，且无人报错 |
| L2 硬门禁 | `scripts/run_gates.py`、`scripts/node/acs_gates.mjs` | 退出码可证 | 模型不主动调用就等于不存在 |
| L3 外部强制点 | `hooks/pre-commit`、`.github/workflows/acs-gates.yml` | 由 git / CI 触发，**不依赖模型配合** | hook 可被 `--no-verify` 绕过（有意保留）；CI 那一层绕不过 |

**hook 是 opt-in，默认不装** —— 未经主人同意往仓库塞 hook 属于接管，与增强层定位相背：

```bash
./install.sh --target /path/to/repo --with-hook       # Linux / macOS（另会 chmod +x 并用 -x 复验）
```

```powershell
powershell -File .\install.ps1 -Target C:\path\to\repo -WithHook
```

目标不是 git 仓库时**显式失败**（exit=1），不静默跳过——「装了但没生效」比不装更危险。
hook 本身纯只读，**不改暂存区**；Python 优先、5/5 道门，Node 兜底 4/5，两者皆无则拦下而不静默放行。
本次提交确与任务无关（如改 README 错别字）时，用 git 原生的 `git commit --no-verify` —— **逃生口必须主人显式动手，而不是脚本自己放水**。

CI（`.github/workflows/acs-gates.yml`）四个作业：`gates`（ubuntu 3.8 / ubuntu 3.12 / macos 3.12 全量门禁 + pytest）、
`node-fallback`（node 16 / 20，并断言 reality 必须 exit=2）、`installer`（**ubuntu + macos 上真跑 install.sh**，含 `--force` 语义）、
`pre-commit-hook`（真 git init 仓库：缺 state 必拦、`--no-verify` 必通、正样本必放行）。

## 自证（可复现）

```bash
python -X utf8 scripts/install_check.py --root .                      # 清单齐全性
python -X utf8 -m pytest tests/test_gates.py -q                       # 377 passed
python -X utf8 scripts/gate_reality_scan.py --root .                  # 套件自身通过真实性扫描
python -X utf8 scripts/run_gates.py --state templates/task-state.example.json --root . --tier T2
python -X utf8 scripts/gate_verify_rank.py --record templates/verify-record.example.json --tier T3
```

## 已知限制（如实声明，不粉饰）

1. **`[降维实现]`**：自验证评分不用模型 logits（agent 侧拿不到），改为显式 1-20 分 rubric。**不宣称复现论文精度。**
   已做的补偿：拿不到 logits 时，「同一份 rubric 重复评分的分歧度」是唯一可得的置信度代理。冠亚加权分差 `< 1.5`（`spec.self_consistency`）
   就落在「可能改判区间」，T3 必须先重采样再定案，且样本数必须真的变成 `repeats + extra_samples`。只在 T3、只在小分差时触发 —— 重采样花 token。
2. token / 耗时默认是**代理指标**（`*_proxy`），由 agent 自报；脚本**无法向平台独立核账**。
   已做的补偿：`cost_metering` 是真账回填口。T2+ **必须显式声明** `source`（`unavailable` / `self_reported` / `terminal_ui` / `api_usage` / `cli_usage`），
   拿不到就写 `unavailable` —— **沉默不算声明**；声称可审计（`api_usage`/`cli_usage`）就必须给 `tokens_actual` + 可复查的 `evidence_ref`（空话不算）；
   来源不可审计时**禁止出现** `tokens_actual`。有真账就按真账拦（T1 100k / T2 400k / T3 1.2M）。
3. `gate_reality_scan` 是词法 + Python AST 静态扫描，不做跨语言语义分析；非 Python 文件仅词法与非空检查。
4. 内置 JSON Schema 校验器是**子集实现**，不支持标准 `$ref`，改用 `$ref_inline` / `$defs_inline` 约定。
5. 软约束（技能与规则）**可能被模型漂移绕过**；只有 `scripts/` 的退出码是可证明的。这是设计取舍，不是缺陷掩饰。
   原先靠自觉的 7 条已搬成 `task-state.json` 结构化字段并由 `gate_checklist.py` 机检（见 `reference-gates.md` 的映射表）；
   但该层只能拦「整段跳过」与「写空话」，**无法评定思考质量**（那仍由原生能力完成），未硬化的 `[软]` 条目依旧靠自觉。
6. `install` 阶段**不做哈希校验**，只核对清单存在性与非空；sha256 逐项校验**仅发生在 `pack.py` 打包时**，因此无法检测分发后被篡改。
7. **无 Python 不再等于强制力归零，但也不是全门**：只有 Node 时跑 4/5 道门（`reality_scan` 需 Python AST，Node 侧直接 `USAGE_ERROR` 而不假装能跑）；
   两者皆无才是 `soft_only`，此时 `acs_doctor` 以 exit=1 明说强制力归零 —— **显式结论，不是静默降级**。
8. **多终端落点已验路径，未验「确被加载」**：claude / cursor / windsurf / codex / qoderwork 的落点按各自公开约定写入 `spec/terminals.json`，
   已做双实现逐字一致与拷贝验证，但未在这些终端内实测规则是否真的被注入；Cursor 的 `.mdc` 需自行补 frontmatter，本套件不代写。
9. **L3 外部强制点不是密不透风**：`hooks/pre-commit` 默认不装（opt-in），装了也可被 `git commit --no-verify` 绕过 ——
   这是**有意保留的主人逃生口**，不是漏洞；它只拦 `git commit`，不管 `git push`、rebase 与其他写入路径。真正绕不过的是 CI 那一层。
10. `hooks/pre-commit` 与 CI 的 yml **不在 `gate_reality_scan` 扫描范围内**（无扩展名 / `.yml` 不在 `DEFAULT_EXTS`），
    改由 `bash -n` 与 YAML 解析测试兼顾；缩排写错的 workflow 不会报错只会不跑，所以那一项断言是必需的。
11. **v1.1 的 T3 专属契约项只在 T3 触发**（边门覆盖/工件登记/模型档位/双盲审查；验收绑定是例外，T1 起全级生效）：
    T2 及以下不检这些项，分级本身就是省 token 设计；代价是 T2 交付物没有工件谱系与双盲保障——若某次 T2 交付需要同等强度，
    把 `tier` 定为 T3 再跑门禁即可，不需要另写规则。
12. **套件卫生机检是只读审计**（v1.1，来自 codex-skill-refactor 的治理原则）：墓碑文件/失效本地链接/无消费者 spec 节/内容完全相同的重复文件，
    四项由 pytest 固化；「疑似孤立」只报不删——删除与否是主人的决定，脚本永远不自动清理。

## 环境

Python ≥ 3.8，门禁 / 安装器 / 技能**零第三方依赖**（自测套件本身用 pytest，CI 已显式安装）。Windows 建议 `$env:PYTHONUTF8='1'` 且用 `python -X utf8` 以避免中文输出乱码。
PowerShell 用 `;` 分隔语句（不支持 `&&`）；`install.ps1` 只用基础 cmdlet，兼容 ConstrainedLanguage 模式。

两个安装脚本的 Python 解释器**默认值不同**（各平台常规不一致，不是 bug）：`install.ps1` 默认 `python`，`install.sh` 默认 `python3`；
需要时用 `-PythonExe` / `--python` 显式指定。

`install.ps1` 的提示信息**故意全为 ASCII 英文**：Windows PowerShell 5.1 会把无 BOM 的 `.ps1` 按 ANSI 解析，
部分多字节字符尾字节为 `0x5C`（`\`）会破坏字符串闭合导致解析失败——这是实测踩到并已修掉的坑，不是省事。
`install.sh` 用 `bash` 执行（POSIX sh 兼容写法）。开发机为 Windows，但已用 IDE 自带的 Git Bash 跑过 `bash -n` 与真实安装（含 `--with-hook` 的七项端到端实测），
并已纳入 pytest；**原生 Linux / macOS 上的实跑由 CI 的 `installer` 作业负责**（ubuntu-latest + macos-latest）。
首次在新环境使用仍建议先 `--dry-run` 看计划，再实装。

## v1.1.0 新增

- **有界重试（机检）**：`retries` 上限 + 必填 `retry_reason` 与 `delta_from_last` —— 「报错→原样再生成」的永动机被结构性封死。
- **T3 任务图契约**：边门必带 `expect_exit`、汇聚 policy 显式四选一、工件登记（`produced_by`/`supersedes` 谱系不断链不成环）、`contract_version` semver 校验、调模型节点登记 `model_tier`。
- **验收绑定（T1+ 全级）**：验收命令必须打到声明的产出物上 —— 交白卷也能测试全绿的盲区被堵死（SWE Refactor Bench 教训）。
- **T3 双盲审查**：≥2 名互相看不见对方推理的独立审查者，禁建造者兼任、禁重复，verdict 冲突必须仲裁留痕。
- **T3 自一致性重采样**：冠亚加权分差 < 1.5 时强制真的增加样本后再定案，防把噪声当结论。
- **套件卫生机检固化入 pytest**：墓碑文件/失效本地链接/无消费者 spec 节/重复文件，只读审计，只报不删。
- **新增 QoderWork 终端支持**：`spec/terminals.json` 增加 qoderwork 识别标记、技能目录与规则落点。
- 双实现退出码一致性扩展到 T3 正样本；自证测试增至 377 项。

## 许可

[MIT](LICENSE) © 2026 Nurburgring-Zhang
