#!/usr/bin/env python3
"""
PFFT GPU solver — CUDA keccak256 brute-force
CLI drop-in compatible with Rust solver:
    python3 gpu_solver.py <challenge_hex_64> <hex_zeros>
Output:
    NONCE <n>
    HASH 0x<hex>
    RATE <mh_per_sec>
    TIME <seconds>

Requires: pycuda, numpy, pycryptodome, NVIDIA driver + CUDA toolkit.
"""
import os, sys, time, struct
import numpy as np

CUDA_KERNEL = r'''
#include <stdint.h>

typedef unsigned long long u64;

__device__ __forceinline__ u64 rotl64(u64 x, int n) {
    return (n == 0) ? x : ((x << n) | (x >> (64 - n)));
}

__device__ __forceinline__ u64 bswap64(u64 x) {
    return ((x & 0xff00000000000000ULL) >> 56)
         | ((x & 0x00ff000000000000ULL) >> 40)
         | ((x & 0x0000ff0000000000ULL) >> 24)
         | ((x & 0x000000ff00000000ULL) >>  8)
         | ((x & 0x00000000ff000000ULL) <<  8)
         | ((x & 0x0000000000ff0000ULL) << 24)
         | ((x & 0x000000000000ff00ULL) << 40)
         | ((x & 0x00000000000000ffULL) << 56);
}

__constant__ u64 RC[24] = {
    0x0000000000000001ULL,0x0000000000008082ULL,0x800000000000808AULL,0x8000000080008000ULL,
    0x000000000000808BULL,0x0000000080000001ULL,0x8000000080008081ULL,0x8000000000008009ULL,
    0x000000000000008AULL,0x0000000000000088ULL,0x0000000080008009ULL,0x000000008000000AULL,
    0x000000008000808BULL,0x800000000000008BULL,0x8000000000008089ULL,0x8000000000008003ULL,
    0x8000000000008002ULL,0x8000000000000080ULL,0x000000000000800AULL,0x800000008000000AULL,
    0x8000000080008081ULL,0x8000000000008080ULL,0x0000000080000001ULL,0x8000000080008008ULL
};

__constant__ int R[25] = {
     0,  1, 62, 28, 27,
    36, 44,  6, 55, 20,
     3, 10, 43, 25, 39,
    41, 45, 15, 21,  8,
    18,  2, 61, 56, 14
};

extern "C" __global__ void solve(
    const u64 *ch_words,   // 4 u64, LE packing of challenge bytes[0..32]
    const u64 *target_be,  // 4 u64, BE top-to-bottom
    u64 start_nonce,
    u64 nonces_per_thread,
    u64 *result            // [found_flag, nonce]
) {
    u64 tid = (u64)blockIdx.x * blockDim.x + threadIdx.x;
    u64 base = start_nonce + tid * nonces_per_thread;

    for (u64 i = 0; i < nonces_per_thread; i++) {
        if (atomicAdd((int*)&result[0], 0) != 0) return;
        u64 n = base + i;

        u64 s[25];
        #pragma unroll
        for (int k = 0; k < 25; k++) s[k] = 0;

        // Absorb 64 bytes: challenge[32] || nonce_be[32]
        s[0] = ch_words[0];
        s[1] = ch_words[1];
        s[2] = ch_words[2];
        s[3] = ch_words[3];
        // nonce is uint256 BE; since nonce fits in u64, bytes[32..56] zero,
        // bytes[56..64] = big-endian u64 of n → LE-load into s[7] = bswap64(n)
        s[7] = bswap64(n);

        // keccak256 pad: byte 64 = 0x01, byte 135 = 0x80
        s[8]  = 0x0000000000000001ULL;
        s[16] = 0x8000000000000000ULL;

        // keccak-f[1600], 24 rounds
        for (int r = 0; r < 24; r++) {
            u64 C[5], D[5], tp[25];
            #pragma unroll
            for (int x = 0; x < 5; x++)
                C[x] = s[x] ^ s[x+5] ^ s[x+10] ^ s[x+15] ^ s[x+20];
            #pragma unroll
            for (int x = 0; x < 5; x++) {
                D[x] = C[(x+4)%5] ^ rotl64(C[(x+1)%5], 1);
                #pragma unroll
                for (int y = 0; y < 5; y++) s[y*5+x] ^= D[x];
            }
            #pragma unroll
            for (int x = 0; x < 5; x++)
                #pragma unroll
                for (int y = 0; y < 5; y++) {
                    int rr = R[y*5+x];
                    tp[((2*x + 3*y) % 5)*5 + y] = rotl64(s[y*5+x], rr);
                }
            #pragma unroll
            for (int y = 0; y < 5; y++)
                #pragma unroll
                for (int x = 0; x < 5; x++)
                    s[y*5+x] = tp[y*5+x] ^ ((~tp[y*5+((x+1)%5)]) & tp[y*5+((x+2)%5)]);
            s[0] ^= RC[r];
        }

        // Compare hash (BE) to target (BE)
        u64 h0 = bswap64(s[0]);
        if (h0 > target_be[0]) continue;
        if (h0 < target_be[0]) { /* win */ }
        else {
            u64 h1 = bswap64(s[1]);
            if (h1 > target_be[1]) continue;
            if (h1 == target_be[1]) {
                u64 h2 = bswap64(s[2]);
                if (h2 > target_be[2]) continue;
                if (h2 == target_be[2]) {
                    u64 h3 = bswap64(s[3]);
                    if (h3 > target_be[3]) continue;
                }
            }
        }

        if (atomicCAS((int*)&result[0], 0, 1) == 0) {
            result[1] = n;
        }
        return;
    }
}
'''


def target_bytes(hex_zeros: int) -> bytes:
    """(2^256 - 1) >> (hex_zeros * 4), as 32 BE bytes."""
    if hex_zeros >= 64:
        return b"\x00" * 32
    full = (1 << 256) - 1
    t = full >> (hex_zeros * 4)
    return t.to_bytes(32, "big")


def verify(challenge: bytes, nonce: int, target: bytes) -> tuple:
    from Crypto.Hash import keccak
    data = challenge + nonce.to_bytes(32, "big")
    h = keccak.new(digest_bits=256, data=data).digest()
    return (h, h <= target)


def main():
    if len(sys.argv) < 3:
        print("usage: gpu_solver.py <challenge_hex_64> <hex_zeros>", file=sys.stderr)
        sys.exit(2)
    ch_hex = sys.argv[1].removeprefix("0x")
    hex_zeros = int(sys.argv[2])
    challenge = bytes.fromhex(ch_hex)
    assert len(challenge) == 32, "challenge must be 32 bytes"
    target = target_bytes(hex_zeros)

    import pycuda.autoinit  # noqa: F401
    import pycuda.driver as cuda
    from pycuda.compiler import SourceModule

    dev = cuda.Device(int(os.environ.get("CUDA_DEVICE", "0")))
    sm_count = dev.get_attribute(cuda.device_attribute.MULTIPROCESSOR_COUNT)
    print(f"[gpu_solver] GPU: {dev.name()} | SMs: {sm_count} | hex_zeros: {hex_zeros}",
          file=sys.stderr)

    mod = SourceModule(CUDA_KERNEL, no_extern_c=True, options=["-O3"])
    kernel = mod.get_function("solve")

    # challenge → 4 u64 LE
    ch_words = np.frombuffer(challenge, dtype="<u8").copy()
    # target → 4 u64 BE
    tgt_words = np.array(struct.unpack(">4Q", target), dtype=np.uint64)

    BLOCK = 256
    GRID = sm_count * 32
    NPT = np.uint64(int(os.environ.get("GPU_NPT", "2048")))
    BATCH = GRID * BLOCK * int(NPT)
    print(f"[gpu_solver] batch={BATCH:,} per-launch | grid={GRID} block={BLOCK} npt={NPT}",
          file=sys.stderr)

    d_ch = cuda.mem_alloc(ch_words.nbytes)
    d_tgt = cuda.mem_alloc(tgt_words.nbytes)
    d_res = cuda.mem_alloc(16)
    cuda.memcpy_htod(d_ch, ch_words)
    cuda.memcpy_htod(d_tgt, tgt_words)

    start_nonce = int.from_bytes(os.urandom(6), "big")  # 48-bit random start
    total = 0
    t0 = time.time()
    last_report = t0

    while True:
        res = np.zeros(2, dtype=np.uint64)
        cuda.memcpy_htod(d_res, res)
        kernel(d_ch, d_tgt,
               np.uint64(start_nonce), NPT,
               d_res,
               block=(BLOCK, 1, 1), grid=(GRID, 1))
        cuda.memcpy_dtoh(res, d_res)
        total += BATCH

        if res[0] != 0:
            nonce = int(res[1])
            h, ok = verify(challenge, nonce, target)
            elapsed = time.time() - t0
            rate = (total / elapsed) / 1e6 if elapsed > 0 else 0
            if not ok:
                print(f"[gpu_solver] VERIFY FAIL nonce={nonce} hash={h.hex()} — kernel bug",
                      file=sys.stderr)
                sys.exit(1)
            print(f"NONCE {nonce}")
            print(f"HASH 0x{h.hex()}")
            print(f"RATE {rate:.3f}")
            print(f"TIME {elapsed:.3f}")
            return

        start_nonce += BATCH
        now = time.time()
        if now - last_report >= 2.0:
            el = now - t0
            rate = (total / el) / 1e6
            print(f"[gpu_solver] {rate:>7.1f} MH/s | {total/1e6:>8.0f}M hashes | {el:>4.1f}s",
                  file=sys.stderr)
            last_report = now


if __name__ == "__main__":
    main()
