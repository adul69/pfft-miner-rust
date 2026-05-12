#!/usr/bin/env bash
# PFFT GPU Miner setup for Vast.ai / any Ubuntu with NVIDIA driver + CUDA.
# Most Vast.ai GPU images already have nvidia-smi + CUDA toolkit installed.
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/adul69/pfft-miner-rust/main/setup-gpu.sh | bash
# Then:
#   nano ~/pfft-miner-rust/.env             # paste PRIVATE_KEY
#   cd ~/pfft-miner-rust
#   USE_GPU=1 ./venv/bin/python3 pfft_miner.py
#
# Or multi-wallet:
#   nano ~/pfft-miner-rust/wallets.txt
#   USE_GPU=1 ./venv/bin/python3 pfft_miner_multi.py

set -e

echo "=== PFFT GPU Miner setup ==="

# 0. Detect GPU
if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "ERROR: nvidia-smi not found. This script is for NVIDIA GPU instances."
    echo "On a CPU-only box, use setup.sh instead."
    exit 1
fi
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader

# 1. System deps
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    build-essential curl git python3 python3-pip python3-venv \
    python3-dev pkg-config libssl-dev

# 2. Ensure nvcc present (pycuda needs it at import-time for kernel build)
if ! command -v nvcc >/dev/null 2>&1; then
    # Try common CUDA install paths
    for p in /usr/local/cuda/bin /usr/local/cuda-12.4/bin /usr/local/cuda-12.2/bin /usr/local/cuda-11.8/bin; do
        if [ -x "$p/nvcc" ]; then
            echo "export PATH=$p:\$PATH" >> ~/.bashrc
            export PATH="$p:$PATH"
            break
        fi
    done
fi
if ! command -v nvcc >/dev/null 2>&1; then
    echo "WARN: nvcc not found. pycuda may fail to JIT kernels."
    echo "Try: apt install nvidia-cuda-toolkit, or add /usr/local/cuda/bin to PATH."
fi

# 3. Rust (fallback CPU solver, always build)
if ! command -v cargo >/dev/null 2>&1; then
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable --profile minimal
fi
source "$HOME/.cargo/env" 2>/dev/null || true

# 4. Clone / update repo
cd ~
if [ ! -d pfft-miner-rust ]; then
    git clone https://github.com/adul69/pfft-miner-rust.git
fi
cd pfft-miner-rust
git pull --quiet || true

# 5. Build Rust solver (for fallback / small-difficulty solves)
cargo build --release

# 6. Python venv + GPU deps
python3 -m venv venv
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet web3 eth-account pycryptodome numpy pycuda

# 7. .env template
[ -f .env ] || cp .env.example .env

# 8. Quick GPU benchmark (7 hex zeros = 28-bit, similar to current PFFT difficulty)
echo ""
echo "--- GPU benchmark (28-bit, same as current PFFT difficulty) ---"
./venv/bin/python3 gpu_solver.py \
    0000000000000000000000000000000000000000000000000000000000000001 7 \
    2>&1 | tail -12 || echo "(GPU test failed — check nvidia-smi / nvcc)"

echo ""
echo "=== DONE ==="
echo ""
echo "Next:"
echo "  1. Edit .env:    nano ~/pfft-miner-rust/.env  (paste PRIVATE_KEY)"
echo "  2. Fund wallet with ~0.002 ETH for gas"
echo "  3. Run with GPU:"
echo "       cd ~/pfft-miner-rust"
echo "       USE_GPU=1 ./venv/bin/python3 pfft_miner.py"
echo ""
echo "  Multi-wallet:"
echo "       nano ~/pfft-miner-rust/wallets.txt"
echo "       USE_GPU=1 ./venv/bin/python3 pfft_miner_multi.py"
