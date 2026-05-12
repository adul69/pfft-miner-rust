#!/usr/bin/env python3
"""
PFFT Multi-Wallet Miner — iterate through multiple burner wallets

Reads wallets.txt (one private key per line, optional label after #):
    0xabc...def  # wallet-1
    0x123...456  # wallet-2

Or WALLETS env var (comma-separated private keys).

Flow:
  for each wallet:
    if mintedByAddress(wallet) >= 10000 PFFT: skip
    else: mine until capped, then next wallet
"""
import os
import sys
import time
import subprocess
from pathlib import Path

# ------------------------ dotenv -----------------------------------------
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            k, v = _line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# ------------------------ config -----------------------------------------
CONTRACT = "0xEFAd2Eab7172dDEbE5Ce7a41f5Ddf8fCcE4Ca0CB"
CHAIN_ID = 1
RPC = os.environ.get("ETH_RPC", "https://ethereum-rpc.publicnode.com")
WALLETS_FILE = os.environ.get("WALLETS_FILE", str(Path(__file__).parent / "wallets.txt"))
GAS_LIMIT = int(os.environ.get("GAS_LIMIT", "250000"))
GAS_PRICE_MULT = float(os.environ.get("GAS_PRICE_MULT", "1.1"))
MAX_GAS_GWEI = float(os.environ.get("MAX_GAS_GWEI", "20"))
PAUSE_BETWEEN_ROUNDS = int(os.environ.get("PAUSE_BETWEEN_ROUNDS", "3"))
THREADS = os.environ.get("SOLVER_THREADS", "")
USE_GPU = os.environ.get("USE_GPU", "0") in ("1", "true", "yes")
SOLVER = os.environ.get(
    "SOLVER_BIN",
    str(Path(__file__).parent / "target" / "release" / "pfft-solver"),
)
GPU_SCRIPT = str(Path(__file__).parent / "gpu_solver.py")
WALLET_CAP = 10_000 * 10**18

ABI = [
    {"inputs":[],"name":"currentPowHexZeros","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"totalMinted","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"MAX_SUPPLY","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"user","type":"address"}],"name":"currentPowChallenge","outputs":[{"type":"bytes32"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"user","type":"address"},{"name":"powNonce","type":"uint256"}],"name":"isValidPow","outputs":[{"type":"bool"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"powNonce","type":"uint256"}],"name":"freeMint","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"user","type":"address"}],"name":"mintedByAddress","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
]

# ------------------------ helpers ----------------------------------------
def human(n, d=18):
    return f"{n/10**d:,.2f}"

def load_wallets():
    """Load keys from wallets.txt or WALLETS env var. Returns list[(pk, label)]."""
    out = []
    env = os.environ.get("WALLETS", "").strip()
    if env:
        for i, k in enumerate(env.split(",")):
            k = k.strip()
            if k:
                out.append((k, f"env-{i+1}"))
        return out
    p = Path(WALLETS_FILE)
    if not p.exists():
        print(f"ERROR: {p} not found. Create wallets.txt or set WALLETS env.")
        sys.exit(1)
    for i, raw in enumerate(p.read_text().splitlines(), 1):
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        label = f"w{i}"
        if "#" in raw:
            key, comment = raw.split("#", 1)
            raw = key.strip()
            label = comment.strip() or label
        if not raw.startswith("0x"):
            raw = "0x" + raw
        out.append((raw, label))
    return out

def run_solver(challenge_hex: str, hex_zeros: int):
    if USE_GPU:
        py = os.environ.get("PYTHON_BIN", sys.executable)
        cmd = [py, GPU_SCRIPT, challenge_hex, str(hex_zeros)]
    else:
        cmd = [SOLVER, challenge_hex, str(hex_zeros)]
        if THREADS:
            cmd.append(str(THREADS))
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    dt = time.time() - t0
    if proc.returncode != 0:
        print(f"  solver failed rc={proc.returncode}: {proc.stderr}")
        return None
    nonce = None
    rate = "?"
    for line in proc.stdout.splitlines():
        if line.startswith("NONCE "):
            nonce = int(line.split()[1])
        elif line.startswith("RATE "):
            rate = line.split()[1]
    if nonce is None:
        print(f"  solver no NONCE: {proc.stdout}")
        return None
    print(f"  solved {dt:.1f}s @ {rate} MH/s ({'GPU' if USE_GPU else 'CPU'}) nonce={nonce}")
    return nonce

def mine_wallet(w3, acct, label, contract):
    """Mine one wallet until capped or broken. Returns dict stats."""
    stats = {"mints": 0, "reverts": 0, "errors": 0, "start": time.time()}
    print("")
    print(f"{'=' * 62}")
    print(f"  wallet: {label} — {acct.address}")
    print(f"{'=' * 62}")

    bal_eth = w3.eth.get_balance(acct.address)
    bal_eth_fmt = w3.from_wei(bal_eth, 'ether')
    wallet_minted = contract.functions.mintedByAddress(acct.address).call()
    print(f"  eth={bal_eth_fmt} | already minted={human(wallet_minted)} PFFT")

    if wallet_minted >= WALLET_CAP:
        print("  already capped, skipping")
        return stats
    if bal_eth == 0:
        print("  0 ETH for gas, skipping")
        stats["errors"] = -1
        return stats

    round_no = 0
    while True:
        round_no += 1
        try:
            hex_zeros = contract.functions.currentPowHexZeros().call()
            wallet_minted = contract.functions.mintedByAddress(acct.address).call()
            if wallet_minted >= WALLET_CAP:
                print(f"  [{label}] CAP reached {human(wallet_minted)} PFFT")
                break

            gp = w3.eth.gas_price
            gp_gwei = gp / 1e9
            if gp_gwei > MAX_GAS_GWEI:
                print(f"  gas={gp_gwei:.1f} gwei > {MAX_GAS_GWEI}, sleeping 60s")
                time.sleep(60)
                continue

            bal_eth = w3.eth.get_balance(acct.address)
            if bal_eth < w3.to_wei(0.0001, "ether"):
                print(f"  [{label}] ETH too low ({w3.from_wei(bal_eth,'ether')}), stopping")
                break

            ch = contract.functions.currentPowChallenge(acct.address).call()
            ch_hex = ch.hex() if isinstance(ch, bytes) else (ch[2:] if ch.startswith("0x") else ch)

            print(f"  [{label}] round {round_no} | bits={hex_zeros*4} | gas={gp_gwei:.1f} gwei | minted={human(wallet_minted)}")
            nonce_pow = run_solver(ch_hex, hex_zeros)
            if nonce_pow is None:
                stats["errors"] += 1
                time.sleep(5)
                continue

            try:
                ok = contract.functions.isValidPow(acct.address, nonce_pow).call()
            except Exception:
                ok = True
            if not ok:
                print("  isValidPow=false, rotated, retry")
                continue

            tx = contract.functions.freeMint(nonce_pow).build_transaction({
                "from": acct.address,
                "nonce": w3.eth.get_transaction_count(acct.address),
                "chainId": CHAIN_ID,
                "gas": GAS_LIMIT,
                "gasPrice": int(gp * GAS_PRICE_MULT),
            })
            signed = acct.sign_transaction(tx)
            txh = w3.eth.send_raw_transaction(signed.raw_transaction)
            print(f"  tx: https://etherscan.io/tx/0x{txh.hex()}")
            rc = w3.eth.wait_for_transaction_receipt(txh, timeout=300)
            if rc.status == 1:
                stats["mints"] += 1
                print(f"  MINT OK block={rc.blockNumber} total_mints={stats['mints']}")
            else:
                stats["reverts"] += 1
                print(f"  REVERTED gas_used={rc.gasUsed}")
                time.sleep(10)

            time.sleep(PAUSE_BETWEEN_ROUNDS)

        except KeyboardInterrupt:
            print(f"\n  [{label}] user stop")
            raise
        except Exception as e:
            stats["errors"] += 1
            print(f"  error: {e!r}")
            time.sleep(10)
            if stats["errors"] >= 5:
                print(f"  [{label}] too many errors, skip")
                break
    stats["duration"] = time.time() - stats["start"]
    return stats


def main():
    from web3 import Web3
    from eth_account import Account

    wallets = load_wallets()
    if not wallets:
        print("ERROR: no wallets loaded")
        sys.exit(1)
    if not Path(SOLVER).is_file():
        print(f"ERROR: solver missing at {SOLVER} — run `cargo build --release`")
        sys.exit(1)

    w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        print(f"ERROR: cannot connect {RPC}")
        sys.exit(1)
    contract = w3.eth.contract(address=Web3.to_checksum_address(CONTRACT), abi=ABI)

    print("=" * 62)
    print(f"  PFFT Multi-Wallet Miner — {len(wallets)} wallet(s)")
    print(f"  RPC: {RPC}")
    print("=" * 62)

    # Pre-scan: show all wallets + balances
    print("\n  Pre-scan:")
    active = []
    for pk, label in wallets:
        try:
            acct = Account.from_key(pk)
            bal = w3.eth.get_balance(acct.address)
            minted = contract.functions.mintedByAddress(acct.address).call()
            capped = minted >= WALLET_CAP
            status = "CAPPED" if capped else ("NO_ETH" if bal == 0 else "READY")
            print(f"    {label:12s} {acct.address} | {w3.from_wei(bal,'ether')} ETH | {human(minted)} PFFT | {status}")
            if status == "READY":
                active.append((acct, label))
        except Exception as e:
            print(f"    {label}: bad key — {e}")

    if not active:
        print("\n  no active wallets to mine")
        sys.exit(0)

    print(f"\n  Will mine {len(active)} wallet(s) sequentially.")

    all_stats = []
    try:
        for acct, label in active:
            s = mine_wallet(w3, acct, label, contract)
            s["label"] = label
            s["address"] = acct.address
            all_stats.append(s)
    except KeyboardInterrupt:
        print("\n  stopping all")

    # Summary
    print("\n" + "=" * 62)
    print("  SUMMARY")
    print("=" * 62)
    total_mints = 0
    for s in all_stats:
        total_mints += s.get("mints", 0)
        print(f"  {s['label']:12s} mints={s.get('mints',0)} reverts={s.get('reverts',0)} "
              f"errors={s.get('errors',0)} time={s.get('duration',0):.0f}s")
    print(f"\n  TOTAL MINTS: {total_mints}")


if __name__ == "__main__":
    main()
