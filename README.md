# 与熊同行 · BearBless

**One phone. Two users. Zero intentional interruption.**

BearBless 是一个 Android 手机 Agent 原型：用户继续使用物理屏 `Display 0`，Agent 在同一台手机的 scrcpy 虚拟屏中观察、规划、操作和验证。模型只能提出有限动作；运行时重新解析虚拟屏 ID，并以 Guard 阻止 Display 0、剪贴板、凭据和越权操作。

```mermaid
flowchart LR
  U[文字/实时语音] --> C[Mission Contract]
  C --> S[通用 GUI Skill + 任务 Skill]
  S --> M[GUI-Plus 单步决策]
  M --> P[Typed Action + Policy Guard]
  P --> V[Shadow Display]
  V --> O[重新截图 / Step Check]
  O --> M
  O --> F[完成探针 + 独立验证]
  H[用户] --> D0[Display 0]
```

## 关键选择

- **隔离优先：** `scrcpy --new-display` 创建独立工作区；每次输入前重新解析 live display ID，不允许回退到 Display 0。
- **不依赖全局 UIAutomator：** 已在华为 P40 Pro 验证其返回 Display 0，而非虚拟屏；Agent 使用虚拟屏截图、OCR/Set-of-Mark 和 GUI 模型。
- **两层 Skill：** 通用层负责截图、点击、滑动、文字桥、返回、验证和恢复；音乐、闹钟、消息等专项 Skill 只补充语义约束及确定性完成条件。
- **双屏身份门禁：** 每次写操作前同时读取虚拟屏与 Display 0 的解码像素指纹；若厂商系统在重连后把虚拟屏截图别名到主屏，先拦截动作并销毁虚拟屏。主屏 Activity、焦点或 IME 泄漏同样立即失败。
- **键盘隔离：** 已专项验证 scrcpy 的 `local` 与 `hide` IME policy；华为 Android 12 均以“不受信任虚拟屏”拒绝。主路径因此使用深链、URL query、页面已有元素和可选 Accessibility Bridge `ACTION_SET_TEXT`，并以 Display 0 键盘监控熔断。`ACTION_SET_TEXT` 只避免为输入而主动点击，不被当作 IME 隔离机制。
- **失败闭合：** 登录、验证码和人机验证转人工；隔离失败立即停止；模型格式错误与页面导航失败使用独立预算。

## 模型分工

当前演示配置不是只使用一个模型，而是让 GUI 模型和通用视觉模型各做擅长的部分：

| 模型 | 当前职责 |
|---|---|
| **GUI-Plus `gui-plus-2026-02-26`** | 主手机规划器；读取最新 Shadow Display 截图，每次只提出一个点击、滑动、返回或等待动作 |
| **Qwen3-VL-Plus** | 生成 Mission Contract、选择目标应用、执行通用最终验证；当 GUI-Plus 返回非标准动作协议时，对同一截图做一次严格 JSON 兜底 |
| **本地 Qwen3-VL 4B** | 离线开发回退；当前演示路径未启用，CPU/统一内存设备上延迟较高 |

两种云模型都只能返回封闭的 `PhoneDecision`，不能直接调用 ADB。Typed Action、权限策略、Display 0/IME Guard、QQ 单次发送、闹钟幂等检查及 MediaSession 验证均由确定性代码执行。也就是说：**GUI-Plus 决定“下一步怎么操作”，Qwen 负责“理解、选择、验证和协议兜底”，Runtime 负责“能否安全执行以及是否真的完成”。**

当前目标是一个可信演示原型，不宣称生产级通用性。完整的思考过程、技术选型、困难与解决方案见 [设计与复盘](docs/DESIGN_JOURNEY.md)；架构、决策和交付边界分别见 [架构说明](docs/ARCHITECTURE.md)、[决策记录](docs/DECISIONS.md) 和 [交付审计](docs/DELIVERY_AUDIT.md)。全部文档入口见 [`docs/`](docs/README.md)。

当前产品回归范围冻结为四类：QQ、Wolt、音乐和时钟。餐厅地址直接从 Wolt 店铺详情页读取，不依赖地图；地图能力不作为本次交付承诺。新 App 仍可尝试通用 GUI 能力，但不计入稳定演示范围。

## 已验证环境与限制

| 项目 | 已验证配置 |
|---|---|
| 真机 | Huawei P40 Pro（ELS-AN00 / `HWELS`） |
| 系统 | Huawei Android 12，API 31 |
| 虚拟屏 | scrcpy 4.1，1080×2400 / 420 dpi，运行时动态 display ID |
| 主机工具 | ADB 1.0.41（37.0.1），Python 3.11+；当前开发环境 Python 3.12 |
| 应用范围 | Wolt、QQ、网易云音乐、华为系统时钟；应用需已安装，账号状态由用户自行准备 |

已知边界：

- **设备/ROM 相关：** Shadow Display、截图和输入路由并非所有 Android ROM 都一致；更换手机必须先运行 `bearbless doctor`，不能沿用华为上的能力结论。目前未支持 iOS。
- **需要电脑连接：** 当前原型依赖 USB 调试、ADB、scrcpy 和一个独立 Worker；目标是本地单手机演示，不是多设备生产调度系统。
- **输入法限制：** 该华为 ROM 拒绝在“不受信任虚拟屏”上启用 scrcpy `local`/`hide` IME policy。中文输入依赖 display-scoped Accessibility Bridge；没有已验证的无 IME 路径时任务会停止。
- **观察限制：** 全局 UIAutomator 在该设备上返回 Display 0，不能用于 Agent grounding；系统只使用指定虚拟屏截图、OCR/Set-of-Mark 和已验证的 display-scoped Accessibility。
- **任务与账号边界：** 登录、验证码、人机验证转人工；付款、购买、读取凭据和越权消息发送默认禁止。App 更新、弹窗、网络和地区内容可能影响 GUI 路径。
- **模型依赖：** 演示路径依赖阿里云百炼额度与网络。模型不可用、虚拟屏身份不确定或主屏/IME 泄漏时均 fail closed，不降级到 Display 0。
- **能力声明：** 这是可信演示原型，不宣称跨设备、跨 App 的生产级通用性；稳定承诺仅覆盖上表与冻结回归口令。

## 部署

要求：Android 11+、USB 调试、ADB、scrcpy 4.x、Python 3.11+。

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev,dashboard]'
# 若需要浏览器实时中文听写：pip install -e '.[voice]'
cp .env.example .env

python -m bearbless doctor
python -m pytest -q
python -m bearbless worker
# 另开一个终端
streamlit run dashboard/app.py
```

若任务需要中文输入，构建并安装 `android-bridge/`，在手机辅助功能中启用 **BearBless 虚拟屏输入桥**，然后验证：

```bash
adb shell content call \
  --uri content://com.bearbless.bridge.control \
  --method health
```

Dashboard：`http://127.0.0.1:8501`。提交任务、确认边界后即可离开 Dashboard，执行由独立 Worker 进程完成。请求使用跨进程原子领取、lease 心跳与过期回收；Dashboard 重载不会中断正在执行的任务。

## 环境变量

```env
ANDROID_SERIAL=
SCRCPY_PATH=scrcpy
ADB_PATH=adb
SHADOW_WIDTH=1080
SHADOW_HEIGHT=2400
SHADOW_DPI=420
COMMAND_TIMEOUT_SECONDS=20
DISPLAY_START_TIMEOUT_SECONDS=20
DEVICE_RECONNECT_ATTEMPTS=5
DEVICE_RECONNECT_DELAY_SECONDS=1.0
ACCESSIBILITY_BRIDGE_AUTHORITY=com.bearbless.bridge.control

PHONE_MODEL_PROVIDER=gui_plus
PHONE_MODEL_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
PHONE_MODEL_NAME=gui-plus-2026-02-26
PHONE_VERIFIER_MODEL_NAME=qwen3-vl-plus
PHONE_MODEL_API_KEY=replace_me
```

API Key 只放 `.env`，该文件已被 `.gitignore` 排除。模型替换不会绕过 Action Schema、Policy、Conflict Guard 或最终验证。

## 冻结回归口令

```text
打开网易云播放：若把你
打开Wolt找一家高评分汉堡店，读取店名评分和地址，然后去QQ发给红枣桂花熊，但只保留草稿不要发送
设置今天18:37的闹钟
```

QQ 当前部署对外发送硬限为唯一联系人“红枣桂花熊”；收件人缺失或不同时在入队前和运行时都会失败关闭。Wolt→QQ 使用两阶段编排：先验证店名、评分、地址，再用该结构化结果构建原文消息，不会把 OCR 未验证文本直接外发。真实发送仍必须在任务中提供完整正文并通过敏感动作门禁。
