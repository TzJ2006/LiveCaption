# Meeting Helper 部署教程

## 项目简介

Meeting Helper 是一个基于 Screenpipe 和 Claude Code CLI 的 AI 会议助手，可以：
- 捕获会议音频和屏幕内容
- 实时生成会议转录
- 回答关于会议讨论的问题
- 生成会议摘要和行动项

## 环境说明

**本项目不需要 Python/Conda 环境**。项目基于 Node.js 运行，主要依赖：
- Node.js >= 18
- Screenpipe（音频/屏幕捕获工具）
- Claude Code CLI（AI 交互界面）

## 部署步骤

### 1. 前置要求检查

确保已安装：
```bash
# 检查 Node.js 版本（需要 >= 18）
node -v

# 检查 Homebrew（macOS）
brew --version
```

### 2. 克隆项目

```bash
git clone https://github.com/TzJ2006/MeetingHelper.git
cd MeetingHelper
```

### 3. 安装 Screenpipe

使用 Homebrew 安装：
```bash
brew install screenpipe
```

安装完成后验证：
```bash
which screenpipe
# 输出：/opt/homebrew/bin/screenpipe
```

### 4. 运行安装脚本

```bash
bash scripts/install.sh
```

安装脚本会自动：
- 检查 Node.js 版本
- 检查 Screenpipe 安装
- 安装会议摘要 pipe 到 `~/.screenpipe/pipes/meeting-summary-zh/`
- 配置 Claude Code 的 MCP 服务器

### 5. macOS 权限配置

在 **系统设置 > 隐私与安全性** 中授予 Screenpipe 以下权限：

1. **屏幕录制** - 捕获屏幕内容和检测会议
2. **辅助功能** - 检测会议（扫描 UI 元素）
3. **麦克风** - 捕获音频

### 6. 健康检查

运行健康检查脚本验证配置：
```bash
bash scripts/health-check.sh
```

预期输出：
```
Results: 4 passed, 1 failed, 3 warnings
```

**注意**：失败和警告是因为 Screenpipe 尚未启动，这是正常的。

## 启动使用

### 启动 Screenpipe

在后台启动 Screenpipe 录制：
```bash
# 方式 1：CLI 启动（推荐用于测试）
screenpipe

# 方式 2：桌面应用（如果已安装）
open -a Screenpipe
```

等待 10-15 秒让 Screenpipe 初始化，然后验证：
```bash
curl http://localhost:3030/health
# 应返回 JSON 响应
```

### 启动 Claude Code

```bash
cd MeetingHelper
claude
```

### 使用示例

在 Claude Code 中尝试以下命令：

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
├── CLAUDE.md                    # Claude 指令文档
├── README.md                    # 项目说明
├── tutorial.md                  # 部署教程（本文件）
├── config/
│   └── screenpipe-settings.json # Screenpipe 推荐设置
├── pipes/
│   └── meeting-summary-zh/      # 中英双语会议摘要 pipe
│       └── pipe.md
├── scripts/
│   ├── install.sh              # macOS/Linux 安装脚本
│   ├── install.ps1             # Windows 安装脚本
│   └── health-check.sh         # 健康检查脚本
└── docs/
    └── ecl/
        └── meeting-helper.yaml  # 项目规划文档
```

## 常见问题

### Q: 为什么健康检查显示失败？
A: 如果看到 "Screenpipe not reachable at localhost:3030"，说明 Screenpipe 没有运行。运行 `screenpipe` 启动它。

### Q: 混合中英文转录准确度如何？
A: Whisper 按 ~30 秒的块检测语言，不是按句子。句子中切换语言（如 "Let's discuss the 技术方案"）可能在该块产生错误。如果某个时间段的转录质量差，可以事后重新转录。

### Q: 说话人识别准确吗？
A: 本地麦克风的说话人最可靠。远程会议参与者共享一个音频通道，可能无法单独识别。说话人准确度会随着声纹模型学习而提高。

### Q: 这个项目需要 Python 或 Conda 环境吗？
A: **不需要**。本项目完全基于 Node.js 和 Screenpipe（Rust 编译的二进制文件），不需要 Python 环境。Claude Code 使用 Anthropic API，也不需要本地 Python 环境。

### Q: 会议数据存储在哪里？
A: 所有音频和屏幕数据本地存储在 `~/.screenpipe/`。使用 Claude 进行摘要/问答时，转录数据会发送到 Anthropic API。

### Q: 录制会议是否合法？
A: 这取决于你所在的司法管辖区和公司政策。许多公司环境禁止会议录制。使用前请确认：
- 你所在地区的录制同意法律
- 你的公司政策
- 获得会议参与者的同意

## 验证部署成功

完整的验证流程：

1. **启动 Screenpipe**
   ```bash
   screenpipe
   ```

2. **等待初始化**（10-15 秒）

3. **运行健康检查**
   ```bash
   bash scripts/health-check.sh
   ```
   
   应该看到所有检查通过（假设已授予权限）：
   ```
   Results: 8 passed, 0 failed, 0 warnings
   All checks passed! You're ready to go.
   ```

4. **启动 Claude Code**
   ```bash
   claude
   ```

5. **测试基本功能**
   ```
   > 列出过去 1 小时的会议
   ```

## 下一步

- 阅读 `CLAUDE.md` 了解详细的交互指令
- 查看 `README.md` 了解功能特性
- 加入一个会议测试实时转录
- 会议后生成摘要

## 故障排除

如果遇到问题：

1. **MCP 连接失败**
   ```bash
   claude mcp list  # 检查 screenpipe 是否在列表中
   claude mcp add screenpipe -- npx -y screenpipe-mcp  # 重新添加
   ```

2. **Screenpipe 无法启动**
   ```bash
   # 检查日志
   tail -f ~/.screenpipe/screenpipe.log
   
   # 重启
   pkill screenpipe
   screenpipe
   ```

3. **权限问题（macOS）**
   - 系统设置 > 隐私与安全性
   - 确保 Screenpipe 在屏幕录制、辅助功能、麦克风列表中
   - 可能需要移除并重新添加 Screenpipe

## 更新日志

- **2026-04-09**: 初始部署教程创建
