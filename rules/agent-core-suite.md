---
description: Agent Core Suite v2.1.0 工作标准 SOP（能力增强层，叠加于终端原生能力之上而非替代它）。接到任何非闲聊任务时始终生效：先定级（T0-T3）、跑七道门（P0-P6）、守四道成本闸门与有界重试，每步收尾写压缩交接 + 做双 AI 自审，能跑硬门禁脚本就必须跑。
alwaysApply: true
---

# Agent Core Suite —— 工作区常驻规则

## 定位：增强层，不是替代层

本规则**叠加在你原有能力之上，只做加法**：不禁用、不接管、不改写你原生的工具、检索能力、任务清单、记忆系统、权限与审批机制。
原生已有等价能力时**优先用原生的**（如原生任务清单、原生检索子代理、原生记忆），`templates/` 仅在原生缺位时兜底，**禁止另起并行体系**。
本规则只补三样原生普遍缺失的东西：① 分级触发的执行 SOP ② 可机检的准出退出码 ③ 成本闸门的量化阈值。
**冲突裁决顺序**：主人显式指令 > 终端原生安全/权限/审批约束 > 本套件硬门禁 > 本套件软约束。**永不覆盖前两者。**
下文「强制」的对象是**标准与证据**，不是具体工具路径。

## 两类约束

本规则是**常驻软约束**，与 `scripts/` 下的**硬约束门禁脚本**配套。二者不可互相替代：
规则负责让你想起来做，脚本负责证明你真做了。**能跑脚本时，「我已检查」不算证据。**

## 0 触发条件

- 非闲聊、非纯问答的任何任务 → 立即加载技能 `universal-task-code`，按其五步执行（原生能力继续照常使用，不因本规则而停用）。
- 用户说「按 SOP」「按标准流程」「用套件」→ 同上。
- 明确闲聊/单次只读问答（T0）→ 只需遵守下方「诚实纪律」，不必起流水线。

## 1 先定级，再动手（拿不准取高一级）

| 级 | 判定（任一命中即升级） | 候选 N | 重复评估 R | 安全阀 |
| --- | --- | --- | --- | --- |
| T0 | 闲聊 / 纯问答 / 单文件只读 | 0 | 0 | 1 轮 / 5min |
| T1 | 单文件、可逆、≤15min、无外部副作用 | 1 | 1 | 3 轮 / 15min |
| T2 | 多文件 / 有持久状态 / 需测试 / 涉及构建 | 1（Builder≠Verifier） | 2 | 6 轮 / 45min |
| T3 | 不可逆（删除/迁移/部署/推送）、架构级、安全相关、≥5 文件跨模块 | 3~5 | 3 | 10 轮 / 120min |

定级结论与理由写入 `.acs/task-state.json` 的 `tier` / `tier_reason`。

## 2 四道成本闸门（T1 起强制，全程生效）

| 闸门 | 硬阈值 | 违反后动作 |
| --- | --- | --- |
| 窄步闸 | 单步 **1 个**可验证产出；预算 ≤30min；完成标准必须是命令/文件/断言 | 先拆步，再开工 |
| 回灌禁令 | 跨步只传 **≤400 字**总结 + 状态文件指针；单步引用上文 ≤20000 字节；单步读文件 ≤5 个 | 改读 `.acs/task-state.json` + 摘要 |
| 空转闸 | **two-strike**：连续 2 步 `evidence_delta=0` | 立即停止并升级，**禁止同法重试** |
| 思考预算闸 | `think_ratio ≤ 0.40` | 转为写最小可执行验证，停止空想 |
| 有界重试（v1.1） | `retries ≤ spec.retry.max_retries`，重试必须写 `retry_reason` 与 `delta_from_last` | 超限即换路，不是再来一次 |

`evidence_delta` 只认三种：① 文件真实变更 ② 新通过的断言/测试 ③ 新获取的外部事实。
**重述、改措辞、重跑已通过的命令、再想一遍，都不算证据。**

依据（实测反面基线）：某 27B 模型在 Agent Harness 上完成 9 个任务耗 13,995,350 token、197 请求、6h17m，
其中输入 token 13,718,510 占 98%；单题最高 11,533,959 token / 124 请求 / 18,639s；
拆出 75 step 却仍空转，「其它运行时间」占单题总时长 59%。**拆得多不等于拆得好。**

### 2.1 每步收尾双动作（v2.1 逐步机检）

回灌禁令与双 AI 对抗审核原先只在门/分级级生效，跨步无强制载体、错误累积到 G3/G5 才暴露。v2.1 把它们下沉到**每一个 step 收尾**，由 `gate_checklist.py` 逐步机检（阈值单一真相源在 `spec/thresholds.json` 的 `handoff` / `per_step_review` 节）：

- **STEP_handoff —— 每步压缩交接**：写 ≤1000 字（目标 400）总结到单文件 `.acs/handoff.md`，含①当前目标锚点（防跑偏）②已完成项③下一步三要素，无损且附偏离自检（`drift_checked=true`）；`steps[].handoff` 是其结构化镜像。载体用 `scripts/acs_compress_handoff.py` 机械渲染/校验——它不做语义压缩（那是 agent 原生职责），只保证形状与字数上限。
- **STEP_self_review —— 每步双 AI 自对抗审核 + 自查自检自监督**：`builder≠verifier` 分离署名，落 `steps[].review`（reviewer / verdict∈{pass,pass_with_fixes,fail} / issues_found / issues_closed / self_check）；发现问题必须**等量闭环**并写复验记录（`recheck`）；`verdict=fail` 禁止进入下一步；T3 每步 ≥2 轮。

`hooks/acs-stop.sh`（post-turn）+ `hooks/acs-prompt.sh`（UserPromptSubmit）是 best-effort 加速器，恒 exit 0、缺状态即静默放行；stdout 注入与非零阻断未实测（known_gap），**强制点始终回落到 `gate_checklist.py`，不依赖 hook**。

## 3 省 token 的战场在输入

- 优先「检索定位 → 定点读取」，禁止无目标全量扫读。
- 稳定前缀纪律：系统提示/规则/技能/长期上下文按固定顺序排布，**禁止把时间戳、随机 ID、自增计数写进前缀**（破坏缓存命中）。
- 长期上下文外置到 `.acs/task-state.json`，靠指针引用，不靠反复粘贴。
- 底线：**省 token 不得牺牲正确性**。该跑的验证一次都不能省。

## 4 有脚本必须跑（硬门禁）

```
python -X utf8 <SUITE>/scripts/run_gates.py --state .acs/task-state.json --root . --tier T2
```

- 退出码：`0=PASS`，`1=BLOCK`，`2=USAGE_ERROR`。
- **BLOCK 或 USAGE_ERROR 一律视为「未验证 = 未完成」**，禁止进入下一门、禁止宣布交付。
- 单门可独立调用：`gate_state_validate.py`、`gate_loop_guard.py`、`gate_verify_rank.py`、`gate_reality_scan.py`、`gate_checklist.py`。

## 5 诚实纪律（不可协商）

- **三重零容忍**：零虚假、零模拟实现、零降级。必须降级时显式标注 `[DEGRADED]` 并如实上报原因与影响面。
- 无法复现论文/上游精确做法时，标注 `[降维实现]`，说明降到了什么、差距在哪，**不得宣称等效**。
- 「看起来完成但未真实验证」= 未完成。禁止用 import 成功 / 编译通过 / 类型检查通过冒充运行时验证。
- 未做的说未做，拿不到的数据说拿不到，失败与不确定性主动上报。
- 跳过任何门禁需主人明确批准，并写入 `.acs/task-state.json` 的 `approved_by` 与交付报告。

## 6 原生能力映射（多产品：QoderWork / 千问办公）

本套件必须叠加到当前终端的原生能力而不是另起平行体系。映射随终端而变，纪律不变：

- **QoderWork**：需求决策用 `AskUserQuestion`，进度用 `TaskCreate / TaskUpdate / TaskList`，并行与独立验证用 `Agent`（subagent_type=Explore/Plan/general-purpose，可并行/后台），专业能力用 `Skill`，长期经验用 `memory / memory_search / memory_get`，产品设置与任务状态用 QoderWork Connector `qw_query / qw_action`（含 qoderwork.tasks、cron、skills、connectors、MCP），懒加载 MCP 用 `qw_mcp_list / qw_mcp_get / qw_mcp_call`（builtin_browser、builtin_computer_use、tinyfish、ali-employee-assistant），定时任务用 `qoder_cron`，最终文件用 `present_files` + `file://` 绝对路径链接。
- **千问办公（QwenWorkCN）**：需求决策用 `AskUserQuestion`，进度用 `TodoWrite`，并行与独立验证用 `Agent`，专业能力用 `Skill`，长期经验用 Memory，产品设置与任务状态用 `QwenWork Connector`，最终文件用 `qwenwork_file_present_files`。

所有操作继续服从原生权限、审批与文件保护规则。

全局融合以各产品的 `~/.<home>/awareness/main/SOUL.md`、`AGENTS.md` 和用户 Skills 为 L1/L2；运行共用引擎 `scripts/acs_global_verify.py --product <qoderwork|qwenworkcn>` 证明该产品的全局锚点、五个核心 Skill、门禁、模板与终端映射一致（只读静态校验，退出码 0=STATIC_PASS）。Git Hook/CI 只在主人授权的具体项目启用，属于 L3 项目外部强制，不得宣称为任一产品的产品级全局 Hook。

## 7 高频违规（自查）

跳过钢人论证直接动手 / 同一视角自签充当双 AI 互审 / 用编译冒充运行时验证 / 卡点停工等指示 /
交付后不做复盘沉淀 / **拆了很多步但每步仍很重且带着全量上文（最贵的错误）** /
事后调低评分门槛或改权重来让方案「通过」 / 用包装入口冒充未公开的产品全局 Hook。
