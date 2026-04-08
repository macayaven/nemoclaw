# NemoClaw Performance Benchmarks

*Measured: 2026-03-22 on live deployment*
*Nemotron 120B: 94GB loaded, 100% GPU on DGX Spark GB10 Blackwell*

---

## Model Inference Latency

Each model tested 3 times with the same prompt ("Write one sentence about AI", max_tokens=50).
Run 1 includes cold start for models that weren't loaded.

### Nemotron 3 Super 120B (DGX Spark, direct Ollama)

| Run | Latency | Tokens | Tok/s | Notes |
|-----|---------|--------|-------|-------|
| 1 | 29,751 ms | 48 | 1.6 | Cold start (model loading into GPU) |
| 2 | 2,820 ms | 50 | 17.7 | Warm |
| 3 | 2,457 ms | 43 | 17.5 | Warm |

**Summary:** ~30s cold start, then **~2.5s / 17-18 tok/s** when warm. `OLLAMA_KEEP_ALIVE=-1` prevents unloading.

### Qwen3 Coder Next Q4_K_M (DGX Spark, direct Ollama) — *historical, model retired*

| Run | Latency | Tokens | Tok/s | Notes |
|-----|---------|--------|-------|-------|
| 1 | 590 ms | 0 | - | Model swap (Nemotron unloading, Qwen loading) |
| 2 | 15,244 ms | 39 | 2.6 | First real inference after load |
| 3 | 1,182 ms | 45 | 38.1 | Warm |

**Summary:** Historical benchmark. This model has been retired from the deployment in favor of Nemotron 120B as the sole Spark model, with Gemma 4 27B on the Mac for fast inference.

### Gemma4 27B (Mac Studio M4 Max, via Tailscale)

| Run | Latency | Tokens | Tok/s | Notes |
|-----|---------|--------|-------|-------|
| 1 | 6,064 ms | 50 | 8.2 | Cold start |
| 2 | 969 ms | 50 | 51.6 | Warm |
| 3 | 940 ms | 50 | 53.2 | Warm |

**Summary:** Sub-second when warm, **~50 tok/s**. Includes Tailscale network latency (~10ms). Best for quick questions where 120B is overkill.

### Nemotron 120B via inference.local (full sandbox chain)

| Run | Latency | Notes |
|-----|---------|-------|
| 1 | 38,084 ms | Cold start + sandbox proxy overhead |
| 2 | 3,307 ms | Warm |
| 3 | 3,267 ms | Warm |

**Summary:** ~800ms overhead from the sandbox chain (SSH tunnel + OpenShell proxy + credential injection) compared to direct Ollama.

---

## Overhead Analysis

| Path | Warm Latency | Overhead vs Direct |
|------|-------------|-------------------|
| Direct Ollama (localhost:11434) | ~2,500 ms | baseline |
| inference.local (sandbox → proxy → Ollama) | ~3,300 ms | +800 ms (~32%) |
| Mac via Tailscale (100.x.x.x:11435) | ~950 ms | N/A (different model) |
| Direct Mac Ollama (LAN) | ~950 ms | N/A (different model/machine) |

The sandbox overhead (~800ms) comes from:
- SSH tunnel from host to sandbox container
- OpenShell HTTPS proxy intercepting `inference.local`
- Credential injection and request rewriting
- Response forwarding back through the chain

---

## GPU Memory Usage

| Model | VRAM | Processor | Context Window |
|-------|------|-----------|---------------|
| nemotron-3-super:120b | 94 GB | 100% GPU | 262,144 tokens |

The DGX Spark has 128GB UMA. With Nemotron loaded (94GB), ~34GB remains for system and other processes.

---

## Key Takeaways

1. **Cold start is the biggest bottleneck** — 30s for Nemotron, 6s for Gemma4 27B. Use `OLLAMA_KEEP_ALIVE=-1` to prevent unloading.

2. **Warm inference is fast** — 17-18 tok/s for 120B parameters is excellent for local inference. Gemma4 27B on M4 Max achieves 50+ tok/s.

3. **Sandbox overhead is acceptable** — 800ms extra for the full security chain (isolation, policy enforcement, credential injection) is a reasonable price for the security guarantees.

5. **Tailscale adds negligible latency** — <10ms on LAN. The Mac's fast response time (950ms) makes it viable for interactive use even over the network.
