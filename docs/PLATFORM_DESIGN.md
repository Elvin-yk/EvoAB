# EvoAB 平台设计

EvoAB（Evolving Agents with Bodies）把已有的 Agent、Agent + Policy 机器人操作方法统一实现，在同一套环境和评测协议下比较。定位类似 StarVLA 和 RLinf：价值在于集成、对齐和可比。

状态：讨论稿。

## 0. 当前能力

现有代码已具备：
- 分层清楚：Agent、调度、工具、服务、后端五层。
- 已有多种交互模式：Direct、Hybrid（Agent + VLA，带审核）、Clean、Code-as-Policy。
- 环境覆盖广：RoboDojo、LIBERO-Plus/PRO、RoboTwin 2.0、RMBench、CALVIN、RoboLab、DexJoCo、BEHAVIOR 等；真机有 ARX ONE、Piper-X。
- 模型网关可以把 Codex 的 Responses 请求转成 Chat Completions。
- 默认不给深度图和判分反馈，符合 RoboDojo Agent Track 规则。
- 自带只读回放监控，可以浏览运行记录。

## 1. 分层

```
① methods/<name>/          方法配置与复现卡片：一个复现工作 = 一份配置
② 交互模式 / 控制回路       evoab/execution、evoab/context：Direct | Hybrid | Clean | CodeAsPolicy，加上我们新增的 Agentic 与 Orchestrator 变体
③ 模型运行时               evoab/agents（Codex、Claude Code 等外部 Agent），evoab/provider（网关与协议转换）；新增 nanobot 驱动的进程内 Agent 后端
④ 工具与服务               evoab/tools（Gripper、Scene、Skill、Episode、Hybrid），evoab/services（环境、actor、感知服务）
⑤ evoab/integrations/<env>/ 环境只描述事实：相机、坐标系、夹爪几何、基础执行器
⑥ 评测与展示               evoab/artifacts、evoab/ui/web（回放监控）；后续扩展为 EvoAB Studio
```

## 2. 关键约定

- **方法与机器人解耦。** prompt 由两部分组成：方法模板（`methods/<name>/prompts/`），加上环境集成提供的事实片段（`evoab/integrations/<env>/` 的 `EnvironmentDescription` 与 prompt builder，见第 4 节）。复现 N 个方法、支持 M 个环境，需要写 N + M 份，而不是 N × M 份。
- **动作接口统一。** 环境只实现三种基础执行器：`eef_abs`、`joint_abs`、`primitives`。delta / relative、B-spline、keypoints 等其他格式在 ④ 层转换成基础执行器。
- **协议是开关，不是分支。**
  - `--protocol paper`：允许使用论文原版用到的任务信息。
  - `--protocol official`：只注入官方任务描述，禁用 task-specific 记忆、按任务名选程序和评测反馈。对应 RoboDojo Agent Track 2026-11-14 规则。
- **复现卡片。** 每个方法一张 `methods/<name>/CARD.md`，记录：
  - 论文数字和我们的数字，以及两者的差距原因；
  - 每回合的 token、美元、延迟；
  - 原始代码链接，以及与原版的差异。
- **原始实现作为对照。** 拿得到代码的方法放进 `third_party/<name>`（git submodule），用来逐项对齐。

## 3. LLM 运行时（`evoab/llm/`）

### 3.1 对外接口：只认 EvoAB 自己的名字

- `EvoLLM`：一次模型调用。输入消息（含图片）、工具 schema、`tool_choice` 和 reasoning effort，输出文本、工具调用和用量。负责重试、按 retry-after 退避、全局限速和熔断。
- `EvoAgentLoop`：持久会话里的"模型 → 工具 → 结果"循环。管理历史保留策略、上下文压缩和每轮工具数上限。
- `EvoTool`：工具协议，包括名字、说明、JSON Schema 和执行函数。返回值是"文本 + 图片"。
- `ToolkitAdapter`：把机器人 `Toolkit` 的工具自动转成 `EvoTool`。图片转成 `image_url` content block。

其他模块只依赖这四个名字。

### 3.2 当前实现：nanobot（MIT）作为内部依赖

- 第三方运行时只出现在 `evoab/llm/_backends/` 里，替换成自研实现时只改这一处。
- 依赖固定版本，用到的部分：
  - providers：OpenAI 兼容、Anthropic、Azure、Bedrock、OAuth，以及回退和重试；
  - 面向模型的工具循环；
  - 工具注册表。
- 不用它的聊天渠道、WebUI、定时任务和自动更新。
- 已知约束：工具循环要求提供历史压缩回调。上游迭代快，需要给适配层写回归测试。
- 底座已有的 `codex`、`claude_code`（以及实验性的 `gemini_cli`、`opencode`）外部 Agent 后端保留，给需要原生 CLI Agent 的方法用。

依赖隔离：Agent 进程只装轻量依赖。仿真器和 VLA 继续在各自的环境里通过 RPC 运行。

### 3.3 当前 LLM 来源（2026-11-20 实测）

- 网关：`http://127.0.0.1:38579/v1`，OpenAI 兼容。key 在 `~/.config/eah/api.env`（`EAH_API_KEY`），不进仓库。
- 默认模型：`gpt-6-astra`；可选 `gpt-6.2-astra`、`gpt-6-sol`、`gpt-6-luna`。
- 性能：
  - 文本调用约 3.5 s。
  - 2 张图 + 强制工具调用约 7 s。
  - `reasoning_effort` 生效，xhigh 会产生 reasoning token。
- 稳定性限制：
  - 代理背后只有一个账号。上游过载后整个账号冷却约 150–330 s，期间所有模型都返回 `auth_unavailable`，所以必须有全局限速和熔断。
  - **不要走 `/v1/responses`**：每次都过载，还会触发冷却。统一走 Chat Completions，持久会话由 `EvoAgentLoop` 自己维护。
- 远程 GPU 机通过 `ssh -R 38579:127.0.0.1:38579` 访问网关。长时间实验需要能自动重连的隧道。
- 验证脚本：
  - `scripts/spikes/astra_tool_call_probe.py`：多图 + 强制工具调用。
  - `scripts/spikes/llm_runtime_spike.py`：工具循环 + 返回图片的工具。

## 4. 接口设计规则（参考 Astranomy，详见 `docs/research/astranomy_interface.md`）

Astranomy 的主要结论：性能的主要来源是接口文本和上下文，而不是工具本身。以下规则写进 ④ 和 ⑤ 的契约：

1. **环境提供结构化"事实片段"，方法模板负责组合。**每个集成的 `EnvironmentDescription` 与 prompt builder 提供以下几块，Direct 和 Agentic 共用：

   | 片段 | 内容 |
   |---|---|
   | `identity` | 机器人与夹爪型号、末端执行器名字 |
   | `frames` | 根坐标系轴向；动作位姿对应哪个 link（法兰还是指尖）；四元数顺序；欧拉角约定 |
   | `timing` | 控制频率；推理时仿真是否暂停；每步位移和转角上限；IK 限幅 |
   | `gripper_geometry` | 法兰到指垫的偏移随开合度变化的表；换算公式和示例 |
   | `camera_calibration` | 每个相机的父坐标系、外参、归一化内参、投影公式；腕部相机的倾角 |
   | `caveats` | 例如"夹爪指令或 IK 成功都不等于抓到了""像素只给出一条射线" |

2. **观测是一份 JSON 文本加上多视角图片。** 观测 JSON 包含：
   - `embodiment`、`orientation_convention`；
   - 每个 effector 的位置、姿态和夹爪状态；
   - 可选的 `geometry_state`（`ee_pos`、`ee_quat`、`gripper_pos`）和关节位置；
   - 剩余步数预算。

   历史观测要带上它自己时刻的位姿，不能用当前位姿。

3. **动作工具的 schema 字段命名要自解释，并且必须带 `reason` 字段。**
   - Agentic：`execute_eef_target({target: {position, quaternion_wxyz, gripper_closed}, steps: 1..5, reason})`。
   - Direct：`emit_eef_actions({actions: [[x,y,z,qw,qx,qy,qz,g], ...] (3..15)})`。

   宿主负责校验（比如目标离当前位姿不超过 0.05 m / 0.35 rad）后再执行。终止只由环境判定，不提供"done"动作。

4. **记忆和分析工具不能动机器人。** 每一轮必须以且只能以一个动作工具结束。
5. **上下文策略可以配置**：
   - Direct：t−14、t−7、t 三帧，无会话。
   - Agentic：完整文本和图片历史，这是 Astranomy 消融里收益最大的一项。
6. **可选项做成开关，不写死。** 规划指令、运动误差反馈、可选分析工具（shell、裁图、计算）、视频示范（ICL）都做成开关。Astranomy 里这些项的收益不显著，但不同环境可能不一样。

## 5. 计算资源

- featurize-5090：
  - 硬件：2 × RTX 5090（32GB）、驱动 580、1.4T 磁盘。
  - 软件：Ubuntu 22.04、Python 3.11、conda、docker。
  - pip 会段错误，改用 uv。
- 服务器上没有 nvcc。RoboDojo 需要 cuRobo，到时要装 CUDA 12.8 toolkit。
- 暂缓：
  - VPP2：依赖 Wan2.1-14B，单卡放不下；
  - 在 RoboLab-120 上复现 Astranomy 的原始数字。

## 6. 里程碑

1. **M0 基础**（已完成）
   - 统一命名为 EvoAB。
2. **M1 方法层**
   - nanobot 驱动的进程内 Agent 后端（`--agent evoab`），不依赖 Codex 也能对接各家模型。
   - 方法配置加载器与复现卡片。
   - 补 Astranomy Direct（无状态、直接出动作块）。
   - 复现 Harness VLA 方法（任务记忆 + `vla_act`），Pi0.5 LIBERO 等作为 actor 后端。
3. **M2 前端（EvoAB Studio）**
   - 以 `evoab/ui/web` 回放监控为基础，按 nanobot 风格重做。
   - 回答 8 个维度：仿真/真机、方法、本体、任务、benchmark、第几回合、可视化、用量。
4. **M3 Benchmark 对比**
   - LIBERO-PRO：Harness VLA 与 Direct/Hybrid 对比。
   - RoboDojo（Isaac Sim 5.1）`official` 协议，对齐 RoboProbe 基线 27.6 与本底座论文的 31.39。
   - RoboLab：复现 Astranomy 三种范式。
5. **M4 榜单方法**：Q0-Preview、PhysicalRSI、PyRUA-Lean、Agent-as-Policy；XPolicyLab 适配器与统一报告页。
