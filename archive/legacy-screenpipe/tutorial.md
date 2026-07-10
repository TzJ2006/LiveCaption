# Meeting Helper 部署教程

## 项目简介

Meeting Helper 是一个本地运行的 AI 会议助手，包含两个独立层：

1. **本地字幕覆盖层**（`scripts/live-subtitle.py`）— PyObjC 浮动窗口，支持 6 种 ASR 后端，实时低延迟字幕（~2s），将转录写入文件
2. **Screenpipe 后台服务** — 捕获音频/屏幕，运行 Whisper ASR，存入 SQLite，通过 27 个 MCP 工具暴露给 Claude Code

两层可以同时运行，各有优势：

| 层 | 延迟 | 功能 | 数据输出 |
|----|------|------|---------|
| 字幕覆盖层 | ~2s | 实时字幕、多后端切换 | `transcripts/` 文本文件 |
| Screenpipe MCP | ~30s | 说话人识别、OCR、会议检测 | SQLite → MCP → Claude Code |

## 架构

```
Microphone ──► live-subtitle.py (ASR) ──► <project>/transcripts/YYYY-MM-DD.txt
                  |
System Audio ──┤  (--source both)     ──► <project>/transcripts/YYYY-MM-DD-sys.txt
               |
System Audio ──► Screenpipe (Whisper) ──► SQLite DB ──► MCP tools ──► Claude Code
Screen ──────┘
```

## 环境要求

| 软件 | 版本 | 用途 | 安装方式 |
|------|------|------|---------|
| **Python 3** | >= 3.8 | 字幕覆盖层运行环境 | macOS 自带或 [python.org](https://www.python.org/) |
| **Node.js** | >= 18 | MCP 服务器运行环境 | [nodejs.org](https://nodejs.org/) |
| **Screenpipe** | 最新版 | 音频/屏幕捕获和转录 | [screenpi.pe/download](https://screenpi.pe/download) |
| **Claude Code CLI** | 最新版 | AI 交互界面 | [claude.ai/code](https://claude.ai/code) |
| **Anthropic API Key** | — | Claude 模型调用 | [console.anthropic.com](https://console.anthropic.com/) |

**系统要求（macOS）：**
- macOS 13 (Ventura) 或更高
- 至少 8GB RAM（双模式 `--source both` 推荐 16GB，因为会加载两个 ASR 模型实例）
- 约 5GB 可用磁盘空间（ASR 模型 + Screenpipe 数据）

## 部署步骤

### 1. 克隆项目

```bash
git clone https://github.com/TzJ2006/MeetingHelper.git
cd MeetingHelper
```

### 2. 安装 Screenpipe

**方式 A：桌面应用（推荐）**
```bash
# 从官网下载 .dmg 文件
open https://screenpi.pe/download
```

**方式 B：使用 npx 运行最新版本**
```bash
npx screenpipe@latest
```

**方式 C：Homebrew（已弃用，不推荐）**
```bash
brew install screenpipe  # v0.2.13，有说话人识别模型问题
```

> 如果已安装 Homebrew 版本，建议卸载：
> ```bash
> brew uninstall screenpipe
> rm -rf ~/Library/Caches/screenpipe/models/
> ```

### 3. 运行安装脚本

```bash
bash scripts/install.sh
```

安装脚本自动完成：
- 检查 Node.js 版本
- 检查 Screenpipe 安装
- 安装会议摘要 Pipe 到 `~/.screenpipe/pipes/meeting-summary-zh/`
- 配置 Claude Code 的 MCP 服务器

### 4. 安装字幕覆盖层依赖

```bash
bash scripts/setup-sherpa.sh
```

此脚本安装：
- Python 依赖（sherpa-onnx、sounddevice、numpy、PyObjC）
- 下载 Sherpa-ONNX 中英双语流式模型（~1GB）
- 验证模型加载

### 4b. 预下载所有 ASR 模型（可选）

如果你想一次性下载全部 6 种 ASR 后端的模型：

```bash
bash scripts/download-models.sh
```

也可以只下载指定模型：

```bash
bash scripts/download-models.sh zipformer whisper moonshine
```

### 5. macOS 权限配置

在 **系统设置 > 隐私与安全性** 中授予以下权限：

| 权限 | 用途 | 必需？ |
|------|------|--------|
| 麦克风 | 录制麦克风音频 | 是（字幕层 + Screenpipe 都需要） |
| 屏幕录制 | 屏幕截图和会议检测 | Screenpipe 需要 |
| 辅助功能 | 会议检测（UI 元素扫描） | Screenpipe 需要 |

### 6. 系统音频设置（可选）

如果需要捕获系统音频（Zoom/Teams/Meet 的远端声音），需要安装虚拟音频设备：

```bash
brew install blackhole-2ch
```

然后在 **音频 MIDI 设置** 中创建 **多输出设备**（Multi-Output Device），包含你的扬声器/耳机和 BlackHole 2ch。将系统输出设为该多输出设备。

### 7. 健康检查

```bash
bash scripts/health-check.sh
```

检查 7 类组件：Python3、公共依赖、ASR 后端（5 种）、本地模型文件、字幕窗口状态、今日转录、Claude Code CLI。

> ASR 后端和模型文件显示 WARN 是正常的——只需安装你要使用的后端即可。

## 启动使用

### 启动字幕覆盖层

```bash
# 默认：apple 后端（macOS 内置），仅麦克风
bash scripts/start.sh

# 麦克风 + 系统音频同时录制
bash scripts/start.sh --source both

# 仅系统音频（需 BlackHole）
bash scripts/start.sh --source system

# 指定系统音频设备
bash scripts/start.sh --source both --system-device "BlackHole 2ch"

# 使用其他 ASR 后端
bash scripts/start.sh --model whisper
bash scripts/start.sh --model paraformer

# 自定义字幕 UI
bash scripts/start.sh --opacity 0.5 --height 200
```

**快捷键：** `Cmd+Shift+S` 切换字幕窗口显示/隐藏

**停止字幕：**
```bash
bash scripts/stop.sh
```

### ASR 后端选择指南

| 后端 | 引擎 | 流式 | 语言数 | 延迟 | 适用场景 |
|------|------|------|--------|------|---------|
| `apple`（默认） | macOS Speech | 真流式 | 63 | 低 | 零下载，开箱即用 |
| `zipformer` | Sherpa-ONNX | 真流式 | 中/英 | 最低 | 离线优先，延迟敏感 |
| `paraformer` | Sherpa-ONNX | 真流式 | 中/英 | 低 | 备选双语方案 |
| `qwen3-asr` | torch | 分块 | 52 | 中 | 多语言场景 |
| `whisper` | MLX-Whisper | 分块 | 99 | 中高 | 最广语言支持，~1.6GB |
| `moonshine` | MLX | 事件驱动 | 8 | 低 | Apple Silicon 优化 |
| `voxtral` | MLX | 分块 | 13 | 较高 | 4-bit 量化，~3GB |

> 双模式（`--source both`）会加载两个 ASR 实例，推荐使用 apple、zipformer 或 paraformer 以降低内存占用。

### 启动 Screenpipe

```bash
# 方式 1：npx（推荐）
bash scripts/start-screenpipe-npx.sh
# 或直接：npx screenpipe@latest

# 方式 2：桌面应用
open -a Screenpipe

# 方式 3：Homebrew CLI（已弃用）
screenpipe
```

等待 10-15 秒初始化，验证：
```bash
curl http://localhost:3030/health
```

### 启动 Claude Code

```bash
cd MeetingHelper
claude
```

### 使用示例

**会议进行中：**
```
> 现在正在讨论什么？
> 最后一位发言人关于截止日期说了什么？
> 显示共享屏幕上的内容
```

**会议结束后：**
```
> 生成我上一场会议的摘要
> 会议中产生了哪些行动项？
> 我们关于预算做了什么决定？
```

**会议历史：**
```
> 列出今天的所有会议
> 查找上周我们讨论新功能的时候
```

## 项目结构

```
MeetingHelper/
├── CLAUDE.md                          # Claude 指令文档
├── README.md                          # 项目说明
├── tutorial.md                        # 部署教程（本文件）
├── .claude/
│   ├── settings.json                  # Claude Code 项目设置
│   └── settings.local.json            # 本地设置覆盖
├── config/
│   └── screenpipe-settings.json       # Screenpipe 推荐设置
├── docs/
│   ├── tutorial.md                    # 详细使用教程
│   └── ecl/
│       ├── meeting-helper.yaml        # 项目规划文档
│       ├── model-switcher.yaml        # 多模型切换规划
│       └── system-audio-capture.yaml  # 系统音频捕获规划
├── transcripts/                       # 转录文件输出目录（.gitignore）
├── pipes/
│   └── meeting-summary-zh/
│       └── pipe.md                    # 中英双语会议摘要 Pipe
└── scripts/
    ├── install.sh                     # macOS/Linux 安装脚本
    ├── install.ps1                    # Windows 安装脚本
    ├── setup-sherpa.sh                # Python 依赖 + Sherpa-ONNX 模型安装
    ├── setup-mcp.sh                   # MCP 配置脚本
    ├── live-subtitle.py               # 实时字幕浮动窗口（核心）
    ├── start.sh                       # 启动字幕覆盖层
    ├── stop.sh                        # 停止字幕覆盖层
    ├── start-screenpipe.sh            # 启动 Screenpipe（CLI）
    ├── start-screenpipe-npx.sh        # 启动 Screenpipe（npx）
    ├── download-models.sh             # 预下载所有 ASR 模型
    ├── health-check.sh                # 健康检查（macOS/Linux）
    └── health-check.ps1               # 健康检查（Windows）
```

## 关键数据位置

| 路径 | 内容 |
|------|------|
| `transcripts/YYYY-MM-DD.txt` | 麦克风实时转录（`[HH:MM:SS] text`） |
| `transcripts/YYYY-MM-DD-sys.txt` | 系统音频转录（`--source both` 时） |
| `~/.meeting-helper/qa-log/YYYY-MM-DD.md` | Q&A 会话日志 |
| `~/.meeting-helper/models/` | Sherpa-ONNX 模型文件 |
| `~/.meeting-helper/subtitle.pid` | 字幕进程 PID |
| `~/.meeting-helper/subtitle.log` | 字幕进程日志 |
| `~/.screenpipe/db/` | Screenpipe SQLite 数据库 |
| `~/.screenpipe/pipes/meeting-summary-zh/` | 自定义摘要 Pipe |

## 常见问题

### Q: 看到 "Protobuf parsing failed" 或 "Load model failed" 错误？
A: 这是 Homebrew 版本（v0.2.13）的已知问题。解决方案：
- **推荐**：下载桌面应用：https://screenpi.pe/download
- **或**：使用 `npx screenpipe@latest`
- **临时**：Screenpipe 仍能录制和转录，只是无法区分说话人

### Q: 这个项目需要 Python 吗？
A: **需要**。字幕覆盖层（`live-subtitle.py`）依赖 Python 3 和 PyObjC（macOS）。运行 `bash scripts/setup-sherpa.sh` 安装所有 Python 依赖。如果只使用 Screenpipe MCP 层（不用本地字幕），则不需要 Python。

### Q: 如何选择 ASR 后端？
A: 默认的 `apple` 后端零下载、开箱即用，适合大多数场景。如需更低延迟或纯离线使用，切换到 `zipformer`。如需更多语言支持，使用 `whisper`。通过 `bash scripts/start.sh --model <name>` 切换。

### Q: 双模式（--source both）内存占用大吗？
A: 会加载两个 ASR 模型实例，内存翻倍。推荐使用轻量的 `apple`、`zipformer` 或 `paraformer` 后端。

### Q: 系统音频（Zoom/Teams 远端声音）怎么捕获？
A: 需要安装 BlackHole 虚拟音频设备，并在音频 MIDI 设置中配置多输出设备。详见上方 [系统音频设置](#6-系统音频设置可选)。

### Q: 混合中英文转录准确度如何？
A: Whisper 按 ~30 秒块检测语言，句中切换（如 "Let's discuss the 技术方案"）可能出错。Sherpa-ONNX 的 zipformer/paraformer 对中英混合支持较好。

### Q: 说话人识别准确吗？
A: 本地字幕层不支持说话人识别。Screenpipe 的 MCP 层支持声纹分离，本地麦克风效果最好，远程会议参与者共享音频通道可能无法单独识别。

### Q: 会议数据存储在哪里？
A: 字幕转录存在 `transcripts/`，Screenpipe 数据存在 `~/.screenpipe/`。使用 Claude 问答时，转录片段会发送到 Anthropic API。

### Q: 录制会议是否合法？
A: 取决于你所在的司法管辖区和公司政策。使用前请确认当地录制同意法律、公司政策，并获得会议参与者的同意。

## 故障排除

### 字幕窗口无法启动

```bash
# 检查日志
cat ~/.meeting-helper/subtitle.log

# 常见原因：缺少 Python 依赖
bash scripts/setup-sherpa.sh

# 常见原因：模型文件未下载
ls ~/.meeting-helper/models/
```

### MCP 连接失败

```bash
claude mcp list                                        # 检查 screenpipe 是否在列表中
claude mcp add screenpipe -- npx -y screenpipe-mcp     # 重新添加
```

### Screenpipe 无法启动

```bash
tail -f ~/.screenpipe/screenpipe.log     # 检查日志
pkill screenpipe                         # 重启
npx screenpipe@latest
```

### 权限问题（macOS）

- 系统设置 > 隐私与安全性
- 确保 Screenpipe/Terminal 在屏幕录制、辅助功能、麦克风列表中
- 可能需要移除并重新添加权限

## 验证部署成功

1. **启动字幕覆盖层**
   ```bash
   bash scripts/start.sh
   ```

2. **确认字幕窗口出现**（透明浮动窗口，Cmd+Shift+S 切换）

3. **对着麦克风说几句话**，观察字幕是否实时显示

4. **检查转录文件**
   ```bash
   cat transcripts/$(date +%Y-%m-%d).txt
   ```

5. **启动 Screenpipe**（如需 MCP 功能）
   ```bash
   npx screenpipe@latest
   ```

6. **运行健康检查**
   ```bash
   bash scripts/health-check.sh
   ```

7. **启动 Claude Code 测试**
   ```bash
   claude
   > 列出过去 1 小时的会议
   ```

## 更新日志

- **2026-04-17**: 更新教程，新增字幕覆盖层、多 ASR 后端、系统音频设置、数据位置等内容
- **2026-04-09**: 初始部署教程创建
