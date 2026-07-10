#!/usr/bin/env bash
set -euo pipefail

# Meeting Helper — 预下载所有 ASR 模型
# 用法:
#   bash scripts/download-models.sh           # 下载全部模型
#   bash scripts/download-models.sh zipformer paraformer whisper  # 只下载指定模型

MODELS_DIR="$HOME/.meeting-helper/models"
SHERPA_BASE_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }
header(){ echo -e "\n${BLUE}══════════════════════════════════════════${NC}"; echo -e "${BLUE}  $1${NC}"; echo -e "${BLUE}══════════════════════════════════════════${NC}"; }

ALL_MODELS=(zipformer paraformer whisper qwen3-asr moonshine voxtral)

if [ $# -gt 0 ]; then
    SELECTED=("$@")
else
    SELECTED=("${ALL_MODELS[@]}")
fi

mkdir -p "$MODELS_DIR"

success=0
failed=0
skipped=0

# ── Sherpa-ONNX 模型下载（zipformer / paraformer）──────────────────────────

download_sherpa_model() {
    local name="$1"
    local model_dir="$MODELS_DIR/$name"
    local archive="/tmp/$name.tar.bz2"

    if [ -d "$model_dir" ] && [ -f "$model_dir/tokens.txt" ]; then
        info "$name 已存在，跳过"
        skipped=$((skipped + 1))
        return 0
    fi

    info "下载 $name ..."
    curl -L --progress-bar -o "$archive" "$SHERPA_BASE_URL/$name.tar.bz2"
    info "解压中..."
    tar -xjf "$archive" -C "$MODELS_DIR/"
    rm -f "$archive"

    if [ -d "$model_dir" ] && [ -f "$model_dir/tokens.txt" ]; then
        info "$name 下载完成"
        success=$((success + 1))
    else
        error "$name 下载失败"
        failed=$((failed + 1))
    fi
}

# ── 各模型下载函数 ─────────────────────────────────────────────────────────

download_zipformer() {
    header "Zipformer (Sherpa-ONNX 流式, 中英文)"
    download_sherpa_model "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20"
}

download_paraformer() {
    header "Paraformer (Sherpa-ONNX 流式, 中英文)"
    download_sherpa_model "sherpa-onnx-streaming-paraformer-bilingual-zh-en"
}

download_whisper() {
    header "MLX-Whisper large-v3-turbo (~1.6GB, Apple Silicon Metal)"
    if ! python3 -c "import mlx_whisper" &>/dev/null; then
        warn "mlx-whisper 未安装，先安装..."
        pip3 install mlx-whisper || { error "mlx-whisper 安装失败"; failed=$((failed + 1)); return 1; }
    fi

    python3 -c "
import mlx_whisper, numpy as np, os, sys

model_size = os.environ.get('WHISPER_MODEL_SIZE', 'large-v3-turbo')
repo = f'mlx-community/whisper-{model_size}'
print(f'下载 {repo} ...')
try:
    mlx_whisper.transcribe(np.zeros(16000, dtype=np.float32), path_or_hf_repo=repo)
    print(f'✅ {repo} 下载完成')
except Exception as e:
    print(f'❌ 下载失败: {e}', file=sys.stderr)
    sys.exit(1)
" && success=$((success + 1)) || failed=$((failed + 1))
}

download_qwen3_asr() {
    header "Qwen3-ASR-0.6B (~1.2GB)"
    if ! python3 -c "import qwen_asr" &>/dev/null; then
        warn "qwen-asr 未安装，先安装..."
        pip3 install qwen-asr || { error "qwen-asr 安装失败"; failed=$((failed + 1)); return 1; }
    fi

    python3 -c "
from qwen_asr import Qwen3ASRModel
import sys

model_name = 'Qwen/Qwen3-ASR-0.6B'
print(f'下载 {model_name} ...')
try:
    model = Qwen3ASRModel.from_pretrained(model_name, max_inference_batch_size=1, max_new_tokens=64)
    print(f'✅ {model_name} 下载完成')
except Exception as e:
    print(f'❌ 下载失败: {e}', file=sys.stderr)
    sys.exit(1)
" && success=$((success + 1)) || failed=$((failed + 1))
}

download_moonshine() {
    header "Moonshine v2 (MLX, 事件驱动流式)"
    if ! python3 -c "import moonshine_voice" &>/dev/null; then
        warn "moonshine-voice 未安装，先安装..."
        pip3 install moonshine-voice || { error "moonshine-voice 安装失败"; failed=$((failed + 1)); return 1; }
    fi

    python3 -c "
from moonshine_voice import get_model_for_language
import sys

print('下载 moonshine 模型 ...')
try:
    model_path, model_arch = get_model_for_language('en')
    print(f'✅ moonshine 模型下载完成: {model_path}')
except Exception as e:
    print(f'❌ 下载失败: {e}', file=sys.stderr)
    sys.exit(1)
" && success=$((success + 1)) || failed=$((failed + 1))
}

download_voxtral() {
    header "Voxtral-Mini-4B-Realtime 4bit (~3GB)"
    if ! python3 -c "import voxmlx" &>/dev/null; then
        warn "voxmlx 未安装，先安装..."
        pip3 install voxmlx || { error "voxmlx 安装失败"; failed=$((failed + 1)); return 1; }
    fi

    python3 -c "
from voxmlx import load_model
import sys

model_path = 'mlx-community/Voxtral-Mini-4B-Realtime-2602-4bit'
print(f'下载 {model_path} (~3GB) ...')
try:
    model, sp, _ = load_model(model_path)
    print(f'✅ {model_path} 下载完成')
except Exception as e:
    print(f'❌ 下载失败: {e}', file=sys.stderr)
    sys.exit(1)
" && success=$((success + 1)) || failed=$((failed + 1))
}

# ── 主逻辑 ─────────────────────────────────────────────────────────────────

echo ""
info "开始下载模型: ${SELECTED[*]}"
echo ""

for model in "${SELECTED[@]}"; do
    case "$model" in
        zipformer)  download_zipformer ;;
        paraformer) download_paraformer ;;
        whisper)    download_whisper ;;
        qwen3-asr)  download_qwen3_asr ;;
        moonshine)  download_moonshine ;;
        voxtral)    download_voxtral ;;
        *)
            error "未知模型: $model"
            echo "  可用: ${ALL_MODELS[*]}"
            failed=$((failed + 1))
            ;;
    esac
done

# ── 汇总 ──────────────────────────────────────────────────────────────────

header "下载汇总"
info "成功: $success  跳过: $skipped  失败: $failed"

if [ "$failed" -gt 0 ]; then
    warn "部分模型下载失败，可单独重试: bash scripts/download-models.sh <模型名>"
    exit 1
fi

info "全部完成!"
