# Agent Core Suite (ACS) v1.2.0 — 设计与标准（G2 规划门四件套）

> 面向 agent 终端的工作标准 SOP 套件：skill + harness + loop engineering + graph engineering。
> 目标双约束：**能力上限抬高** 且 **token/时间成本下降**。

## 定位前置：增强层（augmentation layer）

本套件是**叠加层，不是替代层**。这是一条**设计约束**，下文所有决策均受其限制：

| 约束 | 含义 | 对设计的硬限制 |
| --- | --- | --- |
| 不接管原生能力 | 不禁用/不改写终端原生工具、检索、任务清单、记忆、权限审批 | 套件不得包含任何修改终端配置、拦截工具调用、覆写系统提示的代码 |
| 原生优先 | 原生有等价能力时用原生的 | `templates/` 定位为**兜底契约**而非强制载体；门禁只校验「状态是否合约」，不校验「是否用了套件自己的工具」 |
| 只补三样 | 分级 SOP / 可机检退出码 / 量化成本阈值 | 不重建检索、不重建记忆、不重建编辑器；`scripts/` 只做判定，不做执行 |
| 冲突裁决顺序 | 主人指令 > 终端原生安全/权限/审批 > 本套件硬门禁 > 软约束 | 门禁脚本只返回退出码，**无任何阻止或回滚动作**（不写文件、不杀进程、不改环境） |
| 强制的对象 | 标准与证据，不是工具路径 | 验收均以「证据是否可机检」为准，不限定达成手段 |

## 0. 证据基线（本套件的每条硬约束都必须可追溯到证据）

| 编号 | 来源 | 关键事实 | 推导出的约束 |
| --- | --- | --- | --- |
| E1 | 新浪科技 2026-08-27《「自验证」框架让开源模型反超 Fable 5》（斯坦福 LLM-as-a-Verifier，arXiv 2607.05391） | 同一模型生成 5 条候选轨迹后自验证排序，Terminal-Bench 2.1 成功率 79%→88%，总成本仍低约 11× | 高风险任务用 N 候选 + 独立验证器排序（`self-verify-scaling`） |
| E2 | 同上 | 离散评分在 Terminal-Bench V2 上产生高达 27% 平局；提高评分粒度（1-20）后 100 次比较 77 次选对且零平局 | 评分必须细粒度 1-20 分，禁止「好/中/差」三档 |
| E3 | 同上 | 评分粒度 × 重复评估 × 评价标准拆分三个维度应协同扩展（scale in tandem） | rubric 必须拆分标准（默认 5 项）+ 重复评估（R≥2） |
| E4 | 同上 | 两两比较 O(N²) → 与少量 pivot 比较后近似排序 O(Nk) | 候选排序用 pivot 法，k=2，禁止全量两两比较 |
| E5 | 同上 | Verifier 分数与 agent 实际任务进展正相关，可实时追踪是否朝正确方向推进 | 评分用于方向偏离检测；分数不升 → 触发空转闸 |
| E6 | 同上 | 验证器必须独立于执行者，不信任 agent 自报完成（claimedDone） | Builder/Verifier 强制分离，禁止自评签字 |
| E7 | AI科技评论《阿里开源 Qwen3.8-27B 本地实测》 | 9 个 Agent 任务耗 13,995,350 token / 197 请求 / 22,564s；输入 token 13,718,510 占 98% | 输入侧（上下文回灌）才是成本主因 → 回灌禁令 |
| E8 | 同上 | A9 单题耗 11,533,959 token、124 次请求、18,639s；对照模型同题 161,019 token、15 请求、541s（token 548×、耗时 34×） | 单任务必须设成本上限与升级点，超限即停 |
| E9 | 同上 | 根因：任务拆分归于复杂、**单步目标过重**、**完成标准不清晰**、**上下文反复回灌**，大量时间在空转 | 窄步闸：单步一产出 + 完成标准可机检 |
| E10 | 同上 | Qwen 拆 75 step / 3 turn / 124 请求，对照 8 step / 15 请求；「拆得多不等于拆得好」 | 步数不是指标，**单步上下文体积与完成标准**才是 |
| E11 | 同上 | 思考时间占比 55.6%、思考 token 占比 60.7%（对照组 29.6% / 36.6%）；「其它运行时间」占 A9 总时长 59% | 思考预算闸：思考占比警戒线 ≤40%，超线转为写最小可执行验证 |
| E12 | **套件自定**（无来源原文佐证，仅由 E7-E11 推导） | 连续无新证据的重试对成本无贡献：A9 的「其它运行时间」占 59%，属反复执行与等待 | 空转闸采用 two-strike：连续 2 轮无新证据即停并升级。**此阈值为工程取值，非引用他方结论** |
| E13 | 《Agent 五大工程体系：Prompt/Context/Loop/Graph/Harness》 | Harness 提供边界 → Graph 组织节点 → 节点内跑 Loop → 每轮构造 Context → Context 中组织 Prompt | 套件分层与命名严格对齐五层，避免层级错位 |
| E14 | 同上 | Loop 弱 = 过早宣布完成或无限循环；Graph 弱 = 无依赖任务串行化、每个小步都调模型、循环不收敛 | 停止协议 + 安全阀；确定性节点禁止调模型 |
| E15 | 同上 | 节点 = 边界清晰的工作单元；边 = 数据依赖（不是时间顺序）；验证门挂在边上 | 节点契约 Schema 强制 inputs/outputs/acceptance |
| E16 | 《拆解 Harness Engineering 和 Loop Engineering》 | Pattern 层：Progressive Disclosure（对抗 context rot）、State Outside Context、Clean-Context Continuation、Approval Checkpoints、Planner/Evaluator Split、Approved Fixtures | token-thrift 五策略与 Builder/Verifier 分离的直接依据 |
| E17 | 同上 | Anthropic prompt caching：cache write 1.25×、cache hit 0.1×（省 90%）、TTFT 降约 85%；前缀内任何字符变动即失效 | 稳定前缀纪律：禁止时间戳/随机 ID/自增计数进入前缀 |
| E18 | 同上 | verification 轴：computational（typecheck/unit test，确定性）↔ inferential（LLM-as-judge） | 能用确定性验证的一律不用模型判定 |

### v1.1 增补证据（来自 Loop+Graph+MultiAgents 合集 part1/part2 深度调研，含其提及的项目源码与文章）

| 编号 | 来源 | 关键事实 | 推导出的约束 |
| --- | --- | --- | --- |
| E19 | Anthropic 图工程（part1：验证门挂边上；节点/步骤验收同规矩） | 边是数据契约，上游产出放行下游前必须过门；没有期望退出码的命令验证无法判成败 | T3 图至少一条边门；边门 `kind=cmd` 必须写 `expect_exit`（`spec.edge_gates`） |
| E20 | 三种 Agent Loop Runtime 对比 + Orca 并行（part1/part2：汇聚语义） | 汇聚行为不声明就是隐式 all_success，一旦有人以为「超时也能过」，故障变成误会 | `barriers[].policy` 四选一显式声明；`min_success` 必须带合法 `min_count`（1 ≤ min_count ≤ len(waits_for)） |
| E21 | Loop Engineering 实战案例 FinBot（part1） | 生成→报错→原样再生成，是永动机不是修复；重试无上限、无留痕、无变化 | 有界重试：`retries ≤ max_retries`，且必须写 `retry_reason` 与 `delta_from_last`（`spec.retry`） |
| E22 | SWE Refactor Bench（part2「AI 研发范式演进」） | 行为测试全绿 ≠ 有产出：原系统本来就能过，交白卷也满分；尺子量的是「改没改坏」不是「有没有改」 | 验收命令必须绑定声明的产出物（T1+ 全级，`spec.acceptance_binding`） |
| E23 | Anthropic 图工程第 6 步（part1） | 两名互相看不见对方推理的独立审查者；同一个人审两轮不是双盲；分歧说明验收标准有歧义 | T3 双盲审查：≥2 独立审查者、禁建造者/重复、verdict 冲突必须仲裁留痕（`spec.double_blind`） |
| E24 | Artifact Memory vs Transcript Memory + Tutti 数据契约版本化（part1/part2） | transcript 不是数据库，产出物必须登记为带溯源的工件；supersedes 链是最小版本谱系（ADR 同构） | T3 工件登记：`produced_by` 指向真实节点，`supersedes` 谱系不断链不成环（`spec.artifacts`） |
| E25 | Tutti 教训（part1：v2.3.0→v2.4.0 一眼可查） | 契约演进必须可追溯；校验器识别「来自更新套件的文件」才能提示升级而不是报莫名字段错误 | 状态文件顶层 `contract_version` 必须匹配 semver；旧版兼容，新版只告警（`spec.contract`） |
| E26 | 图工程模型分层（part1） | 无聊节点跑便宜模型、判断节点跑强模型；不上图、不登记，这笔账永远糊涂 | T3 调模型节点必须登记 `model_tier`（计量字段，不拦你用强模型）（`spec.model_tier`） |
| E27 | codex-skill-refactor 治理原则（part2） | 不留电子墓碑（.retired/.bak/.old/.orig）、本地链接必须有效、spec 节必须有消费者、不维护内容相同的重复文件 | 套件卫生四项机检固化入 pytest（只读审计：只报不删，删除是主人的决定） |

E19-E26 全部双实现（Python + Node）同批落地，阈值只存 `spec/thresholds.json` 对应节；E27 是 pytest 层的套件自洽守护，不占门禁脚本。

## 1. 项目规划（阶段 / 里程碑 / 依赖 / 并行度）

| 阶段 | 产出 | 依赖 | 时长 | 准出 |
| --- | --- | --- | --- | --- |
| S1 | DESIGN.md（本文件） | 双文章证据 | 20min | 证据表齐全（首发 E1-E18，v1.1 增补 E19-E27）、四件套齐全 |
| S2 | skills/ 五技能 | S1 | 30min | 主入口 ≤120 行；子技能各 ≤160 行 |
| S3 | scripts/ 六脚本 | S1 | 30min | 零第三方依赖、真实可运行、退出码语义明确 |
| S4 | templates/ + rules/ + AGENTS.md | S1 | 20min | schema 可被脚本校验 |
| S5 | tests/ 实跑 | S2,S3,S4 | 20min | pytest 全绿、门禁在正/负样本上均按预期判定 |
| S6 | 本机安装 + 生效验证 | S5 | 15min | 真实 FS 读回一致（防「写入不落盘」故障态） |
| S7 | 打包 zip + install 脚本 | S6 | 15min | 解包后可在其他终端安装；manifest 校验通过 |
| S8 | 双 AI 对抗审核 ×2 + 交付报告 | S7 | 25min | HIGH 项全部处置；四要素齐全 |

并行度：S2 / S3 / S4 无相互依赖，可并行；S5 是汇聚点（fan-in barrier）。

## 2. 详细设计（架构 / 数据 / 契约）

### 2.1 五层归位（对齐 E13）

```
Harness 层  →  G0-G6 门禁 + scripts/ 可执行硬门禁 + rules/AGENTS.md 常驻边界
   ↓ 承载
Graph 层    →  graph-engineering：任务 DAG、节点契约、fan-out/fan-in、边上验证门、模型分层
   ↓ 放大一个节点
Loop 层     →  loop-engineering：P0-P6 七道门 + 四道成本闸门 + 停止协议 + 安全阀
   ↓ 每轮构造输入
Context 层  →  token-thrift：渐进披露、状态外置、稳定前缀、洁净续跑、压缩总结
   ↓ 组织语言
Prompt 层   →  各 SKILL.md 正文措辞（含 rubric 模板）
```

### 2.2 分级触发路由表（成本闸门总开关）

| 级 | 判定条件（任一命中即升级） | 强制流水线 | 自验证候选 N | 重复评估 R | 常驻加载 |
| --- | --- | --- | --- | --- | --- |
| T0 | 闲聊 / 纯信息问答 / 单文件只读 | 仅诚实纪律 | 0 | 0 | 无（不加载套件正文） |
| T1 | 单文件、可逆、预计 ≤15min、无外部副作用 | P0(压缩) → P3 → G3 真实性扫描 → G4 | 1（rubric 自检） | 1 | 主入口 |
| T2 | 多文件 / 有持久状态 / 需测试 / 涉及构建 | P0-P6 全量 | 1（Builder≠Verifier 分离） | 2 | 主入口 + 按需子技能 |
| T3 | 不可逆（删除/迁移/部署/推送）或 架构级 或 安全相关 或 ≥5 文件跨模块 | P0-P6 全量 + 候选竞标 | 3~5 | 3 | 全部 |

判定证据必须写入 `task-state.json.tier` 与 `tier_reason`，可被 `gate_checklist.py` 核查。

### 2.3 四道成本闸门（Loop 层，直接对症 E7-E12）

| 闸门 | 硬阈值（默认） | 机检方式 | 违反后的强制动作 |
| --- | --- | --- | --- |
| 窄步闸 Narrow-Step | 单步 1 个可验证产出；预算 15~30min；完成标准必须是命令/文件/断言 | `gate_loop_guard.py` 校验 step.outputs 长度与 step.acceptance 类型 | 拆分该步后重新开工 |
| 回灌禁令 No-Refeed | 跨步只传压缩总结（≤400 字）+ 状态文件指针；单步引用上文体积 ≤ 阈值 | 校验 step.context_bytes 与 step.refeed_full_history 标记 | 改为读 `task-state.json` + 摘要 |
| 空转闸 Spin Detector | two-strike：连续 2 轮 evidence_delta=0（无文件变更 / 无新通过测试 / 无新事实） | 校验相邻 step 的 evidence_delta 序列 | 立即停止 → 升级（换方案或问主人），禁止继续同法重试 |
| 思考预算闸 Think-Budget | think_ratio ≤ 0.40 | 校验 step.think_ratio | 转为「写最小可执行验证」而非继续推理 |

### 2.4 数据契约

- `task-state.json`：唯一权威状态（State Outside Context）。字段见 `templates/task-state.schema.json`。
- `verify-record.json`：候选评分与排序证据。字段见 `templates/verify-record.schema.json`。
- `progress.md`：人类可读进度与压缩总结链（每步 ≤400 字）。
- 节点契约：`inputs / outputs / acceptance / deterministic / owner`；`deterministic=true` 的节点**禁止调用模型**（对症 E14）。

### 2.5 退出码语义（所有 gate 脚本统一）

| 码 | 含义 |
| --- | --- |
| 0 | PASS（准出） |
| 1 | BLOCK（发现违规，阻断进入下一门） |
| 2 | USAGE_ERROR（参数/文件缺失，视为未验证 = 未完成） |

## 3. 标准定义（逐条可检查的验收标准）

| ID | 验收标准 | 检查方式 |
| --- | --- | --- |
| A1 | 主入口 SKILL.md ≤120 行；单个子技能 ≤160 行 | 行数统计 |
| A2 | 六个 gate 脚本零第三方依赖，`python -X utf8` 下可在 Windows 运行 | 实跑 |
| A3 | 每个 gate 在**正样本 PASS、负样本 BLOCK** 双向验证通过 | pytest |
| A4 | 所有 schema 能被 `gate_state_validate.py` 真实校验（含缺字段、类型错误、枚举越界） | pytest |
| A5 | 套件自身通过 `gate_reality_scan.py`（除白名单：门禁脚本内的模式常量、模板示例、本文件证据表） | 实跑 |
| A6 | 本机安装后用真实 FS 读回校验一致（防写入不落盘故障态） | python 读回比对 |
| A7 | zip 分发件的每个条目与源文件 **sha256 逐项一致**（打包时回读校验）；**安装时不做哈希校验**，只核对清单存在性与非空 | `pack.py`（sha256 回读）+ `scripts/install_check.py`（清单齐全性） |
| A8 | rules/AGENTS.md 合计 ≤100,000 字符（Qoder 官方上限） | 字符统计 |
| A9 | 交付报告含成果清单 / 验证证据 / 已知风险 / 后续建议四要素 | 人工逐项核 |
| A10 | **增强层不接管**：`scripts/` 内所有脚本纯只读（无写/删文件、无建目录、不改 `os.environ`、不杀进程）；检测器自身有负样本 | `pytest`：`test_gate_scripts_are_readonly` + `test_negative_side_effect_detector_catches` |
| A11 | **升级不得静默假成功**：目标文件已存在且内容不同而未加 `-Force/--force` 时，安装必须点名旧文件并 exit=1（仅 `rules_source = AGENTS.md` 的终端属有意跳过并显式告知） | 实跑 `install.ps1` / `install.sh`（无 `--force` 得 exit=1，加 `--force` 得 exit=0） |
| A12 | 定位不得被静默改写：`manifest.json` 必须声明 `kind=augmentation-layer`、`replaces_native_capabilities=false`、`disables_native_capabilities=false`、`prefer_native_when_equivalent=true` 与四级冲突裁决顺序 | `pytest`：`test_manifest_declares_augmentation_layer` |
| A13 | **落点不得有第二份定义**：终端识别标记 / 技能目录 / 规则落点 / 强制力等级只存 `spec/terminals.json`；两个安装器一律向 `acs_doctor` 取值，脚本里不得再写映射表；`detect_order` 末项必须是 `generic` 兜底 | `pytest`：`test_terminals_spec_shape` + `test_installers_ask_doctor_instead_of_hardcoding_targets` + `test_doctor_refuses_spec_without_generic_fallback` |
| A14 | **探针必须双实现且逐字一致**：`acs_doctor.py` 与 `acs_gates.mjs doctor` 对同一 `--mode` 必须输出完全相同的落点。此处不能只比退出码——落点会被当路径直接拷文件，差一个字符就装到两个地方而两边都报成功 | `pytest`：`test_doctor_paths_identical_across_impls`（覆盖全部 mode + auto） |
| A15 | **外部强制点必须真拦住，且不得封堵逃生口**：`hooks/pre-commit` 在缺 `task-state` / state 不合规 / 无任何运行时时均退出非 0；正样本必须放行；`git commit --no-verify` 必须仍然可用（逃生口属 git 原生，增强层无权接管） | `pytest`：`test_hook_*` 五项（建真仓库实跑）+ 本机 Git Bash 七轮实测 + CI `pre-commit-hook` 作业 |
| A16 | **hook 必须是 opt-in 且装了就能跑**：不加 `--with-hook` / `-WithHook` 不得往仓库写 hook；目标不是 git 仓库时显式失败；装完必须有可执行位（没有则 git 静默忽略 = 装了但未生效） | `pytest`：`test_installers_offer_opt_in_hook` + `test_install_sh_makes_hook_executable`；实跑：非仓库目标 exit=1 |
| A17 | **真账可回填且不得冒充**：T2+ 必须显式声明 `cost_metering.source`（拿不到就写 `unavailable`，沉默不算声明）；声称可审计就必须给 `tokens_actual` + 可复查 `evidence_ref`（空话不算）；来源不可审计时禁出现 `tokens_actual`；分项必须对上账；有真账就按分级上限拦 | `pytest`：`LOOP_MUTATIONS` 新增 7 条负样本 + `test_cost_metering_*` 三项（含 `unavailable` 必须放行、T1 不误伤）+ 跟实现退出码比对 |
| A18 | **小分差必须重采样，且重采样必须真的多出样本**：T3 冠亚加权分差 `< spec.self_consistency.trigger_score_gap` 时未登记重采样即 BLOCK；已登记则 `values` 长度必须等于 `repeats + extra_samples`；`gap` 不得手改；T2 不得误伤（分级省 token） | `pytest`：`VERIFY_MUTATIONS` 新增 6 条负样本 + `test_verify_rank_self_consistency_*` 三项 + `test_spec_registers_dimension_recovery` + 跟实现退出码比对 |

## 4. 边界约束

**做**：写标准与可执行门禁脚本；本机安装；打包可分发件；跨终端安装映射（Qoder / Claude Code / Cursor / Windsurf / Codex / 通用 AGENTS.md，定义只存 `spec/terminals.json`）；运行时能力探针（`acs_doctor`，Python 与 Node 双实现）；外部强制点（`hooks/pre-commit` 与 `.github/workflows/acs-gates.yml`，均不依赖模型自觉）。

### 4.1 执行力三层（不分层就会把「写进文档了」当成「强制住了」）

| 层 | 载体 | 约束强度 | 失效方式 |
| --- | --- | --- | --- |
| L1 软约束 | `skills/` `rules/` `AGENTS.md` | Context 层注入，无退出码 | 模型漂移即失效，且无人报错 |
| L2 硬门禁 | `scripts/run_gates.py`、`scripts/node/acs_gates.mjs` | 退出码可证 | 模型不主动调用就等于不存在 |
| L3 外部强制点 | `hooks/pre-commit`（opt-in）、`.github/workflows/acs-gates.yml` | 由 git / CI 触发，**不依赖模型配合** | hook 可被 `--no-verify` 绕过（有意保留）；CI 那一层绕不过 |

CI 的 `installer` 作业另兼一个作用：在 ubuntu-latest 与 macos-latest 上真跑 `install.sh`（语法 / dry-run / 真装 / 非法 `--mode` / 旧版本不得静默跳过），补上本机无原生 Linux、macOS 的证据缺口。

**不做**（明确排除，避免虚假承诺）：
1. **不实现 logit 概率分布评分**。E2 的原始机制需要模型 logits，agent 终端拿不到 → 本套件降维为「显式 1-20 分 rubric + 标准拆分 + 重复评估」，并在技能正文中标注 `[降维实现]`。**不宣称复现论文精度。**
   但降维不等于摆烂：拿不到 logits 时，「同一份 rubric 重复评分的分歧度」是 agent 侧**唯一可得的置信度代理**。故冠亚加权分差落入「可能改判区间」（`< spec.self_consistency.trigger_score_gap`）时，T3 必须先重采样再定案，且 `values` 长度必须真的变成 `repeats + extra_samples` —— 否则 `triggered=true` 只是口号。**只在 T3、只在小分差时触发**，因为重采样花 token；无脑全量采样自己就是成本灾难。
2. **不实现精确 token 计数**。agent 侧无法向平台独立核账 → 默认用可测代理指标（步数 / 请求数 / 文件变更数 / 引用上文字节数 / 耗时），字段名显式为 `*_proxy`。
   但 proxy 不得从「权宜」变成「永久」：`cost_metering` 是真账回填口。T2+ 必须显式声明 `source`（拿不到就写 `unavailable`，**沉默不算声明**）；声称可审计（`api_usage`/`cli_usage`）就必须给数字 + 可复查证据；来源不可审计时**禁止出现** `tokens_actual`（字段名本身就是诚实声明）。上限取自反面基线：同框架对照模型 9 个任务共 956,630 token（均 ~106k/任务），T3 给到 1.2M 已是 11 倍宽容。
3. **不承诺机械级强制**。Qoder 技能与规则属 Context 层注入（软约束），只有 `scripts/` 属 Harness 层（硬约束）。文档中必须区分「软约束」与「硬约束」，禁止把软约束描述为强制。原先靠自觉的 7 条已下沉为 `task-state.json` 结构化字段并由 `gate_checklist.py` 机检（阈值见 `spec/thresholds.json` 的 `checklist.hardened`）；**该层只判「有没有做、是不是空话」，不判思考质量**（质量仍由原生能力负责），未硬化的 `[软]` 条目继续如实标注为软约束。
4. 不修改任何终端本体、不安装第三方依赖、不联网执行、不触碰工作区外的既有文件（家目录下只新增各终端技能目录中属于本套件的子目录，落点见 `spec/terminals.json`）。
5. **不假装识别终端**。`--mode auto` 只按文件系统标记判断；全不命中就显式落到 `generic`，不猜。Cursor 的 `.mdc` 规则需自行补 frontmatter 才会常驻，本套件不代写 —— 代写会让「装完即生效」变成一句无法验证的话。
6. **不静默降级运行时**。有 Python 走 5/5 门；只有 Node 走 4/5 并强制打印 `PARTIAL`；两者皆无则 `soft_only` + exit=1，明说强制力归零。
7. **不封堵主人的逃生口**。pre-commit 默认不装（未经同意往仓库塞 hook 属于接管）；装了也不封 `git commit --no-verify`。逃生必须是主人显式动手，而不是脚本自己放水 —— 两者的差别就是真强制与假强制的差别。
8. 边界外需求处置：记录到 `README.md` 的「后续建议」，不擅自扩张实现。
