# PFFT Miner (Rust solver + Python wrapper)

Fast PoW miner for **PFFT (Pow Free Fair Token)** on Ethereum mainnet.

- Rust CPU solver (multi-thread, keccak256 via `sha3` crate)
- Python wrapper handles RPC, challenge fetch, tx submission
- Contract: `0xEFAd2Eab7172dDEbE5Ce7a41f5Ddf8fCcE4Ca0CB`

## Why CPU not GPU?

Current difficulty is 28-bit (≈ 2^28 hashes per solve).
A 16-core CPU at ~300 MH/s solves in ~1 second.
Block time and gas are the real bottleneck, not mining.

If difficulty later climbs to 40-bit, revisit with CUDA.

## Setup

```bash
# 1. Install Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y

# 2. Build solver
source "$HOME/.cargo/env"
cargo build --release

# 3. Python deps
pip install web3 eth-account pycryptodome

# 4. Configure
cp .env.example .env
nano .env   # paste PRIVATE_KEY

# 5. Fund wallet with ETH for gas (~$1-2 covers 10+ mints)

# 6. Run
python3 pfft_miner.py
```

## Files

| File | Purpose |
|------|-------------|
| `src/main.rs` | Rust PoW solver — parallel keccak256 brute force |
| `pfft_miner.py` | Single-wallet driver |
| `pfft_miner_multi.py` | Multi-wallet driver (sequential, reads `wallets.txt`) |
| `Cargo.toml` | Rust build config (LTO, opt-level 3) |
| `setup.sh` | One-liner installer for fresh Ubuntu/Debian |

## Multi-wallet mode

Create `wallets.txt` (gitignored) — one private key per line:

```
0xaaa...111  # burner-1
0xbbb...222  # burner-2
0xccc...333  # burner-3
```

Then run:

```bash
./venv/bin/python3 pfft_miner_multi.py
```

Flow: iterates each wallet sequentially, mints until capped (10k PFFT),
then moves to the next. Skips wallets with 0 ETH or already capped.
Shows a pre-scan table + final summary.

You can also pass keys via env var (comma-separated):

```bash
WALLETS=0xaaa,0xbbb,0xccc ./venv/bin/python3 pfft_miner_multi.py
```

## Solver CLI (standalone)

```bash
./target/release/pfft-solver <challenge_hex_64> <hex_zeros> [threads]
```

Output:
```
NONCE 12345
HASH 0x00000...
RATE 285.4   # MH/s
TIME 1.23    # seconds
```

## Gas safety

- `MAX_GAS_GWEI=20` pauses mining when gas spikes
- `GAS_PRICE_MULT=1.1` — 10% above current for quick inclusion

## Stopping

- Auto-stops when `mintedByAddress(wallet) >= 10,000 PFFT` (per-wallet cap)
- `Ctrl+C` anytime
