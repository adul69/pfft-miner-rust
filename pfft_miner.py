#!/usr/bin/env python3
"""
PFFT Miner — Rust solver + Python RPC wrapper
Contract: 0xEFAd2Eab7172dDEbE5Ce7a41f5Ddf8fCcE4Ca0CB (Ethereum mainnet)

Flow per iteration:
  1. Read contract state (hex_zeros, totalMinted, wallet mintedByAddress)
  2. Fetch currentPowChallenge(wallet)
  3. Call ./target/release/pfft-solver <challenge> <hex_zeros>
  4. submit freeMint(nonce)
  5. Wait receipt, repeat
"""
import os
import sys
import time
import subprocess
from pathlib import Path

# ------------------------ dotenv (no external dep) -----------------------
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
PRIVATE_KEY = os.environ.get("PRIVATE_KEY", "")
GAS_LIMIT = int(os.environ.get("GAS_LIMIT", "250000"))
GAS_PRICE_MULT = float(os.environ.get("GAS_PRICE_MULT", "1.1"))
MAX_GAS_GWEI = float(os.environ.get("MAX_GAS_GWEI", "20"))  # pause if gas > this
PAUSE_BETWEEN_ROUNDS = int(os.environ.get("PAUSE_BETWEEN_ROUNDS", "3"))
THREADS = os.environ.get("SOLVER_THREADS", "")  # "" = auto
SOLVER = os.environ.get(
    "SOLVER_BIN",
    str(Path(__file__).parent / "target" / "release" / "pfft-solver"),
)

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

def run_solver(challenge_hex: str, hex_zeros: int):
    cmd = [SOLVER, challenge_hex, str(hex_zeros)]
    if THREADS:
        cmd.append(str(THREADS))
    print(f"  solver: {' '.join(cmd)}")
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    dt = time.time() - t0
    if proc.returncode != 0:
        print(f"  solver failed rc={proc.returncode}")
        print(proc.stderr)
        return None
    nonce = None
    for line in proc.stdout.splitlines():
        if line.startswith("NONCE "):
            nonce = int(line.split()[1])
        elif line.startswith("RATE "):
            print(f"  rate: {line.split()[1]} MH/s")
    if nonce is None:
        print("  solver output missing NONCE")
        print(proc.stdout)
        return None
    print(f"  solved in {dt:.2f}s, nonce={nonce}")
    return nonce

# ------------------------ main -------------------------------------------
def main():
    if not PRIVATE_KEY:
        print("ERROR: PRIVATE_KEY not set (edit .env)")
        sys.exit(1)
    if not Path(SOLVER).is_file():
        print(f"ERROR: solver binary not found at {SOLVER}")
        print("Run: cargo build --release")
        sys.exit(1)

    from web3 import Web3
    from eth_account import Account

    w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        print(f"ERROR: cannot connect to {RPC}")
        sys.exit(1)

    acct = Account.from_key(PRIVATE_KEY)
    contract = w3.eth.contract(address=Web3.to_checksum_address(CONTRACT), abi=ABI)

    print("=" * 62)
    print("  PFFT Miner (Rust solver)")
    print(f"  Wallet:   {acct.address}")
    print(f"  Contract: {CONTRACT}")
    print(f"  RPC:      {RPC}")
    print("=" * 62)

    bal_eth = w3.eth.get_balance(acct.address)
    print(f"  ETH balance: {w3.from_wei(bal_eth, 'ether')} ETH")
    if bal_eth == 0:
        print("  WARN: wallet has 0 ETH, fund it first.")

    round_no = 0
    while True:
        round_no += 1
        try:
            hex_zeros = contract.functions.currentPowHexZeros().call()
            total_minted = contract.functions.totalMinted().call()
            max_supply = contract.functions.MAX_SUPPLY().call()
            wallet_minted = contract.functions.mintedByAddress(acct.address).call()
            wallet_bal = contract.functions.balanceOf(acct.address).call()

            print("")
            print(f"[round {round_no}] hex_zeros={hex_zeros} ({hex_zeros*4}-bit)")
            print(f"  supply:  {human(total_minted)} / {human(max_supply)} PFFT "
                  f"({total_minted/max_supply*100:.2f}%)")
            print(f"  wallet:  minted={human(wallet_minted)} balance={human(wallet_bal)} PFFT")

            # wallet cap check: most PFFT contracts hard cap 10,000 per wallet
            if wallet_minted >= 10_000 * 10**18:
                print("  wallet cap reached. stopping.")
                break

            # gas price sanity
            gp = w3.eth.gas_price
            gp_gwei = gp / 1e9
            if gp_gwei > MAX_GAS_GWEI:
                print(f"  gas={gp_gwei:.1f} gwei > MAX_GAS_GWEI={MAX_GAS_GWEI}, sleeping 60s")
                time.sleep(60)
                continue
            print(f"  gas:     {gp_gwei:.2f} gwei")

            # fetch challenge
            ch = contract.functions.currentPowChallenge(acct.address).call()
            if isinstance(ch, bytes):
                ch_hex = ch.hex()
            else:
                ch_hex = ch[2:] if ch.startswith("0x") else ch
            print(f"  challenge: 0x{ch_hex}")

            # solve with Rust
            nonce_pow = run_solver(ch_hex, hex_zeros)
            if nonce_pow is None:
                time.sleep(5)
                continue

            # verify on-chain before spending gas
            try:
                ok = contract.functions.isValidPow(acct.address, nonce_pow).call()
            except Exception as e:
                print(f"  isValidPow call failed: {e}")
                ok = True  # proceed anyway
            if not ok:
                print("  isValidPow=false, challenge rotated. retrying.")
                continue

            # submit freeMint
            tx = contract.functions.freeMint(nonce_pow).build_transaction({
                "from": acct.address,
                "nonce": w3.eth.get_transaction_count(acct.address),
                "chainId": CHAIN_ID,
                "gas": GAS_LIMIT,
                "gasPrice": int(gp * GAS_PRICE_MULT),
            })
            signed = acct.sign_transaction(tx)
            txh = w3.eth.send_raw_transaction(signed.raw_transaction)
            txh_hex = txh.hex()
            print(f"  tx sent: https://etherscan.io/tx/0x{txh_hex}")

            rc = w3.eth.wait_for_transaction_receipt(txh, timeout=300)
            if rc.status == 1:
                print(f"  MINT OK block={rc.blockNumber} gas_used={rc.gasUsed}")
            else:
                print(f"  REVERTED gas_used={rc.gasUsed}")
                time.sleep(10)

            time.sleep(PAUSE_BETWEEN_ROUNDS)

        except KeyboardInterrupt:
            print("\n  stopped by user")
            break
        except Exception as e:
            print(f"  error: {e!r}")
            time.sleep(10)


if __name__ == "__main__":
    main()
