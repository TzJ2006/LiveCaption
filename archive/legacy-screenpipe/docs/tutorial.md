# Meeting Helper 使用教程

本教程将带你从零开始安装、配置和使用 Meeting Helper——一个基于 Screenpipe + Claude Code CLI 的 AI 会议助手。

---

## 目录

1. [项目简介](#1-项目简介)
2. [架构与工作原理](#2-架构与工作原理)
3. [环境准备](#3-环境准备)
4. [安装步骤](#4-安装步骤)
5. [验证安装](#5-验证安装)
6. [第一次使用：会议前准备](#6-第一次使用会议前准备)
7. [会议进行中：实时问答](#7-会议进行中实时问答)
8. [会议结束后：生成摘要](#8-会议结束后生成摘要)
9. [说话人管理](#9-说话人管理)
10. [会议历史搜索](#10-会议历史搜索)
11. [中英混合语言处理](#11-中英混合语言处理)
12. [问答日志](#12-问答日志)
13. [自定义摘要 Pipe](#13-自定义摘要-pipe)
14. [故障排查](#14-故障排查)
15. [进阶技巧](#15-进阶技巧)

---

## 1. 项目简介

Meeting Helper 是一个 **本地运行** 的 AI 会议助手。它不是一个独立的应用程序，而是将两个强大的工具连接在一起：

- **Screenpipe**：在后台持续录制麦克风音频、系统音频（Zoom/Teams/Meet 等）和屏幕内容，并通过 Whisper 实时生成转录文本
- **Claude Code CLI**：Anthropic 的命令行 AI 助手，通过 MCP（Model Context Protocol）连接到 Screenpipe 的数据

你可以在终端中用自然语言向 Claude 提问，Claude 会搜索 Screenpipe 捕获的会议数据来回答你。

### 能做什么？

| 场景 | 示例 |
|------|------|
| 会议进行中 | "刚才讨论了什么？" "Alice 说了什么关于 deadline 的事？" |
| 会议结束后 | "生成会议摘要" "列出所有 action items" |
| 会议历史 | "上周我们什么时候讨论了新功能？" "今天有几个会？" |
| 屏幕内容 | "刚才共享屏幕上显示了什么？" |

---

## 2. 架构与工作原理

```
┌─────────────────────────────────────────────────────────────────┐
│                        你的电脑（本地运行）                        │
│                                                                 │
│  ┌──────────────────────┐         ┌──────────────────────────┐  │
│  │     Screenpipe       │         │    Claude Code CLI       │  │
│  │  (后台静默运行)       │         │   (你的终端窗口)          │  │
│  │                      │         │                          │  │
│  │  🎤 麦克风音频       │         │  你: "刚才讨论了什么？"   │  │
│  │  🔊 系统音频(Zoom等) │◄──MCP──►│  你: "生成会议摘要"      │  │
│  │  🖥️ 屏幕截图+OCR    │         │  你: "Alice说了什么？"   │  │
│  │  📝 Whisper 转录     │         │                          │  │
│  │                      │         │  Claude 搜索数据并回答    │  │
│  └──────────┬───────────┘         └──────────────────────────┘  │
│             │                                                    │
│             ▼                                                    │
│  ~/.screenpipe/db/ (SQLite 本地数据库)                            │
│  - 音频转录文本                                                   │
│  - OCR 识别的屏幕文字                                             │
│  - 说话人声纹嵌入                                                 │
│  - 会议检测记录                                                   │
└─────────────────────────────────────────────────────────────────┘
```

**数据流程：**
1. Screenpipe 后台捕获音频和屏幕 → 通过 VAD（语音活动检测）和 Whisper 生成转录 → 存入本地 SQLite 数据库
2. 你在 Claude Code CLI 中提问 → Claude 通过 MCP 调用 Screenpipe 的搜索 API → 从数据库检索相关内容 → 组织回答
3. 所有数据都在本地，只有 Claude 处理查询时会将转录片段发送到 Anthropic API

---

## 3. 环境准备

### 必需软件

| 软件 | 版本要求 | 用途 | 安装方式 |
|------|---------|------|---------|
| **Node.js** | >= 18 | 运行 MCP 服务器 | [nodejs.org](https://nodejs.org/) |
| **Screenpipe** | 最新版 | 音频/屏幕捕获和转录 | [screenpi.pe/download](https://screenpi.pe/download) |
| **Claude Code CLI** | 最新版 | AI 交互后端 | [claude.ai/code](https://claude.ai/code) |
| **Anthropic API Key** | — | Claude 模型调用 | [console.anthropic.com](https://console.anthropic.com/) |

### 系统要求

**macOS：**
- macOS 13 (Ventura) 或更高版本（用于 ScreenCaptureKit 系统音频捕获）
- 至少 8GB RAM（Whisper 模型需要 ~3GB 加载）
- 约 5GB 可用磁盘空间（Whisper 模型 + Screenpipe 数据）

**Windows：**
- Windows 10/11
- 至少 8GB RAM
- 约 5GB 可用磁盘空间

### 检查 Node.js 版本

```bash
node -v
# 应该输出 v18.x.x 或更高
```

如果版本不够，从 [nodejs.org](https://nodejs.org/) 下载 LTS 版本。

---

## 4. 安装步骤

### 方式一：使用安装脚本（推荐）

#### macOS / Linux

```bash
# 克隆项目
git clone https://github.com/TzJ2006/MeetingHelper.git
cd MeetingHelper

# 运行安装脚本
bash scripts/install.sh
```

脚本会自动完成以下操作：
1. 检查 Node.js 版本
2. 检查 Screenpipe 是否已安装
3. 提醒 macOS 权限设置
4. 安装自定义会议摘要 Pipe
5. 配置 Claude Code 的 MCP 连接

#### Windows (PowerShell)

```powershell
# 克隆项目
git clone https://github.com/TzJ2006/MeetingHelper.git
cd MeetingHelper

# 运行安装脚本
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

### 方式二：手动安装

如果你希望了解每一步具体做了什么，可以手动执行：

#### 第 1 步：安装 Screenpipe

**macOS：**
```bash
# 方式 A: Homebrew
brew install screenpipe

# 方式 B: 下载桌面应用
# 访问 https://screenpi.pe/download 下载 .dmg
```

**Windows：**
```powershell
# 方式 A: winget
winget install screenpipe

# 方式 B: 下载桌面应用
# 访问 https://screenpi.pe/download 下载安装包
```

#### 第 2 步：配置系统权限

**macOS（必须，否则无法正常工作）：**

打开 **系统设置 > 隐私与安全性**，为 Screenpipe 授予以下权限：

| 权限 | 路径 | 用途 |
|------|------|------|
| 屏幕录制 | 隐私与安全性 > 屏幕录制 | 屏幕截图和会议检测 |
| 辅助功能 | 隐私与安全性 > 辅助功能 | 会议检测（UI 元素扫描） |
| 麦克风 | 隐私与安全性 > 麦克风 | 录制麦克风音频 |

> 提示：授权后可能需要重启 Screenpipe 才能生效。

**Windows：**

在 **设置 > 隐私 > 麦克风** 中允许 Screenpipe 访问麦克风。系统音频通过 WASAPI Loopback 捕获，无需额外配置。

#### 第 3 步：启动 Screenpipe 并等待初始化

```bash
# CLI 方式
screenpipe record

# 或者直接打开 Screenpipe 桌面应用
```

首次启动时，Screenpipe 会下载 Whisper large-v3-turbo 模型（约 3GB），这可能需要几分钟。等待下载完成后，你应该能看到音频捕获和转录开始运行。

验证 Screenpipe 正在运行：

```bash
curl http://localhost:3030/health
# 应该返回 JSON 健康状态
```

#### 第 4 步：安装自定义会议摘要 Pipe

```bash
# 创建 Pipe 目录
mkdir -p ~/.screenpipe/pipes/meeting-summary-zh

# 复制 Pipe 文件
cp pipes/meeting-summary-zh/pipe.md ~/.screenpipe/pipes/meeting-summary-zh/
```

这个 Pipe 是一个 Markdown 文件，告诉 Screenpipe 的 AI 代理如何生成结构化的中英双语会议摘要。

#### 第 5 步：配置 MCP 连接

MCP（Model Context Protocol）是让 Claude Code 访问 Screenpipe 数据的桥梁。

```bash
# 将 screenpipe MCP 服务器添加到 Claude Code
claude mcp add screenpipe -- npx -y screenpipe-mcp
```

验证 MCP 是否添加成功：

```bash
claude mcp list
# 应该看到 "screenpipe" 在列表中
```

#### 第 6 步：设置 Anthropic API Key

如果你还没有设置：

```bash
# 设置环境变量（添加到你的 shell 配置文件中）
export ANTHROPIC_API_KEY="sk-ant-..."
```

Windows PowerShell：
```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
# 永久设置:
[Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-ant-...", "User")
```

---

## 5. 验证安装

使用内置的健康检查脚本验证所有组件：

```bash
bash scripts/health-check.sh
```

脚本会检查 8 个关键组件：

```
Meeting Helper — Health Check
==============================

1. Screenpipe API (localhost:3030)
  [PASS] Screenpipe is running

2. Meeting Detection
  [PASS] Meeting API endpoint accessible

3. Audio Transcription
  [PASS] Audio transcriptions are being generated

4. Screen Capture
  [PASS] Screen capture with OCR is working

5. Node.js
  [PASS] Node.js v22.0.0

6. Claude Code CLI
  [PASS] Claude Code CLI found

7. MCP Configuration
  [PASS] screenpipe MCP server configured in Claude Code

8. Meeting Summary Pipe
  [PASS] Custom pipe installed at ~/.screenpipe/pipes/meeting-summary-zh/pipe.md

==============================
Results: 8 passed, 0 failed, 0 warnings

All checks passed! You're ready to go.
```

**如果有 FAIL 项目，请按提示修复后重新检查。**

### 快速手动验证

如果不想运行脚本，可以手动检查核心功能：

```bash
# 1. Screenpipe 在运行吗？
curl -s http://localhost:3030/health | head -1

# 2. 有音频转录了吗？（说几句话后检查）
curl -s "http://localhost:3030/search?content_type=audio&limit=1" | head -5

# 3. MCP 配置好了吗？
claude mcp list | grep screenpipe
```

---

## 6. 第一次使用：会议前准备

### 启动所有组件

确保以下组件都在运行：

```bash
# 1. 启动 Screenpipe（如果还没启动）
screenpipe record
# 或打开 Screenpipe 桌面应用

# 2. 启动 Claude Code
claude
```

### 验证 MCP 连接

在 Claude Code 中输入你的第一个命令：

```
> 列出最近的会议
```

如果一切正常，Claude 会通过 MCP 查询 Screenpipe 并返回结果。如果还没有会议记录，Claude 会告诉你目前没有检测到会议——这是正常的。

### 配置 Screenpipe（可选）

项目中的 `config/screenpipe-settings.json` 包含推荐配置。你可以参考它在 Screenpipe 的设置界面中调整：

**关键配置项：**

| 设置 | 推荐值 | 说明 |
|------|--------|------|
| Whisper 模型 | `large-v3-turbo` | 兼顾准确度和速度 |
| 语言 | `["zh", "en"]` | 支持中英文自动检测 |
| VAD 引擎 | `silero` | 语音活动检测，过滤静音 |
| 说话人分离 | 启用 | 区分不同说话人 |
| 屏幕捕获模式 | `event_driven` | 只在有意义的事件时截图，节省资源 |

---

## 7. 会议进行中：实时问答

当你加入一个 Zoom/Teams/Meet 会议后，Screenpipe 会自动检测会议并开始录制。

在另一个终端窗口打开 Claude Code（或使用已打开的窗口），随时提问：

### 基本问答

```
> 现在在讨论什么？
```

Claude 会搜索最近几分钟的转录，告诉你当前的讨论主题。

```
> 刚才谁提到了 deadline？
```

Claude 会搜索包含 "deadline" 的转录片段，并标注是哪个说话人说的。

### 按时间范围查询

```
> 过去 10 分钟讨论了什么？
```

```
> 2:00 到 2:30 之间聊了什么？
```

### 屏幕内容

```
> 现在共享屏幕上显示了什么？
```

Claude 会搜索会议应用的 OCR 数据，告诉你屏幕上的文字内容。

### 按说话人查询

```
> Alice 最近说了什么？
```

```
> Speaker 2 关于预算说了什么？
```

> **提示：** 实时问答的延迟通常在 5-15 秒左右（Whisper 转录 + MCP 查询 + Claude 响应）。如果你需要的信息刚刚说过，等几秒再查询效果更好。

---

## 8. 会议结束后：生成摘要

### 方式一：在 Claude Code 中直接生成

这是最灵活的方式，你可以对话式地调整摘要内容。

```
> 生成上一个会议的摘要
```

Claude 会：
1. 调用 `list-meetings` 找到最近的会议
2. 调用 `search-content` 获取会议时间范围内的转录
3. 生成结构化摘要

**摘要包含以下部分：**
- 一句话概述 / One-line Summary
- 主要讨论点 / Key Discussion Points
- 决策事项 / Decisions Made
- 行动项 / Action Items（含负责人和截止日期）
- 未解决问题 / Open Questions
- 屏幕内容要点 / Screen Content Highlights

**追加提问示例：**

```
> 把 action items 整理成表格
> 关于预算的讨论更详细地展开一下
> 用英文重新生成摘要
```

### 方式二：使用 Screenpipe Pipe 生成

如果你安装了自定义 Pipe，也可以在 Screenpipe 界面中触发：

1. 打开 Screenpipe 桌面应用
2. 进入 Pipes 页面
3. 找到 "Meeting Summary (中英)"
4. 点击运行

Pipe 会自动执行完整的摘要流程，包括说话人识别、分段摘要（超过 30 分钟的会议）和转录质量检查。

### 长会议处理（> 1 小时）

对于超过 1 小时的会议，直接获取全部转录可能超出上下文限制。系统会自动分段处理：

```
> 生成今天下午 2 点到 5 点的会议摘要
```

Claude 会自动将长会议分成 30 分钟的片段，分别摘要后合并成最终结果。

如果你只关心某个特定话题：

```
> 今天的会议里，关于技术方案的讨论有哪些？
```

---

## 9. 说话人管理

Screenpipe 使用声纹嵌入来区分不同说话人。新的说话人一开始会被标记为 "Speaker 0"、"Speaker 1" 等。

### 查看未命名的说话人

```
> 列出未命名的说话人
```

Claude 会显示所有还未被命名的说话人 ID 及其示例发言片段，帮助你辨认。

### 命名说话人

```
> 把 Speaker 2 命名为 "Alice Chen"
```

命名后，这个声纹对应的所有转录（包括过去和未来）都会显示这个名字。命名是永久的，跨会议生效。

### 使用日历辅助识别

如果你的日历（如 Google Calendar）连接到了 Screenpipe，系统可以自动获取会议参与者名单：

```
> 这个会议有哪些参与者？帮我匹配声纹和名字
```

Claude 会：
1. 从日历获取参与者列表
2. 展示每个未命名说话人的示例发言
3. 建议匹配关系（例如："Speaker 2 可能是 Alice Chen，因为她在讨论设计相关话题"）
4. 等你确认后执行命名

### 合并重复说话人

有时同一个人可能被识别为多个 Speaker ID（例如切换了麦克风位置）：

```
> 合并 Speaker 3 到 Speaker 1（他们是同一个人）
```

### 按说话人搜索

```
> Alice 在今天的会议里说了什么？
> 列出所有 Bob 提到 "预算" 的发言
```

### 注意事项

| 场景 | 说话人识别效果 |
|------|--------------|
| 本地麦克风说话 | 准确率高，声纹清晰 |
| 远程会议多人通过系统音频 | 有限，多人可能被混为较少的 Speaker ID |
| 同一人不同设备/位置 | 可能产生多个 ID，需手动合并 |
| 多次会议后 | 声纹模型持续学习，准确率逐渐提升 |

---

## 10. 会议历史搜索

### 列出会议

```
> 列出今天的所有会议
> 列出这周的会议
> 最近 3 天有哪些会议？
```

### 跨会议搜索

```
> 上周的会议里，谁提到了 "技术方案"？
> 最近一个月关于预算的讨论有哪些？
> 找到我们上次讨论新功能的时间
```

### 比较不同会议

```
> 比较一下周一和周三会议中关于 sprint 目标的讨论
> 上次会议的 action items 完成了多少？
```

---

## 11. 中英混合语言处理

这是本项目的一个核心挑战。Whisper 模型按 ~30 秒的音频块检测语言，每个块只能识别为一种语言。

### 工作原理

```
音频块 1 (00:00-00:30): 全中文      → 正确识别为中文 ✅
音频块 2 (00:30-01:00): 全英文      → 正确识别为英文 ✅
音频块 3 (01:00-01:30): 中英混合    → 可能出错 ⚠️
                                      （整块被识别为一种语言，另一种语言被错误转录）
```

### 已知的限制

| 场景 | 效果 |
|------|------|
| 整段说中文 | 准确 |
| 整段说英文 | 准确 |
| 一段中文一段英文（段间切换） | 基本准确（每个块独立检测） |
| 句中混合（如 "Let's discuss 技术方案"） | 可能出错 |

### 实时应对

会议进行中如果发现转录不准：

```
> 刚才 2:15 左右的转录好像有问题，可以记一下
```

Claude 会记录这个时间戳，方便会后重新转录。

### 会后重新转录

如果某个时间段的转录质量差，可以用 retranscription API 重新处理：

```
> 2:00 到 2:30 的转录质量很差，帮我重新转录
```

Claude 会告诉你执行以下命令（或直接帮你执行）：

```bash
curl -X POST http://localhost:3030/audio/retranscribe \
  -H "Content-Type: application/json" \
  -d '{
    "start": "2026-04-09T14:00:00Z",
    "end": "2026-04-09T14:30:00Z",
    "engine": "whisper-large-v3-turbo",
    "prompt": "This is a bilingual Chinese-English meeting. 这是一个中英双语会议。",
    "vocabulary": [
      {"word": "技术方案", "weight": 1.5},
      {"word": "sprint planning", "weight": 1.2}
    ]
  }'
```

**自定义词汇表：** 在 `config/screenpipe-settings.json` 的 `vocabulary` 部分添加你团队常用的专业术语，可以提高转录准确度。

---

## 12. 问答日志

每次你向 Claude 询问会议内容时，交互记录会自动保存到本地文件：

**日志位置：** `~/.meeting-helper/qa-log/YYYY-MM-DD.md`

### 日志格式

```markdown
## 14:30 — [Meeting: Sprint Planning] — Q&A

**Questions asked:**
1. 今天讨论了什么技术方案？
   → 团队讨论了微服务迁移方案，决定先从用户服务开始

**Key findings:**
- Sprint 目标确认：本周完成用户服务 API 重构

---
```

### 查看历史日志

```
> 我今天问过哪些关于会议的问题？
> 上周的问答记录有哪些？
```

Claude 会读取 qa-log 目录中的文件并展示。

### 日志的用途

- **回忆** — "我上周问过什么来着？"
- **追踪** — 发现哪些话题你反复关注
- **审计** — 记录你从会议中获取信息的历史

---

## 13. 自定义摘要 Pipe

`pipes/meeting-summary-zh/pipe.md` 是自定义的会议摘要模板。你可以根据需要修改它。

### Pipe 的工作流程

```
步骤 1: 查找最近的会议
步骤 2: 识别说话人（检查日历参与者、已命名和未命名的说话人）
步骤 3: 收集会议数据（音频转录 + 屏幕 OCR）
步骤 4: 检查转录质量（标记低质量片段）
步骤 5: 长会议分段处理（每 30 分钟一段）
步骤 6: 生成结构化摘要
```

### 自定义摘要格式

编辑 `pipes/meeting-summary-zh/pipe.md` 中 "Step 6" 之后的模板。例如，如果你不需要 "参与者观点" 部分，可以删除它。如果你想添加新的部分（如 "风险项"），在模板中添加即可。

### 修改 Pipe 配置

Pipe 的 YAML 前言（frontmatter）控制行为：

```yaml
---
schedule: manual        # manual = 手动触发, 也可以设为 cron 表达式
enabled: true
model: "claude-sonnet-4-5"  # 使用的 AI 模型
permissions:
  allow:
    - App(zoom.us, Microsoft Teams, Google Chrome, Slack, Google Meet)
  deny:
    - App(1Password, Keychain Access)  # 永远不读取密码管理器
---
```

**修改后重新安装：**

```bash
cp pipes/meeting-summary-zh/pipe.md ~/.screenpipe/pipes/meeting-summary-zh/pipe.md
```

---

## 14. 故障排查

### 问题：MCP 工具返回错误

**症状：** Claude 说无法连接到 Screenpipe 或 MCP 工具报错

**解决方案：**

```bash
# 1. 检查 Screenpipe 是否在运行
curl http://localhost:3030/health

# 2. 如果没有运行，启动它
screenpipe record
# 等待 10 秒

# 3. 检查 MCP 配置
claude mcp list | grep screenpipe

# 4. 如果 MCP 没有配置，重新添加
claude mcp add screenpipe -- npx -y screenpipe-mcp
```

### 问题：没有检测到会议

**症状：** Screenpipe 在运行但 `list-meetings` 返回空

**可能原因：**
- macOS 上缺少辅助功能权限（会议检测依赖 UI 元素扫描）
- 使用的会议应用不在支持列表中

**解决方案：**

```
> 搜索最近 2 小时的音频转录
```

即使会议检测失败，音频转录仍然在工作。你可以直接搜索转录内容。

### 问题：没有音频转录

**症状：** 搜索返回空结果，即使你一直在说话

**检查步骤：**

```bash
# 检查 Screenpipe 是否在录音
curl -s "http://localhost:3030/search?content_type=audio&limit=1"

# 检查可用的音频设备
# 在 Screenpipe 设置中查看选择了哪个输入设备
```

**macOS：** 确认已授予麦克风权限
**Windows：** 确认 Settings > Privacy > Microphone 已允许

### 问题：转录质量差

**症状：** 转录文本乱码或明显错误

**解决方案：**
1. 确认 Whisper 模型是 `large-v3-turbo`（在 Screenpipe 设置中检查）
2. 确认语言设置包含你使用的语言
3. 对问题片段使用重新转录（见[第 11 节](#11-中英混合语言处理)）
4. 添加自定义词汇表（见 `config/screenpipe-settings.json` 的 vocabulary 部分）

### 问题：Claude Code CLI 找不到

```bash
# 安装 Claude Code CLI
npm install -g @anthropic-ai/claude-code

# 或通过官方渠道
# 访问 https://claude.ai/code
```

### 运行完整诊断

```bash
bash scripts/health-check.sh
```

---

## 15. 进阶技巧

### 高效查询技巧

**具体 > 模糊：**
```
# 好 — 给出时间和关键词
> 2:00 到 2:30 之间关于 API 设计的讨论

# 一般 — 太模糊
> 刚才讨论了什么
```

**组合筛选：**
```
> Alice 在下午的会议里说了什么关于 deadline 的话？
```

**指定格式：**
```
> 把今天会议的 action items 整理成 markdown 表格，包含负责人和截止日期
```

### 会议期间的最佳实践

1. **提前打开 Claude Code**：在会议开始前就启动，避免错过开头
2. **另一个终端窗口**：不要在会议应用的窗口中切换，保持 Claude Code 在独立窗口
3. **及时记录时间点**：如果听到重要内容但来不及查询，记下大概时间，会后再详细查
4. **长会议分段查询**：每 30 分钟查一次当前进展，而不是等到最后

### 与团队协作

**会后分享摘要：**
```
> 生成今天会议的摘要，用 markdown 格式，我要发给团队
```

**追踪 Action Items：**
```
> 列出最近 3 次会议未完成的 action items
```

### 隐私注意事项

- Screenpipe 录制的所有数据都存储在本地 `~/.screenpipe/` 目录
- 音频转录在本地通过 Whisper 处理
- 当使用 Claude 进行摘要/问答时，相关转录片段会发送到 Anthropic API
- 对于高度敏感的会议，可以考虑使用本地 LLM（如 Ollama）替代 Claude
- **务必了解你所在地区的录音法律和公司政策**

### 自定义词汇表

编辑 `config/screenpipe-settings.json` 中的 vocabulary 部分：

```json
{
  "vocabulary": {
    "entries": [
      {"word": "你们团队的专业术语", "weight": 1.5},
      {"word": "项目代号", "weight": 1.5},
      {"word": "technical term", "weight": 1.2}
    ]
  }
}
```

权重越高，Whisper 越倾向于识别为该词汇。建议将频繁出现的项目名称、人名、技术术语都加进去。

---

## 常见场景速查表

| 你想做什么 | 在 Claude Code 中输入 |
|-----------|---------------------|
| 当前在讨论什么 | `现在在讨论什么？` |
| 某人说了什么 | `Alice 说了什么关于 X？` |
| 生成会议摘要 | `生成上一个会议的摘要` |
| 提取 action items | `列出会议中的所有 action items` |
| 搜索特定话题 | `最近的会议里谁提到了"预算"？` |
| 屏幕共享内容 | `刚才屏幕上显示了什么？` |
| 列出今天的会议 | `今天有哪些会议？` |
| 命名说话人 | `把 Speaker 2 命名为 "张三"` |
| 转录质量差 | `2:00-2:30 的转录有问题，帮我重新转录` |
| 查看问答历史 | `我今天问过什么关于会议的问题？` |

---

## 下一步

- 查看 [README.md](../README.md) 了解项目结构和概述
- 查看 [CLAUDE.md](../CLAUDE.md) 了解 Claude 在会议场景下的完整指令
- 查看 [pipes/meeting-summary-zh/pipe.md](../pipes/meeting-summary-zh/pipe.md) 了解和自定义摘要格式
- 查看 [config/screenpipe-settings.json](../config/screenpipe-settings.json) 了解推荐的 Screenpipe 配置
