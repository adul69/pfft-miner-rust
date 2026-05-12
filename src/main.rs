// PFFT PoW solver — CPU multi-thread, keccak256
//
// Contract expects: keccak256(challenge[32] || nonce_as_uint256[32]) <= target
// target = (2^256 - 1) >> (hex_zeros * 4)
//
// Usage:
//   pfft-solver <challenge_hex_64> <hex_zeros>
// Output (on success, to stdout):
//   NONCE <decimal_nonce>
//   HASH  <hex_hash>
//   RATE  <mh_per_sec>
//   TIME  <secs>

use rayon::prelude::*;
use sha3::{Digest, Keccak256};
use std::env;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Instant;

fn target_from_zeros(hex_zeros: u32) -> [u8; 32] {
    // target = (2^256 - 1) >> (hex_zeros * 4)  == first (hex_zeros) nibbles must be zero
    let shift = (hex_zeros * 4) as usize;
    if shift >= 256 {
        return [0u8; 32];
    }
    // start from all 0xFF, then shift right by `shift` bits as a big-endian 256-bit int
    let mut t = [0xFFu8; 32];
    // shift: zero-out the top `shift` bits
    let full_bytes = shift / 8;
    let rem_bits = shift % 8;
    for i in 0..full_bytes {
        t[i] = 0;
    }
    if full_bytes < 32 && rem_bits > 0 {
        t[full_bytes] >>= rem_bits;
    }
    t
}

fn hash_le_target(hash: &[u8; 32], target: &[u8; 32]) -> bool {
    // both big-endian 256-bit; compare lexicographically
    for i in 0..32 {
        if hash[i] < target[i] {
            return true;
        }
        if hash[i] > target[i] {
            return false;
        }
    }
    true // equal
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 3 {
        eprintln!("usage: pfft-solver <challenge_hex_64> <hex_zeros> [num_threads]");
        std::process::exit(2);
    }
    let ch_hex = args[1].trim_start_matches("0x");
    let hex_zeros: u32 = args[2].parse().expect("hex_zeros must be integer");

    let challenge = hex::decode(ch_hex).expect("challenge must be hex");
    assert_eq!(challenge.len(), 32, "challenge must be 32 bytes");
    let mut ch = [0u8; 32];
    ch.copy_from_slice(&challenge);

    let target = target_from_zeros(hex_zeros);

    let threads = if args.len() > 3 {
        args[3].parse().unwrap_or(0)
    } else {
        0
    };
    if threads > 0 {
        rayon::ThreadPoolBuilder::new()
            .num_threads(threads)
            .build_global()
            .ok();
    }
    let n_threads = rayon::current_num_threads();

    eprintln!("[pfft-solver] threads={} hex_zeros={} target={}",
        n_threads, hex_zeros, hex::encode(&target));

    let found = Arc::new(AtomicBool::new(false));
    let solution_nonce = Arc::new(AtomicU64::new(0));
    let total_hashes = Arc::new(AtomicU64::new(0));

    let start = Instant::now();

    // Progress printer
    {
        let found = Arc::clone(&found);
        let total_hashes = Arc::clone(&total_hashes);
        let start = start;
        std::thread::spawn(move || {
            let mut last = 0u64;
            let mut last_t = Instant::now();
            while !found.load(Ordering::Relaxed) {
                std::thread::sleep(std::time::Duration::from_secs(2));
                let now = total_hashes.load(Ordering::Relaxed);
                let dt = last_t.elapsed().as_secs_f64();
                let delta = now.saturating_sub(last);
                let mhs = (delta as f64 / dt) / 1_000_000.0;
                let el = start.elapsed().as_secs_f64();
                eprintln!("[pfft-solver] {:>6.2} MH/s | {:>10} hashes | {:>5.0}s",
                    mhs, now, el);
                last = now;
                last_t = Instant::now();
            }
        });
    }

    // Split nonce space across threads. Each thread scans stride by n_threads.
    (0..n_threads).into_par_iter().for_each(|tid| {
        let mut local_buf = [0u8; 64];
        local_buf[0..32].copy_from_slice(&ch);
        let mut local_count: u64 = 0;
        let start_nonce = tid as u64;
        let stride = n_threads as u64;
        let mut nonce: u64 = start_nonce;

        while !found.load(Ordering::Relaxed) {
            // Pack nonce as 32-byte big-endian uint256 into local_buf[32..64]
            // Upper 24 bytes are zero; last 8 bytes = nonce big-endian
            // (already zero except last 8)
            for i in 0..24 {
                local_buf[32 + i] = 0;
            }
            local_buf[56..64].copy_from_slice(&nonce.to_be_bytes());

            let mut hasher = Keccak256::new();
            hasher.update(&local_buf);
            let out = hasher.finalize();
            let mut h = [0u8; 32];
            h.copy_from_slice(&out);

            if hash_le_target(&h, &target) {
                solution_nonce.store(nonce, Ordering::SeqCst);
                found.store(true, Ordering::SeqCst);
                let elapsed = start.elapsed().as_secs_f64();
                let total = total_hashes.fetch_add(local_count, Ordering::Relaxed) + local_count;
                let mhs = (total as f64 / elapsed) / 1_000_000.0;
                println!("NONCE {}", nonce);
                println!("HASH 0x{}", hex::encode(&h));
                println!("RATE {:.3}", mhs);
                println!("TIME {:.3}", elapsed);
                return;
            }

            nonce = nonce.wrapping_add(stride);
            local_count += 1;

            // batch-update global counter every 65536 hashes
            if local_count & 0xFFFF == 0 {
                total_hashes.fetch_add(0x10000, Ordering::Relaxed);
            }
        }
    });
}
