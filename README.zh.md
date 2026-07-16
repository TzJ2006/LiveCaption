# LiveCaption

[English](README.md) | 中文

**LiveCaption** 是一个轻量、多后端的 macOS 语音转文字框架。可捕获麦克风、系统音频或两者；选择 ASR 后端（Apple Speech、本地 Sherpa-ONNX、Hugging Face）；显示实时字幕；并把转录保存在项目目录中。

会议是很自然的场景——Zoom/Teams/Meet 的系统声 + 你的麦克风——同一套能力也适用于讲座、视频、语言练习，或任何你想转成文字的实时音频。

## 功能

- 麦克风、系统声音或双音源捕获
- 可插拔 ASR：`apple` · `sherpa` · `hf`
- Apple 双音源智能门控合并；Sherpa/HF 双音源左右分栏
- 本地 Sherpa-ONNX 中英双语流式识别
- Apple Speech 实时识别和音频文件转录
- 可选择、复制、滚动、隐藏的悬浮字幕窗口
- Sherpa/HF 分别保存双音源 transcript；Apple 双音源保存合并 transcript
- Debug 模式保存 WAV，便于检查收音与离线转录
- Sherpa 模型缺失时自动安装到项目目录

## 系统要求

- macOS 13 或更高版本
- Xcode Command Line Tools（需要 `xcrun swiftc`）
- Python 3（仅 Sherpa/Hugging Face 模式需要）
- 麦克风权限（使用 mic 时）
- 屏幕录制权限（捕获 system audio 时）
- 语音识别权限（使用 Apple Speech 时）

## 快速开始

```bash
cd /path/to/LiveCaption
```

会议 / 双音源推荐（本地 Sherpa）：

```bash
bash scripts/start.sh --source both --asr sherpa
```

首次运行会自动下载中英双语 INT8 模型。依赖、模型、缓存和临时文件全部保存在项目目录中；安装完成后识别不需要联网。

停止：

```bash
bash scripts/stop.sh
```

## 常用命令

```bash
# 默认：麦克风 + Apple Speech
bash scripts/start.sh

# Apple Speech 合并 system/microphone：system 有声时优先，静音时使用 microphone
bash scripts/start.sh --source both --asr apple

# 只识别系统声音
bash scripts/start.sh --source system --asr sherpa

# 只识别麦克风
bash scripts/start.sh --source mic --asr sherpa

# 同时识别两路声音，显示左右双栏（Sherpa/HF）——会议场景很有用
bash scripts/start.sh --source both --asr sherpa

# Apple Speech 英文识别
bash scripts/start.sh --source mic --asr apple --language en-US

# 显示音量并保存调试 WAV
bash scripts/start.sh --source both --asr sherpa --debug

# 调整窗口
bash scripts/start.sh --source both --asr sherpa --height 160 --opacity 0.85
```

主要参数：

| 参数 | 可用值 | 默认值 |
| --- | --- | --- |
| `--source` | `mic`、`system`、`both` | `mic` |
| `--asr` | `apple`、`sherpa`、`hf` | `apple` |
| `--language` | 例如 `zh-CN`、`en-US` | `zh-CN` |
| `--output-dir` | transcript 输出目录 | `transcripts/` |
| `--height` | 字幕窗口高度 | `120` |
| `--opacity` | 背景透明度 | `0.75` |
| `--debug` | 开启音量显示与 WAV 保存 | 关闭 |

## 字幕窗口

- `--source both --asr apple`：智能门控合并两路音频，显示单栏字幕并写入主 transcript
- `--source both` 搭配 Sherpa/HF：左侧显示 speaker/system，右侧显示 microphone
- `Hide` / `Show`：收起或恢复字幕
- `Quit`：停止 LiveCaption
- 选择文字后按 `Cmd+C`：复制所选字幕
- 没有选择文字时按 `Cmd+C`：复制当前栏全部字幕
- 鼠标滚动：查看历史字幕

## 文件位置

所有运行时文件都位于项目目录：

```text
LiveCaption/
├── scripts/                 # 启动、停止和安装脚本
├── src/
│   ├── swift/               # macOS 主程序和 Apple Speech 工具
│   └── python/              # ASR worker 和 transcript 工具
├── .build/                  # 编译产物、Python 依赖和缓存
├── models/                  # 本地 Sherpa 模型
├── transcripts/             # 字幕文字
│   ├── YYYY-MM-DD.txt       # microphone，或 Apple both 的合并 transcript
│   └── YYYY-MM-DD-sys.txt   # speaker/system
├── debug-audio/             # Debug WAV
└── logs/
    ├── subtitle.log
    ├── subtitle-stop.log
    └── subtitle.pid
```

这些运行时目录已加入 `.gitignore`。

## 转录音频文件

使用 Apple Speech 手动转录 WAV 或其他受 AVFoundation 支持的音频文件：

```bash
bash scripts/transcribe.sh "debug-audio/example.wav" --language en-US
```

把结果写入文件：

```bash
bash scripts/transcribe.sh "debug-audio/example.wav" \
  --language en-US \
  --output "transcripts/example.txt"
```

这个命令在文件处理结束后会自动退出，不会持续运行。

## ASR 后端

| 模式 | 适用情况 | 说明 |
| --- | --- | --- |
| `sherpa` | 双音源、离线字幕（如会议） | 推荐；真正流式，中英双语，安装后完全本地 |
| `apple` | 单音源、智能门控双音源、手动文件转录 | 系统原生；实时任务每 50 秒轮换，可能使用 Apple 在线语音服务 |
| `hf` | 自行选择 Hugging Face 模型 | 实验模式；依赖和模型需要自行管理 |

## 本地 LLM

`src/python/query_transcript.py` 可以把 transcript 发送到兼容 OpenAI API 的本地服务，例如 Ollama：

```bash
python3 src/python/query_transcript.py \
  transcripts/2026-07-10.txt \
  "请总结会议结论和待办事项"
```

默认连接 `http://localhost:11434/v1`，默认模型为 `llama3.1`。可以通过 `LOCAL_LLM_BASE_URL`、`LOCAL_LLM_MODEL` 和 `LOCAL_LLM_API_KEY` 修改。

## 完整教程

权限设置、英文识别、Debug 音频和故障排查见 [tutorial.zh.md](tutorial.zh.md)（[English tutorial](tutorial.md)）。
