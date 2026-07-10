# MeetingHelper 使用教程

这份教程从零开始介绍如何在 macOS 上运行 MeetingHelper、同时识别系统声音和麦克风、使用英文输入、检查 Debug 音频，以及排查“有声音但没有字幕”等问题。

## 1. 工作方式

MeetingHelper 的 Swift 主程序负责：

1. 使用 `AVAudioEngine` 捕获麦克风；
2. 使用 `ScreenCaptureKit` 捕获当前系统输出；
3. 把音频送给 Apple Speech 或本地 Sherpa-ONNX；
4. 在屏幕底部显示字幕；
5. 把最终文字写入 `transcripts/`。

双音源模式使用两个独立识别 stream 和两个独立字幕区域：

```text
┌─────────────────────────────┬─────────────────────────────┐
│ speaker / system            │ microphone                  │
│ 对方或电脑播放的声音         │ 你对着麦克风说的话           │
└─────────────────────────────┴─────────────────────────────┘
```

## 2. 准备环境

进入项目目录：

```bash
cd /Users/tongtongtot/Desktop/algorithms/MeetingHelper
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

- 麦克风：使用 `mic` 或 `both` 时需要；
- 屏幕录制：使用 `system` 或 `both` 时需要，ScreenCaptureKit 通过这个权限读取系统音频；
- 语音识别：只有 `--asr apple` 需要。

权限列表中可能显示 Terminal、`live-subtitle` 或启动它的终端应用。修改权限后，先停止 MeetingHelper，再重新运行命令；必要时完全退出并重新打开终端。

## 4. 推荐的第一次启动

同时识别会议声音和自己的麦克风：

```bash
bash scripts/start.sh --source both --asr sherpa
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

同时识别两路声音：

```bash
bash scripts/start.sh --source both --asr sherpa
```

在 `both` 模式中：

- 左栏 `(speaker)` 来自系统声音；
- 右栏 `(microphone)` 来自麦克风；
- 两栏可以同时更新，不会互相覆盖；
- transcript 文件也分别保存。

## 6. 中文、英文和混合输入

Sherpa 使用中英双语模型，不需要指定语言：

```bash
bash scripts/start.sh --source both --asr sherpa
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

字幕窗口位于屏幕底部：

- `Hide`：把窗口收起到按钮高度；
- `Show`：恢复完整字幕；
- `Quit`：运行停止脚本并关闭程序；
- 选择字幕后按 `Cmd+C`：复制选择内容；
- 不选择文字时按 `Cmd+C`：复制当前栏全部历史；
- 使用鼠标滚轮：查看更早的字幕。

调整窗口高度和背景透明度：

```bash
bash scripts/start.sh \
  --source both \
  --asr sherpa \
  --height 160 \
  --opacity 0.85
```

参数变化需要停止并重新启动才能生效。

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

所有这些文件都在 MeetingHelper 目录中。

## 9. Debug 音频

如果程序能够收音但不显示字幕，使用 Debug 模式：

```bash
bash scripts/stop.sh
bash scripts/start.sh --source both --asr sherpa --debug
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
bash scripts/start.sh --source both --asr sherpa
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
bash scripts/start.sh --source both --asr sherpa
```

### 系统声音没有字幕

1. 确认命令包含 `--source system` 或 `--source both`；
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

未完成的下载保存在 `models/*.part`，下次运行会尝试继续。不要把模型移动到用户主目录；程序固定从 MeetingHelper 的 `models/` 读取。

### 两路声音只有一路有内容

先分别测试：

```bash
bash scripts/start.sh --source mic --asr sherpa --debug
```

停止后再测试：

```bash
bash scripts/start.sh --source system --asr sherpa --debug
```

两路单独都正常后，再使用 `--source both`。Sherpa 会为两路音频创建独立 stream。

## 13. 本地处理与隐私

- Sherpa 模型安装完成后，实时识别在本机完成；
- transcript、日志、Debug WAV、模型和缓存都保存在 MeetingHelper；
- Apple Speech 可能使用 Apple 在线语音服务；
- Hugging Face 模式的第三方依赖与缓存不由自动安装器管理；如果要求所有文件都留在项目目录，请使用 Sherpa；
- `src/python/query_transcript.py` 会把 transcript 发送到你配置的 API 地址，默认是本机 Ollama；
- 录制会议前应确认参与者同意，并遵守所在地法律和组织政策。
