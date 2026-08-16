# LiveCaption

[English](README.md) | 中文

**LiveCaption** 是一个轻量、多后端的 macOS 语音转文字框架。可捕获麦克风、系统音频或两者；选择 ASR 后端（Apple Speech、本地 Sherpa-ONNX、Hugging Face）；显示实时字幕；并把转录保存在项目目录中。

会议是很自然的场景——Zoom/Teams/Meet 的系统声 + 你的麦克风——同一套能力也适用于讲座、视频、语言练习，或任何你想转成文字的实时音频。

## 功能

- 麦克风、系统声音，或 `auto`——两路都采集，只识别正在说话的那一路
- 可插拔 ASR：`apple` · `sherpa` · `hf` · `hf-stream`
- 离线模型与流式模型分走两条路径，各按自己的设计运行
- 字幕栏下拉框可热切换 ASR 模型，字幕不中断，无需重启
- 永远只有一栏字幕——`auto` 把两个通道门控进这一栏，并逐行标注来源
- 本地 Sherpa-ONNX 中英双语流式识别
- Apple Speech 实时识别和音频文件转录
- 可选择、复制、滚动、隐藏的悬浮字幕窗口
- `auto` 只写一份 transcript，每行标注来源通道（以及检测到的语言）
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
bash scripts/start.sh --source auto --asr sherpa
```

`auto` 是默认音源：两路都采集，但只识别正在说话的那一路——只跑一个识别器而不是两个，
每条字幕仍然标注 `(speaker)` 或 `(microphone)`。

首次运行会自动下载中英双语 INT8 模型。依赖、模型、缓存和临时文件全部保存在项目目录中；安装完成后识别不需要联网。

停止：

```bash
bash scripts/stop.sh
```

## 常用命令

```bash
# 默认：auto（两路都采集，只识别一路）+ Apple Speech
bash scripts/start.sh

# auto 适用于任何后端：speaker 有声时优先，静音时切回 microphone，单栏显示并标注来源
bash scripts/start.sh --source auto --asr sherpa

# 同一个门控单栏，换成 Apple Speech
bash scripts/start.sh --source auto --asr apple

# 只识别系统声音
bash scripts/start.sh --source system --asr sherpa

# 只识别麦克风
bash scripts/start.sh --source mic --asr sherpa

# Apple Speech 英文识别
bash scripts/start.sh --source mic --asr apple --language en-US

# 使用 cache-aware 流式 Hugging Face 模型（说话过程中就出字）
bash scripts/start.sh --source auto --asr hf-stream \
  --hf-model nvidia/nemotron-3.5-asr-streaming-0.6b --language auto

# 显示音量并保存调试 WAV
bash scripts/start.sh --source auto --asr sherpa --debug

# 调整窗口
bash scripts/start.sh --source auto --asr sherpa --height 160 --opacity 0.85
```

主要参数：

| 参数 | 可用值 | 默认值 |
| --- | --- | --- |
| `--source` | `mic`、`system`、`auto`（`both` 已退役 → `auto`） | `auto` |
| `--asr` | `apple`、`sherpa`、`hf`、`hf-stream` | `apple` |
| `--hf-model` | Hugging Face 模型 id（`--asr hf` / `hf-stream` 必填） | — |
| `--hf-models` | 字幕栏下拉框的额外模型 id，逗号分隔 | — |
| `--language` | `auto`，或 `zh-CN` / `en-US` 之类（见「混合语言会议」） | `zh-CN` |
| `--output-dir` | transcript 输出目录 | `transcripts/` |
| `--height` | 字幕窗口高度 | `120` |
| `--opacity` | 背景透明度 | `0.75` |
| `--debug` | 开启音量显示与 WAV 保存 | 关闭 |

## 字幕窗口

- 任何模式、任何后端都只有一栏字幕。左右并排的 speaker|microphone 双栏已退役：
  `--source both` 仍被接受，但会解析成 `auto`
- `--source auto`：门控单栏，每行以来源通道作为前缀；两路共用主 transcript，写入同样的标记
- `--source mic` / `--source system`：同一栏，由那一个通道独占
- 拖动手柄（模型下拉框左侧）：把窗口拖到屏幕任意位置
- 模型下拉框：不重启即可切换 ASR 模型（见下文）
- `Hide` / `Show`：收起为只剩控制栏的小条，或恢复完整字幕
- `Quit`：停止 LiveCaption
- 选择文字后按 `Cmd+C`（Windows 为 `Ctrl+C`）：复制所选字幕
- 没有选择文字时按同样的快捷键：复制全部字幕
- 鼠标滚动：查看历史字幕

## 混合语言会议

`--language` 并不是所有后端都用。`sherpa`、`hf`、`wsl-vllm` 自己判断语言，不接受这个参数；
`apple` 需要一个真实 locale，而且 **Apple Speech 上的 `--language auto` 会被静默折成 `zh-CN`**，
那里没有自动检测。

真正逐行检测的是 `--asr hf-stream`。在 `--language auto` 下，checkpoint 会给每一句标注它听到的
locale，双语会议因此能自己标注自己。用一段「英文 → 中文 → 英文」的音频实测：

| `--language` | 英文段 | 中文段 |
| --- | --- | --- |
| `auto` | 转写成功，标注 `en-US` | 转写成功，标注 `zh-CN` |
| `zh-CN` | **整段丢失** | 转写成功（比 `auto` 略准） |
| `en-US` | 转写成功 | **整段丢失** |

所以对会切换语言的会议来说，`auto` 不是锦上添花——钉错语言不会让另一种语言"识别得差一点"，
而是直接没有。只有整场都是同一种语言时才值得固定它。

检测到的 locale 写进 transcript，不占用字幕：

```text
[14:03:21] (speaker) [en-US] Hello everyone, welcome to the meeting.
[14:03:29] (microphone) [zh-CN] 大家好，欢迎参加今天的会议。
```

检测粒度是「一句」，所以一句话中途换语言时，两种语言都会被正确转写，但只带一个标签——
以这句话结束时的语言为准。

## 离线模型与流式模型分走两条路径

Hugging Face 上的语音模型分两类，LiveCaption 不把它们塞进同一个 worker，而是各走各的：

| | 离线 | 流式 |
| --- | --- | --- |
| 参数 | `--asr hf` | `--asr hf-stream` |
| 适用模型 | 任意 seq2seq / CTC checkpoint——Whisper、Qwen3-ASR | cache-aware RNNT——Nemotron ASR streaming |
| 送音频的方式 | 按 `--chunk-seconds` 切成独立片段，逐段解码 | 一路识别常驻，按模型训练时的 chunk 尺寸持续喂入 |
| 字幕形态 | 每段一条，全部是最终结果 | 说话过程中就出部分文字，按模型自己的标点提交 |
| 段与段之间 | 丢弃 encoder 状态 | 复用 encoder cache，不重复计算 |
| 依赖 | `transformers`（Qwen3-ASR 另需 `qwen-asr`） | `transformers >= 5.13`，实时使用基本需要 GPU |

把流式 checkpoint 放在离线路径上能跑，但白费——低延迟设计的模型只剩下准确率，延迟优势全没了。
两个 worker 在模型和路径对不上时都会在 stderr 里说明。

`qwen-asr` 把 `transformers` 钉死在 `4.57.6`，而流式 worker 需要 `>= 5.13`，同一个解释器装不下
两者。`scripts/setup-hf-stream.sh` 会在项目内的 `.build/stream-env` 建好这第二个环境，`start.sh`
在第一次用到流式模型时自动调用。如果你自己另有环境，用 `--hf-stream-python` 指过去即可。

## GPU

两个 Hugging Face worker 都会自动选设备：先 CUDA，再 `mps`（Apple Silicon 的 GPU），最后 CPU。
不需要配置——worker 会在 stderr（`logs/subtitle.log`）打印 `ASR device: mps`，字幕栏的
`... ready (mps)` 里也能看到。

对流式路径来说这不是锦上添花。在本项目的 Mac 上实测，44 秒音频跑
`nvidia/nemotron-3.5-asr-streaming-0.6b`：

| 设备 | 墙钟时间 | 相对实时 |
| --- | --- | --- |
| `mps` | 16 秒 | 快 2.7 倍——可以实时跟上 |
| `cpu` | 147 秒 | 慢 3.3 倍——积压越滚越多，字幕永远追不上 |

想强制指定设备（比如你的 torch 版本在 MPS 上缺某个算子）：

```bash
LIVECAPTION_DEVICE=cpu bash scripts/start.sh --source auto --asr hf-stream \
  --hf-model nvidia/nemotron-3.5-asr-streaming-0.6b
```

## 热切换模型

拖动手柄和 `Hide` 之间的下拉框会列出当前宿主能启动的所有后端——macOS 上是 `Apple Speech` 和
`Sherpa-ONNX`，Windows 上是 `Sherpa-ONNX` 和 `WSL vLLM`——以及 `--hf-model` 与 `--hf-models`
提供的每个 Hugging Face 模型 id，并标注 `(streaming)` 或 `(chunked)`，走哪条路径一眼可见：

```bash
bash scripts/start.sh --source auto --asr sherpa \
  --hf-models Qwen/Qwen3-ASR-0.6B,openai/whisper-large-v3-turbo
```

id 里含 `streaming` 的走流式路径，其余走分块路径；加 `stream:` 或 `offline:` 前缀可以覆盖判断。

选中某一项会停掉正在运行的识别器并就地启动新的；采集、transcript 文件和字幕历史都不中断，
收起成小条时下拉框依然可用。字幕区会依次显示 `Switching to ...` 和 `... ready`。若某个模型
启动失败（依赖缺失、模型下载不到、语音识别权限被拒），失败信息同样显示在字幕区而不会退出程序，
再选一个继续即可。切换模型不会改变字幕栏的形状——布局只由 `--source` 决定，所以模型变了字幕也不会跳。

Hugging Face 模型首次使用时会下载到项目内的 `models/hf/`。如果希望和其他工具共用缓存，
可以自行设置 `HF_HOME`。

**下拉框里只会出现你指定过的模型。** 直接 `bash scripts/start.sh` 不带任何模型参数，菜单里就
只有内置后端。把模型 id 写在命令行上，或者写进 `config.json`：

```json
{
  "source": "auto",
  "asr": "hf-stream",
  "hf-model": "nvidia/nemotron-3.5-asr-streaming-0.6b",
  "hf-models": ["Qwen/Qwen3-ASR-0.6B", "openai/whisper-large-v3-turbo"],
  "language": "auto"
}
```

两个宿主都会读取项目根目录的 `config.json`（`--config <路径>` 可以指到别处）。优先级是
内置默认值 < `config.json` < 环境变量 < 命令行参数，你手打的参数永远最优先。
可以从 `config.example.json` 复制一份开始。

## 文件位置

所有运行时文件都位于项目目录：

```text
LiveCaption/
├── scripts/                 # 启动、停止和安装脚本
├── src/
│   ├── swift/               # macOS 主程序和 Apple Speech 工具
│   └── python/              # ASR worker 和 transcript 工具
├── .build/                  # 编译产物、Python 依赖和缓存
│   └── stream-env/          # 流式 worker 用的 transformers >= 5.13 虚拟环境
├── models/                  # 本地 Sherpa 模型
│   └── hf/                  # Hugging Face 缓存（HF_HOME）
├── transcripts/             # 字幕文字
│   ├── YYYY-MM-DD.txt       # microphone；auto 模式下两路都写这里，逐行标注来源
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
| `hf` | 离线 Hugging Face 模型（Whisper、Qwen3-ASR） | 实验模式；依赖和模型需自行管理；字幕按块出现 |
| `hf-stream` | cache-aware 流式 Hugging Face 模型（Nemotron ASR streaming） | 实验模式；需要 `transformers >= 5.13`，实时使用基本要 GPU |

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
