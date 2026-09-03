#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="$PROJECT_DIR/.build"
ENV_DIR="$BUILD_DIR/stream-env"
# ponytail: same probe as stream_python() in win_host.py -- Windows venvs are Scripts/python.exe,
# and hardcoding bin/python made this script rm -rf and rebuild the venv on every single run.
venv_python() {
    for candidate in "$ENV_DIR/bin/python" "$ENV_DIR/Scripts/python.exe"; do
        [[ -x "$candidate" ]] && { echo "$candidate"; return; }
    done
    echo "$ENV_DIR/bin/python"  # does not exist yet; the [[ -x ]] guards below handle that
}
PY="$(venv_python)"

mkdir -p "$BUILD_DIR/pip-cache" "$BUILD_DIR/tmp"
export PIP_CACHE_DIR="$BUILD_DIR/pip-cache"
export PYTHONPYCACHEPREFIX="$BUILD_DIR/pycache"
export TMPDIR="$BUILD_DIR/tmp"

if ! python3 -c 'import torch' 2>/dev/null; then
    echo "The streaming worker needs torch, and python3 cannot import it."
    echo "Install it first (see requirements.txt for the CUDA index Windows needs):"
    echo "    python3 -m pip install torch"
    exit 1
fi

# ponytail: the venv borrows torch from whichever python3 built it, so switching conda environments
# can leave it pointing at packages that are no longer there. Rebuild only when it is actually
# broken -- hopping between environments should not reinstall on every start.
if [[ -x "$PY" ]] && ! "$PY" -c 'import torch, transformers' 2>/dev/null; then
    echo "Rebuilding the streaming environment (its base interpreter no longer provides torch)..."
    rm -rf "$ENV_DIR"
fi

# ponytail: a venv of its own rather than .build/pydeps -- qwen-asr pins transformers==4.57.6 and
# the streaming worker needs >= 5.13, so no single import path serves both. --system-site-packages
# borrows the already-installed torch (multi-GB) instead of downloading a second copy.
if [[ ! -x "$PY" ]]; then
    echo "Creating the streaming ASR environment inside LiveCaption..."
    python3 -m venv --system-site-packages "$ENV_DIR"
    PY="$(venv_python)"  # only now can we tell whether this venv is bin/ or Scripts/
fi

if ! "$PY" -c 'import transformers as t; v=[int(p) for p in t.__version__.split(".")[:2]]; raise SystemExit(0 if v >= [5, 13] else 1)' 2>/dev/null; then
    echo "Installing transformers >= 5.13 for the streaming worker..."
    "$PY" -m pip install --quiet --upgrade "transformers>=5.13"
fi

"$PY" - <<'PY'
import torch, transformers
gpu = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu only"
print(f"Streaming env ready: transformers {transformers.__version__}, torch {torch.__version__}, {gpu}")
PY
