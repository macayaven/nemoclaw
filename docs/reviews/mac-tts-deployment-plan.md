# Mac TTS Deployment Plan

Status: recommended architecture and deployment plan

This document defines the recommended way to run text-to-speech on the Mac
Studio for the NemoClaw stack.

## 1. Recommendation

Run TTS **natively on macOS**, not inside Docker.

Reason:

- Apple Silicon acceleration depends on direct access to Metal / MLX
- Docker on Mac runs Linux workloads inside a VM
- Linux containers on macOS do not get direct Metal access
- putting the TTS inference path inside Docker on the Mac would force CPU
  inference and waste the M-series hardware

The correct pattern is:

- native TTS service on the Mac
- persistent `launchd` management
- stable HTTP API
- optional container clients on the Mac use `host.docker.internal`
- Spark reaches the Mac over Tailscale DNS or a dedicated forwarded port

This is the same architectural pattern already used successfully for Ollama.

## 2. Preferred TTS Candidate

Recommended first choice:

- Kokoro-82M served through an OpenAI-compatible HTTP wrapper

Why:

- high quality for small footprint
- local-first
- practical on a 36 GB Mac
- good fit for future wearable workflows
- API shape aligns well with later NemoClaw integration

Fallback if simplicity matters more than interface quality:

- Piper

## 3. Host / Network Model

### Mac-local clients

If a Docker container running on the **Mac itself** needs TTS, it should call:

```text
http://host.docker.internal:<port>
```

This is a Docker-on-Mac convenience path only.

### Spark / tailnet clients

If Spark needs to call the Mac TTS service, it should use:

- `mac-studio.local`
- or the Tailscale name / tailnet address
- or a small forwarded/proxied port on the Mac

Spark should **not** rely on `host.docker.internal`, because that name is only
meaningful from Docker containers on the same Mac host.

## 4. Bind Strategy

Do not blindly bind the TTS service to `0.0.0.0`.

Use:

- `127.0.0.1` if only Mac-local apps and Mac-local containers need the service
- `0.0.0.0` only if Spark or other remote tailnet clients need to call it

Recommended initial rollout:

1. Start with `127.0.0.1`
2. Validate local service behavior
3. Widen to `0.0.0.0` only when a real cross-machine integration requires it

## 5. launchd Placement

Use the correct `launchd` location for a user service:

```text
~/Library/LaunchAgents/com.nemoclaw.tts.plist
```

Do not mix:

- `~/Library/LaunchAgents` (user service)
- `/Library/LaunchDaemons` (system service)

For this setup, a user-scoped LaunchAgent is the right default.

## 6. Service Contract

The best service contract is an OpenAI-compatible speech endpoint, for example:

```text
POST /v1/audio/speech
```

This keeps later integration straightforward and avoids inventing a custom TTS
API shape unless the chosen runtime forces one.

## 7. Suggested Deployment Pattern

### Native service

- Python virtualenv or uv-managed environment on macOS
- persistent service via `launchd`
- stable port
- local logs
- explicit smoke tests

### Example user LaunchAgent

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.nemoclaw.tts</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/carlos/path/to/venv/bin/python</string>
    <string>-m</string>
    <string>mlx_audio.server</string>
    <string>--host</string>
    <string>127.0.0.1</string>
    <string>--port</string>
    <string>8000</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/tmp/nemoclaw-tts.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/nemoclaw-tts.err</string>
</dict>
</plist>
```

Load it:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.nemoclaw.tts.plist
launchctl kickstart -k gui/$(id -u)/com.nemoclaw.tts
```

If Spark later needs direct access, change `127.0.0.1` to `0.0.0.0` only after
the security boundary is understood and documented.

## 8. Docker-on-Mac Client Pattern

If another Docker service on the Mac needs TTS:

```yaml
services:
  app:
    environment:
      - AUDIO_TTS_API_BASE_URL=http://host.docker.internal:8000/v1
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

This lets Mac-local containers call the native TTS service without moving the
inference path into Docker.

## 9. Smoke Tests

### Health / docs

```bash
curl http://127.0.0.1:8000/docs
```

### Speech generation

```bash
curl -X POST http://127.0.0.1:8000/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{
    "model":"kokoro",
    "voice":"af_bella",
    "input":"Hello from NemoClaw.",
    "response_format":"mp3"
  }' \
  --output /tmp/nemoclaw-tts-test.mp3
```

### Mac-local container path

From a container running on the same Mac:

```bash
curl http://host.docker.internal:8000/docs
```

### Spark-to-Mac path

Only after remote reachability is intentionally enabled:

```bash
curl http://mac-studio.local:8000/docs
```

## 10. Security Notes

- Prefer `127.0.0.1` binding by default
- Only widen to `0.0.0.0` when cross-machine access is required
- If the service becomes reachable from Spark, document the exact network path
  and trust boundary
- Do not assume Tailscale itself is the application-layer auth boundary
- Keep the TTS model and runtime separate from the canonical knowledge service

## 11. Integration Sequence

Recommended order:

1. Stand up native TTS on the Mac
2. Validate local generation
3. Decide whether Spark must call it directly
4. If yes, expose a controlled remote path
5. Add NemoClaw integration only after the API contract is stable

## 12. Non-Goals

This plan does not:

- require Docker for TTS inference on the Mac
- require immediate Spark-to-Mac remote access
- define the final wearable product UX
- replace the current NemoClaw text/image flows yet
