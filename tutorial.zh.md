# LiveCaption 使用教程

[English](tutorial.md) | 中文

**LiveCaption** 是一个轻量、多后端的 macOS 语音转文字框架。本教程从零开始介绍如何运行它：同时识别系统声音和麦克风、使用英文输入、检查 Debug 音频，以及排查“有声音但没有字幕”等问题。

会议是常见场景（Zoom/Teams/Meet + 麦克风），同一流程也适用于讲座、视频，或任何你想转成文字的实时音频。

## 1. 工作方式

LiveCaption 的 Swift 主程序负责：

1. 使用 `AVAudioEngine` 捕获麦克风；
2. 使用 `ScreenCaptureKit` 捕获当前系统输出；
3. 把音频送给 Apple Speech 或本地 Sherpa-ONNX；
4. 在屏幕底部显示字幕；
5. 把最终文字写入 `transcripts/`。

字幕区域只有一栏。在 `--source auto` 下两路都采集，但只识别正在说话的那一路，每行标注来源：

```text
┌───────────────────────────────────────────────────────────┐
│ (speaker) 对方或电脑播放的声音                              │
│ (microphone) 你对着麦克风说的话                             │
└───────────────────────────────────────────────────────────┘
```

## 2. 准备环境

进入项目目录：

```bash
cd /path/to/LiveCaption
```

确认基础工具存在：

```bash
sw_vers -productVersion
xcrun --find swiftc
python3 --version
```

如果 `xcrun --find swiftc` 失败，安装 Xcode Command Line Tools：

```bash
xcode-select --install
```

## 3. 设置 macOS 权限

打开“系统设置 → 隐私与安全性”，根据使用的音源开启权限：

- 麦克风：使用 `mic` 或 `auto` 时需要；
- 屏幕录制：使用 `system` 或 `auto` 时需要，ScreenCaptureKit 通过这个权限读取系统音频；
- 语音识别：只有 `--asr apple` 需要。

权限列表中可能显示 Terminal、`live-subtitle` 或启动它的终端应用。修改权限后，先停止 LiveCaption，再重新运行命令；必要时完全退出并重新打开终端。

## 4. 推荐的第一次启动

同时识别会议声音和自己的麦克风：

```bash
bash scripts/start.sh --source auto --asr sherpa
```

如果本地没有 Sherpa，启动脚本会自动：

1. 把 Python 依赖安装到 `.build/pydeps/`；
2. 把下载缓存和临时文件放到 `.build/`；
3. 把中英双语 INT8 模型安装到 `models/`；
4. 加载模型进行验证；
5. 编译并启动字幕窗口。

模型下载中断后，再次运行相同命令即可继续。成功后，`models/` 中应有：

```text
models/sherpa-onnx-streaming-paraformer-bilingual-zh-en/
├── encoder.int8.onnx
├── decoder.int8.onnx
└── tokens.txt
```

也可以单独运行安装检查：

```bash
bash scripts/setup-sherpa.sh
```

## 5. 选择音源

只识别麦克风：

```bash
bash scripts/start.sh --source mic --asr sherpa
```

只识别电脑播放的声音：

```bash
bash scripts/start.sh --source system --asr sherpa
```

两路都采集，只用一个识别器（默认）：

```bash
bash scripts/start.sh --source auto --asr sherpa
```

在 `auto` 模式中：

- 两路声音都采集，但只有正在说话的那一路会送去识别；
- speaker 有声时优先占用通道，静音约 0.6 秒后交还给 microphone；
- 只有一栏字幕、一个识别器，每行仍以 `(speaker)` 或 `(microphone)` 作为前缀；
- 两路共用 `transcripts/YYYY-MM-DD.txt`，行内写入同样的来源前缀；
- 一行字幕归属于开启它的那一路，因此说到一半发生切换也不会把句子劈成两行；
- `--record` / `--debug` 仍然按通道各存一个 WAV——门控只决定字幕，不影响录音。

旧的 `--source both`（每个通道一个识别器、左右两栏）已经退役。它仍然被接受，但会解析成 `auto`。

## 6. 中文、英文和混合输入

Sherpa 使用中英双语模型，不需要指定语言：

```bash
bash scripts/start.sh --source auto --asr sherpa
```

它可以处理中文、英文和中英混合内容。专有名词、姓名、缩写和多人重叠说话仍可能识别错误。

使用 Apple Speech 识别中文：

```bash
bash scripts/start.sh --source mic --asr apple --language zh-CN
```

使用 Apple Speech 识别英文：

```bash
bash scripts/start.sh --source mic --asr apple --language en-US
```

双音源会议建议优先使用 Sherpa，避免依赖 Apple Speech 的并发实时任务。

## 7. 使用字幕窗口

字幕窗口初始位于屏幕底部，横向铺满整个屏幕宽度：

- 拖动手柄（模型下拉框左侧）：拖动窗口；窗口始终会有一角留在屏幕内；
- 模型下拉框：不重启就地切换 ASR 模型；
- `Hide`：收起为只剩控制栏的小条，对齐窗口右下角；
- `Show`：从小条向左上方展开，恢复完整字幕；
- `Quit`：停止 LiveCaption 并退出（macOS 会调用 `scripts/stop.sh`）；
- 选择字幕后按 `Cmd+C`（Windows 为 `Ctrl+C`）：复制选择内容；
- 不选择文字时按同样的快捷键：复制全部字幕历史；
- 使用鼠标滚轮：查看更早的字幕。

调整窗口高度和背景透明度：

```bash
bash scripts/start.sh \
  --source auto \
  --asr sherpa \
  --height 160 \
  --opacity 0.85
```

参数变化需要停止并重新启动才能生效——ASR 模型除外，它可以通过下拉框热切换。

用下面的方式把想用的 Hugging Face 模型放进下拉框：

```bash
bash scripts/start.sh \
  --source auto \
  --asr sherpa \
  --hf-models Qwen/Qwen3-ASR-0.6B,openai/whisper-large-v3-turbo
```

选中某一项会停掉正在运行的识别器并启动所选模型；采集和 transcript 文件都不中断，字幕区会依次
显示 `Switching to ...` 和 `... ready`。若模型启动失败，失败信息显示在字幕区、程序继续运行，
再选一个即可。Hugging Face 权重会下载到项目内的 `models/hf/`，所以第一次切到新模型要等它下载完。

每个 Hugging Face 条目都标注了 `(chunked)` 或 `(streaming)`，含义见下一节。

## 7a. 离线与流式 Hugging Face 模型

`--asr hf` 和 `--asr hf-stream` 是两个不同的 worker，因为这两类模型本来就是两种东西：

- **`hf`（离线）**——Whisper、Qwen3-ASR 这类。音频被切成 `--chunk-seconds` 的独立片段，逐段
  转写，所以一段说完才出字幕，并且每条字幕都是最终结果。
- **`hf-stream`（流式）**——cache-aware RNNT checkpoint，例如
  `nvidia/nemotron-3.5-asr-streaming-0.6b`。整个运行期间只有一路识别常驻，按模型训练时的
  chunk 尺寸持续喂入并复用 encoder cache。句子还在说的时候文字就出来了，模型打出标点时提交。

```bash
bash scripts/start.sh \
  --source auto \
  --asr hf-stream \
  --hf-model nvidia/nemotron-3.5-asr-streaming-0.6b \
  --language auto
```

这里的 `--language` 是有作用的：它会作为模型的语言 prompt。用 `auto` 让模型逐句自动判断（系统
声和麦克风语言不同时很有用），或者用 `zh-CN` / `en-US` 之类的 locale 固定住。不支持的取值会退回
`auto`，并在 stderr 里说明。

下拉框根据 id 本身决定走哪个 worker——id 里含 `streaming` 的走流式路径。可以用前缀覆盖：

```bash
bash scripts/start.sh --source auto --asr sherpa \
  --hf-models stream:my/custom-cache-aware-model,offline:some/streaming-named-but-offline-model
```

流式 checkpoint 需要 `transformers >= 5.13`，而 `qwen-asr` 把 `transformers` 钉死在 `4.57.6`，
同一个解释器装不下两者。`start.sh` 已经处理好了：第一次用到流式模型时它会调用
`scripts/setup-hf-stream.sh`，在项目内建出 `.build/stream-env` 并装上新版 transformers。
你自己另有环境的话，用 `--hf-stream-python` 指过去。

### GPU

两个 Hugging Face worker 都会自己选设备——先 CUDA，再 `mps`（Apple Silicon），最后 CPU。
确认实际用的是哪个：

```bash
grep "ASR device" logs/subtitle.log
```

对流式路径来说，这一项直接决定功能能不能用。在本项目的 Mac 上，44 秒音频跑 0.6B 的 Nemotron，
**`mps` 用 16 秒**（比实时快 2.7 倍），**CPU 用 147 秒**（比实时慢 3.3 倍）。比实时慢不是
“字幕有点延迟”，而是没处理完的音频不断堆积，字幕每秒都落后更多，永远追不回来。

如果你的 torch 版本在 MPS 上缺算子，可以强制用 CPU：

```bash
LIVECAPTION_DEVICE=cpu bash scripts/start.sh --source auto --asr hf-stream \
  --hf-model nvidia/nemotron-3.5-asr-streaming-0.6b
```

### 如果下拉框是空的

菜单里只有内置后端，加上**你传进去的那些模型 id**。直接 `bash scripts/start.sh` 没指定任何模型，
Hugging Face 的条目自然一个都不会出现。要么把 id 写在命令行上，要么写进项目根目录的
`config.json`——现在两个宿主都会读它，命令行参数依然优先：

```json
{
  "source": "auto",
  "asr": "hf-stream",
  "hf-model": "nvidia/nemotron-3.5-asr-streaming-0.6b",
  "hf-models": ["Qwen/Qwen3-ASR-0.6B"],
  "language": "auto"
}
```

## 8. Transcript 和日志

麦克风最终字幕：

```text
transcripts/YYYY-MM-DD.txt
```

系统声音最终字幕：

```text
transcripts/YYYY-MM-DD-sys.txt
```

运行日志：

```text
logs/subtitle.log
```

停止日志：

```text
logs/subtitle-stop.log
```

查看最近日志：

```bash
tail -n 100 logs/subtitle.log
```

所有这些文件都在 LiveCaption 目录中。

## 9. Debug 音频

如果程序能够收音但不显示字幕，使用 Debug 模式：

```bash
bash scripts/stop.sh
bash scripts/start.sh --source auto --asr sherpa --debug
```

窗口会显示两路输入的 dB 音量，并在 `debug-audio/` 生成：

```text
YYYY-MM-DD-HHMMSS-microphone.wav
YYYY-MM-DD-HHMMSS-speaker.wav
```

判断方法：

- 音量一直是 `waiting`：该音源没有送入音频帧；
- dB 数值变化但没有字幕：检查 ASR、语言或模型日志；
- WAV 能被文件转录识别：采集链路基本正常，应继续检查实时 ASR；
- WAV 本身几乎无声：检查输入设备、系统音量或权限。

## 10. 手动转录一个音频文件

使用 Apple Speech 输出到终端：

```bash
bash scripts/transcribe.sh \
  "debug-audio/YYYY-MM-DD-HHMMSS-microphone.wav" \
  --language en-US
```

保存到项目内的 transcript 文件：

```bash
bash scripts/transcribe.sh \
  "debug-audio/YYYY-MM-DD-HHMMSS-microphone.wav" \
  --language en-US \
  --output "transcripts/manual-transcription.txt"
```

中文文件使用 `--language zh-CN`。路径包含空格时必须加引号。该命令会等待完整文件处理结束，然后退出。

## 11. 停止和重新启动

正常停止：

```bash
bash scripts/stop.sh
```

然后重新启动：

```bash
bash scripts/start.sh --source auto --asr sherpa
```

如果提示 `Subtitle window already running`，先运行停止命令。停止脚本会同时清理主程序和 Sherpa/Hugging Face 子进程。

## 12. 常见问题

### 字幕窗口没有出现

检查日志：

```bash
tail -n 100 logs/subtitle.log
```

然后重新启动：

```bash
bash scripts/stop.sh
bash scripts/start.sh --source auto --asr sherpa
```

### 系统声音没有字幕

1. 确认命令包含 `--source system` 或 `--source auto`；
2. 确认“屏幕录制”权限已开启；
3. 让电脑实际播放一段有声音的内容；
4. 使用 `--debug` 检查 speaker dB；
5. 修改权限后重新启动程序。

### 麦克风没有字幕

1. 确认“麦克风”权限已开启；
2. 使用 `--debug` 检查 microphone dB；
3. 检查生成的 `*-microphone.wav`；
4. 使用 `transcribe.sh` 手动转录该文件。

### 出现 `No speech detected`

这不一定表示超时，也可能是音量过低、噪声、语言设置不匹配或音频太短。先检查 Debug WAV：如果文件转录成功但实时字幕为空，问题更可能位于实时识别链路。

### Sherpa 模型安装失败

确认网络和磁盘空间，然后重新运行：

```bash
bash scripts/setup-sherpa.sh
```

未完成的下载保存在 `models/*.part`，下次运行会尝试继续。不要把模型移动到用户主目录；程序固定从 LiveCaption 的 `models/` 读取。

### 两路声音只有一路有内容

先分别测试：

```bash
bash scripts/start.sh --source mic --asr sherpa --debug
```

停止后再测试：

```bash
bash scripts/start.sh --source system --asr sherpa --debug
```

两路单独都正常后，再使用 `--source auto`，由门控在两者之间挑选。

## 13. 本地处理与隐私

- Sherpa 模型安装完成后，实时识别在本机完成；
- transcript、日志、Debug WAV、模型和缓存都保存在 LiveCaption；
- Apple Speech 可能使用 Apple 在线语音服务；
- Hugging Face 模式的第三方依赖与缓存不由自动安装器管理；如果要求所有文件都留在项目目录，请使用 Sherpa；
- `src/python/query_transcript.py` 会把 transcript 发送到你配置的 API 地址，默认是本机 Ollama；
- 录制会议前应确认参与者同意，并遵守所在地法律和组织政策。
