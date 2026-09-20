# BearBless 交付验收审计

审计日期：2026-09-20。结论按真实代码、测试与 `artifacts/` 运行记录给出，不把 fixture 当作真机成功。

本次功能范围冻结为 QQ、Wolt、音乐和时钟。餐厅地址直接从 Wolt 店铺详情读取，地图不属于本次承诺范围。除此之外的新 App 不进入交付阻塞清单。

| 题目要求 | 状态 | 当前证据 | 交付前动作 |
|---|---|---|---|
| 有人使用手机时完成真实任务 | 真机功能已证明，待录制 | 网易云、Wolt、时钟及两次 Wolt→QQ 草稿链路均有完成记录 | 录制时让用户持续操作 Display 0 |
| 不抢屏幕、焦点和键盘 | 自动指标通过 | 最新四功能回归的 Display 0 动作、IME、包泄漏和隔离违规均为 0 | 最终录制仍需镜头同时覆盖实体手机与 Shadow Display |
| 方法自选及技术判断 | 满足 | scrcpy 虚拟屏 + display-scoped input + fail-closed runtime | 答辩说明 UIAutomator/IME policy 为什么在华为不可用 |
| 本地真机可演示 | 满足 | Huawei P40 Pro / Android 12 当前真机回归证据 | 演示前跑 `doctor` 并保存当天报告 |
| GitHub + 清晰 commit | 部分满足 | 已初始化本地 `main` 仓库；尚无远端 | 分阶段提交、配置 GitHub 远端并推送 |
| README 一页以内 | 已整改 | README 已压缩为架构、部署、环境变量和诚实边界 | GitHub 渲染复核 |
| 1–2 分钟 Demo | 不满足 | 尚无最终成片 | 按 `DEMO_SCRIPT.md` 连续验收后录制 |
| 复杂任务能力 | 冻结范围满足 | Wolt→QQ 两阶段链路连续三次通过；四个冻结功能均有独立完成证据 | 答辩时明确不把未知 App 泛化能力冒充为生产级承诺 |

## 运行证据快照

- 自动化测试：260 项通过（2026-09-20）；最终提交仍以 `python -m pytest -q` 的最新输出为准。
- 网易云《若把你》：`agent-b9f325b52e`、`agent-003d7887d9` 和 `agent-8822a91ca4` 连续三次完成，MediaSession 均为 `state=3` 且标题匹配。
- Wolt：`agent-41d77de193` 与 `agent-799ae51b67`，两次均验证 Shishbar Restaurant / 9.4 / 完整地址。
- 时钟：`agent-cdcd55ab89` 创建并验证 18:37；`agent-63f59df58e` 和 `agent-3cfb49511f` 随后两次从 Android 系统闹钟状态幂等确认已存在，均为 0 手机动作、0 重复创建。
- Wolt→QQ 草稿：`request-9c996b50b172`、`request-b8e69dfa6661` 与 `request-7acb640577eb`，连续三次均仅选中“红枣桂花熊”，原文包含店名/评分/地址且 `sent=false`。第三次的 Wolt/QQ 子运行分别为 `agent-abfadf9945` 和 `agent-044ecdd4ee`。
- 用户最终选择“只编辑、不发送”。`request-874268a76694` / `agent-7c8d648584` 已用最新 Wolt 结果再次完成 QQ 草稿，证据为 `recipient=红枣桂花熊; sent=false; surface=share_confirmation`。真实外发不列为当前演示承诺。
- 上述最新运行的 `agent_actions_targeting_primary_display`、`ime_policy_violations`、`isolation_violations` 与 `primary_display_agent_package_leaks` 均为 0。
- 时钟幂等快速路径现在与普通 Agent 运行一样原子落盘 `state.json`、`result.json` 和 `metrics.json`，Dashboard 不再只显示一个缺少原因的完成计数。

## 演示冻结范围

**主 Demo 候选：网易云通过精确歌曲深链播放一首已登录账号可播放的固定歌曲。** 链路是“解析精确标题 → `orpheus://song/<id>` → 隔离屏打开歌曲页 → 点击页面已有播放按钮 → MediaSession 验证标题与播放态”。全程禁止打开搜索页、点击输入框或调用 IME；深链解析失败即在创建虚拟屏前停止。只有连续三次无主屏 IME/Activity 泄漏后才能冻结。

**保底 Demo：Quark 打开并验证官方 DSB 页面。** 它已有较稳定隔离证据，但任务价值和 Agent 泛化展示较弱。

建议录屏主任务为 Wolt→QQ 草稿：它同时展示跨 App 数据传递、收件人硬限、虚拟屏切换和零打扰，又不需要在录制中产生真实外发副作用。网易云《若把你》作为短链路保底。

## IME-free 链路核对

| 候选任务 | 文本如何进入目标 | 是否触碰输入框 | 完成证据 | 结论 |
|---|---|---:|---|---|
| 网易云播放固定歌曲 | 服务端精确标题查询、官方歌曲深链、点击已有播放按钮 | 否 | Android MediaSession 标题匹配且 `state=3` | 主 Demo；需连续 3 次通过 |
| YouTube 搜索固定主题 | URL `search_query` 参数 | 否 | 隔离屏出现查询词和视频结果 | 次选；只展示搜索，不播放 |
| DSB 哥本哈根—汉堡页面 | 固定官方页面 URL | 否 | 隔离屏页面标题/路线内容 | 保底 Demo |
| 美团/Wolt 搜附近商家 | 依赖位置、页面状态或搜索控件 | 可能 | 页面结果与位置证据 | 不用于主 Demo |
| QQ 发消息 | 联系人选择与消息输入 | 是，且是敏感动作 | 发送后的会话证据 | 不用于主 Demo |
| 时钟设闹钟 | 无文本输入，但依赖滚轮与确认控件 | 否 | 系统闹钟记录 | 可做扩展，不用于主 Demo |

## 放行门槛

1. `doctor` 全部关键项 PASS，并保存报告。
2. 主 Demo 连续 3 次成功，期间用户持续在 Display 0 打字或切换 App。
3. 每次 `agent_actions_targeting_primary_display=0`、`primary_display_agent_package_leaks=0`、`ime_policy_violations=0`。
4. 完成后 Agent 立即停止，不回退、不继续搜索。
5. 失败时 Dashboard 在 5 秒内显示终态和原因，不永久停留在 QUEUED/PENDING。
6. 仓库没有 `.env`、API Key、运行截图中的账号/验证码等敏感信息。

## 2026-09-18 真机回归增量

- Doctor 全部关键项 PASS：Huawei ELS-AN00 / Android 12，虚拟屏创建、display ID 新鲜度、独立截图和剪贴板隔离均通过。
- Wolt 汉堡任务在补充 Restaurants 稀疏加载页等待后完成：6 步、0 replan，提取店名、评分、配送时间和地址；Display 0、IME、包泄漏和隔离违规均为 0。
- 网易云《银河赴约》完成：2 步、0 replan，MediaSession `state=3` 且标题匹配；四项隔离指标均为 0。
- 华为闹钟暴露出厂商特有的幂等陷阱：对已存在的 18:00 闹钟重复发送 `SET_ALARM` 会进入编辑/切换路径，连续重放最终可能取消该闹钟。现已改为三态协议：系统记录已存在则直接成功；不存在则最多创建一次；系统状态不确定则失败停止，禁止重放副作用。系统状态验证已有单测覆盖；重连后的真机显示捕获仍出现把 Display 0 画面误当虚拟屏的情况，因此时钟暂不列为稳定 Demo。
- 虚拟屏增加动作前双截图身份门禁：解码后的虚拟屏与 Display 0 像素指纹相同即视为厂商显示别名故障，任何点击、输入或设置动作都不会下发；同时保留主屏 Activity 与 IME 的动作前后监控。真机 Doctor 已验证 Display 212 与 Display 0 像素面不同、旧 Display 212 在重建为 213 后不可复用。
- 时钟首轮遇到虚拟屏进程消失，次轮暴露 scrcpy 临时端口竞争，已分别增加失败闭合证据与显示枚举短退避，并将华为时钟改为 staged shadow start。最终复验前手机从 ADB 完全断开，任务在任何手机操作前以 `DEVICE_LOST` 停止；重新接线后仍需完成真机复验。
- QQ 没有在本轮重复发送消息。已有发送成功证据保留，但最终发送属于对第三方的敏感动作，必须使用明确正文并由主屏通知确认后单次执行。
