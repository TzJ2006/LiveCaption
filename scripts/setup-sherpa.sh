#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="$PROJECT_DIR/.build"
PYDEPS="$BUILD_DIR/pydeps"
MODEL_NAME="sherpa-onnx-streaming-paraformer-bilingual-zh-en"
MODEL_ROOT="$PROJECT_DIR/models"
MODEL_DIR="$MODEL_ROOT/$MODEL_NAME"
MODEL_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$MODEL_NAME.tar.bz2"
ARCHIVE="$MODEL_ROOT/$MODEL_NAME.tar.bz2"

mkdir -p "$PYDEPS" "$BUILD_DIR/pip-cache" "$BUILD_DIR/tmp" "$MODEL_ROOT"
export PIP_CACHE_DIR="$BUILD_DIR/pip-cache"
export PYTHONPYCACHEPREFIX="$BUILD_DIR/pycache"
export TMPDIR="$BUILD_DIR/tmp"

if ! PYTHONPATH="$PYDEPS" python3 -c 'import numpy, sherpa_onnx' 2>/dev/null; then
    echo "Installing Sherpa dependencies inside MeetingHelper..."
    python3 -m pip install --upgrade --target "$PYDEPS" numpy sherpa-onnx
fi

if [[ ! -s "$MODEL_DIR/encoder.int8.onnx" || \
      ! -s "$MODEL_DIR/decoder.int8.onnx" || \
      ! -s "$MODEL_DIR/tokens.txt" ]]; then
    echo "Downloading bilingual Sherpa model inside MeetingHelper..."
    curl --fail --location --retry 3 --continue-at - \
        --progress-bar --output "$ARCHIVE.part" "$MODEL_URL"
    mv "$ARCHIVE.part" "$ARCHIVE"
    tar -xjf "$ARCHIVE" -C "$MODEL_ROOT" \
        "$MODEL_NAME/encoder.int8.onnx" \
        "$MODEL_NAME/decoder.int8.onnx" \
        "$MODEL_NAME/tokens.txt"
    rm -f "$ARCHIVE"
fi

python3 "$PROJECT_DIR/src/python/sherpa_asr_worker.py" </dev/null
echo "Sherpa ready: $MODEL_DIR"
