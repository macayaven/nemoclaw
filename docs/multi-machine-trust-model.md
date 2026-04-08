# Multi-Machine Trust Model

How NemoClaw secures cross-machine inference between the DGX Spark and Mac Studio.

## Architecture

```
+------------------+          Tailscale          +------------------+
|   DGX Spark      |       (encrypted mesh)      |   Mac Studio     |
|                  |  =========================>  |                  |
|  Sandbox         |                              |  Ollama          |
|  (agent code)    |                              |  (gemma-4:27b)   |
|       |          |                              |    :11434        |
|       v          |                              |       ^          |
|  inference.local |                              |       |          |
|       |          |                              |  TCP forwarder   |
|       v          |                              |  0.0.0.0:11435   |
|  OpenShell GW    | --- mac-ollama provider ---> |                  |
|  (provider reg)  |     100.116.228.36:11435     +------------------+
+------------------+
```

## Trust Boundaries

### What Tailscale provides
- **Encrypted transport**: All traffic between Spark and Mac is WireGuard-encrypted.
- **Authenticated peers**: Only devices in the same tailnet can communicate.  New devices require admin approval.
- **ACL enforcement**: Tailscale ACLs can restrict which peers can reach which ports.

### What OpenShell provides
- **Provider registration**: The `mac-ollama` provider is registered at the gateway level.  Sandboxes don't know the Mac's IP — they call `inference.local` and the gateway routes to the configured provider.
- **Credential routing**: Cloud API keys (Anthropic, Gemini) are held at the gateway and never injected into sandboxes.
- **Audit logging**: All inference requests are logged in the OpenShell audit log.

### What is NOT enforced
- **Per-sandbox provider ACLs on cross-machine paths**: Any sandbox with the `mac-ollama` provider configured can reach the Mac's Ollama endpoint.  There is no per-sandbox restriction at the Tailscale or OpenShell layer (all sandboxes share the same provider registration).
- **Mac-side request authentication**: Ollama does not authenticate incoming requests.  Any client that can reach port 11435 can submit inference requests.
- **Rate limiting**: Neither Tailscale nor the TCP forwarder implements rate limiting.  A runaway sandbox could saturate the Mac's GPU.

## Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Any sandbox reaches Mac Ollama | Medium | OpenShell provider registration limits which sandboxes can route to `mac-ollama`.  Tailscale ACLs can further restrict access. |
| Unauthenticated Ollama access | Low | Ollama is only reachable via Tailscale (not on the LAN).  Add Tailscale ACLs to restrict to the Spark node. |
| Mac GPU saturation | Low | Monitor via `make mac-status`.  Consider adding Ollama's `OLLAMA_MAX_LOADED_MODELS=1` and `OLLAMA_NUM_PARALLEL=1`. |
| TCP forwarder bypass | Low | The forwarder binds to 0.0.0.0:11435 but is only reachable via Tailscale.  Mac firewall can restrict to the Tailscale interface. |

## Mitigations Checklist

- [ ] **Tailscale ACLs**: Restrict port 11435 access to the Spark node's Tailscale IP only
- [ ] **Mac firewall**: Block port 11435 from non-Tailscale interfaces (`sudo pfctl` rule)
- [ ] **Ollama concurrency**: Set `OLLAMA_NUM_PARALLEL=1` to prevent GPU saturation
- [ ] **Provider scoping**: Only register `mac-ollama` for sandboxes that need it (currently all share it)
- [ ] **Audit review**: Periodically check `make security-audit` output for cross-machine anomalies
