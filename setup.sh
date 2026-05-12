#!/usr/bin/env bash
# PFFT Miner one-liner setup for fresh Ubuntu/Debian VPS
# Usage:
#   curl -sSL https://raw.githubusercontent.com/adul69/pfft-miner-rust/main/setup.sh | bash
# Then edit .env to paste PRIVATE_KEY, and run:
#   cd ~/pfft-miner-rust && python3 pfft_miner.py

set -e

echo "=== PFFT Miner setup ==="

# 1. System deps
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    build-essential curl git python3 python3-pip python3-venv pkg-config libssl-dev

# 2. Rust
if ! command -v cargo >/dev/null 2>&1; then
    echo "--- installing Rust ---"
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable --profile minimal
fi
source "$HOME/.cargo/env"

# 3. Clone repo
cd ~
if [ ! -d pfft-miner-rust ]; then
    git clone https://github.com/adul69/pfft-miner-rust.git
fi
cd pfft-miner-rust
git pull --quiet || true

# 4. Build Rust solver
echo "--- building Rust solver (may take ~1 min) ---"
cargo build --release

# 5. Python venv + deps
python3 -m venv venv
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet web3 eth-account pycryptodome

# 6. .env template
if [ ! -f .env ]; then
    cp .env.example .env
fi

CORES=$(nproc)
echo ""
echo "=== DONE ==="
echo "CPU cores: $CORES"
echo ""
echo "Quick solver benchmark (12-bit, should be instant):"
./target/release/pfft-solver 0000000000000000000000000000000000000000000000000000000000000001 6 2>&1 | grep -E "RATE|TIME" || true
echo ""
echo "Next:"
echo "  1. Edit .env and paste PRIVATE_KEY (burner wallet)"
echo "     nano ~/pfft-miner-rust/.env"
echo "  2. Fund the wallet with ~0.002 ETH for gas"
echo "  3. Start mining:"
echo "     cd ~/pfft-miner-rust"
echo "     ./venv/bin/python3 pfft_miner.py"
echo ""
