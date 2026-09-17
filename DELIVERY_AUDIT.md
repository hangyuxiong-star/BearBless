# BearBless 交付验收审计

审计日期：2026-09-17。结论按真实代码、测试与 `artifacts/` 运行记录给出，不把 fixture 当作真机成功。

| 题目要求 | 状态 | 当前证据 | 交付前动作 |
|---|---|---|---|
| 有人使用手机时完成真实任务 | 部分满足 | DSB、蓝牙查询、一次网易云播放有完成记录 | 录制前连续成功 3 次同一主 Demo |
| 不抢屏幕、焦点和键盘 | 部分满足 | 虚拟屏、live-ID Guard、零回退；曾出现主屏 IME/Activity 泄漏并已记录 | 必须重跑双人并发验收，镜头同时覆盖手机与 Shadow Display |
| 方法自选及技术判断 | 满足 | scrcpy 虚拟屏 + display-scoped input + fail-closed runtime | 答辩说明 UIAutomator/IME policy 为什么在华为不可用 |
| 本地真机可演示 | 历史满足，当前未连接 | Huawei P40 Pro 历史 Doctor/Shadow Test 证据 | 当前 Doctor 显示无授权设备；演示前重新连接并保存新报告 |
| GitHub + 清晰 commit | 部分满足 | 已初始化本地 `main` 仓库；尚无远端 | 分阶段提交、配置 GitHub 远端并推送 |
| README 一页以内 | 已整改 | README 已压缩为架构、部署、环境变量和诚实边界 | GitHub 渲染复核 |
| 1–2 分钟 Demo | 不满足 | 尚无最终成片 | 按 `DEMO_SCRIPT.md` 连续验收后录制 |
| 复杂任务能力 | 实验中 | Skill、Step Check、重规划、完成探针已存在；异构成功率不足 | 不作为硬性承诺，只展示一次成功任务和失败恢复设计 |

## 运行证据快照

- 现有状态文件：48。
- `COMPLETED`：12，其中包含 fixture；不能据此宣称 25% 真机成功率。
- `FAILED`：32；主要失败包括 grounding、模型协议、无效果循环和完成判断。
- 自动化测试：130 项通过。
- 当前硬件预检：ADB 未发现授权设备；因此本轮没有伪造“刚刚真机通过”的结论。

## 演示冻结范围

**主 Demo 候选：网易云播放一首已登录账号可播放的歌曲。** 它能展示打开 App、搜索、输入、选择、播放和 MediaSession 确定性完成；但只有在连续三次无主屏 IME/Activity 泄漏后才能冻结。

**保底 Demo：Quark 打开并验证官方 DSB 页面。** 它已有较稳定隔离证据，但任务价值和 Agent 泛化展示较弱。

闹钟、美团和 QQ 暂不作为录屏主任务：它们适合答辩展示失败分析与 Skill 架构，但当前成功率不足。

## 放行门槛

1. `doctor` 全部关键项 PASS，并保存报告。
2. 主 Demo 连续 3 次成功，期间用户持续在 Display 0 打字或切换 App。
3. 每次 `agent_actions_targeting_primary_display=0`、`primary_display_agent_package_leaks=0`、`ime_policy_violations=0`。
4. 完成后 Agent 立即停止，不回退、不继续搜索。
5. 失败时 Dashboard 在 5 秒内显示终态和原因，不永久停留在 QUEUED/PENDING。
6. 仓库没有 `.env`、API Key、运行截图中的账号/验证码等敏感信息。
