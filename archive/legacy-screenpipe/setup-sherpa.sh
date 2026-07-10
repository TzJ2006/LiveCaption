#!/usr/bin/env bash
set -euo pipefail

# Meeting Helper — Sherpa-ONNX 安装脚本
# 安装 Python 依赖 + 下载中英双语流式模型

MODEL_NAME="sherpa-onnx-streaming-paraformer-bilingual-zh-en"
MODEL_DIR="$HOME/.meeting-helper/models/$MODEL_NAME"
MODEL_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$MODEL_NAME.tar.bz2"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ── 1. 检查 Python3 ────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    error "python3 未找到，请先安装 Python >= 3.8"
    exit 1
fi
info "Python3: $(python3 --version)"

# ── 2. 安装 Python 依赖 ────────────────────────────────────────────────────
info "检查 Python 依赖..."

install_if_missing() {
    local pkg="$1"
    local import_name="${2:-$1}"
    if python3 -c "import $import_name" &>/dev/null; then
        info "  $pkg 已安装"
    else
        info "  安装 $pkg..."
        pip3 install "$pkg"
    fi
}

install_if_missing "sherpa-onnx" "sherpa_onnx"
install_if_missing "sounddevice" "sounddevice"
install_if_missing "numpy" "numpy"

# PyObjC (macOS only)
if [[ "$(uname)" == "Darwin" ]]; then
    if python3 -c "import AppKit" &>/dev/null; then
        info "  PyObjC 已安装"
    else
        info "  安装 PyObjC..."
        pip3 install pyobjc-framework-Cocoa
    fi
fi

# ── 3. 下载模型 ────────────────────────────────────────────────────────────
if [ -f "$MODEL_DIR/tokens.txt" ] && \
   [ -f "$MODEL_DIR/encoder.int8.onnx" ] && \
   [ -f "$MODEL_DIR/decoder.int8.onnx" ]; then
    info "模型已存在: $MODEL_DIR"
else
    info "下载模型: $MODEL_NAME (~1GB)..."
    mkdir -p "$HOME/.meeting-helper/models"

    ARCHIVE="/tmp/$MODEL_NAME.tar.bz2"
    if [ ! -f "$ARCHIVE" ]; then
        curl -L --progress-bar -o "$ARCHIVE" "$MODEL_URL"
    fi

    info "解压模型..."
    tar -xjf "$ARCHIVE" -C "$HOME/.meeting-helper/models/"
    rm -f "$ARCHIVE"
    info "模型已安装: $MODEL_DIR"
fi

# ── 4. 验证 ────────────────────────────────────────────────────────────────
info "验证模型加载..."
python3 -c "
import sherpa_onnx
import os

model_dir = os.path.expanduser('$MODEL_DIR')
recognizer = sherpa_onnx.OnlineRecognizer.from_paraformer(
    encoder=os.path.join(model_dir, 'encoder.int8.onnx'),
    decoder=os.path.join(model_dir, 'decoder.int8.onnx'),
    tokens=os.path.join(model_dir, 'tokens.txt'),
    num_threads=2,
    sample_rate=16000,
    enable_endpoint_detection=True,
)
stream = recognizer.create_stream()
print('OK: Paraformer 模型加载成功')
" || {
    error "模型加载失败，请检查文件完整性"
    exit 1
}

# ── 5. 完成 ────────────────────────────────────────────────────────────────
echo ""
info "安装完成!"
echo ""
echo "  模型: $MODEL_DIR"
echo "  启动: bash scripts/start.sh"
echo ""
