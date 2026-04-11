<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Kokoro Deployment Guide

Use Kokoro as the Mac-hosted text-to-speech service for NemoClaw.

The correct architecture on Apple Silicon is:

- run TTS natively on macOS
- manage it with `launchd`
- expose a stable HTTP API
- let local containers use `host.docker.internal`
- let Spark reach it over Tailscale only if remote access is needed

Do not make Docker on Mac the primary inference path for TTS. Apple Silicon acceleration is available to native services, not to Linux containers running inside the Docker VM.

## Recommended Runtime Contract

Expose an OpenAI-compatible speech endpoint if possible:

- `POST /v1/audio/speech`

That keeps later integration with OpenClaw, NemoClaw, and wearable clients simple.

## Deployment Pattern

### 1. Create a dedicated virtual environment on the Mac

Use a standalone venv for Kokoro so it does not interfere with Ollama, LM Studio, or node-host tooling.

```bash
python3 -m venv ~/venvs/kokoro
source ~/venvs/kokoro/bin/activate
python -m pip install --upgrade pip
```

### 2. Bind locally first

Start with:

- host: `127.0.0.1`
- port: `8000`

Only widen to `0.0.0.0` if the Spark must call the Mac-hosted TTS service directly.

### 3. Manage it with `launchd`

Use a user agent under:

- `~/Library/LaunchAgents/ai.nemoclaw.kokoro.plist`

Keep the service persistent and restartable at login. Log stdout/stderr to files under `/tmp` or a dedicated logs directory.

### 3.1 Example user-agent layout

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>ai.nemoclaw.kokoro</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/carlos/venvs/kokoro/bin/python</string>
    <string>-m</string>
    <string>kokoro_server</string>
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
  <string>/tmp/kokoro.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/kokoro.err</string>
</dict>
</plist>
```

Load it with:

```bash
launchctl unload ~/Library/LaunchAgents/ai.nemoclaw.kokoro.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/ai.nemoclaw.kokoro.plist
launchctl list | grep ai.nemoclaw.kokoro
```

### 4. Keep the Mac as the modality node

The Mac should host:

- Kokoro TTS
- the OpenClaw node host
- future wearable-facing modality services

The Spark should continue to host:

- primary reasoning models
- retrieval services
- the OpenClaw gateway

## Suggested `launchd` Shape

Use a plist that:

- launches the Kokoro API server from the venv
- sets `RunAtLoad` to true
- sets `KeepAlive` to true
- writes logs to known locations

Do not hard-code temporary shell wrappers if a direct `ProgramArguments` array is sufficient.

## Smoke Tests

After launch:

```bash
curl -sf http://127.0.0.1:8000/health
```

If the runtime exposes an OpenAI-compatible speech path:

```bash
curl -X POST http://127.0.0.1:8000/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"kokoro","input":"Hello from NemoClaw.","voice":"alloy"}' \
  --output /tmp/kokoro-smoke.wav
```

Expected result:

- request succeeds
- a playable waveform is written
- the Mac remains responsive

### Smoke-test from Spark later

Once you intentionally widen the bind from `127.0.0.1` to `0.0.0.0` and expose it over Tailscale:

```bash
curl -sf http://mac-studio.local:8000/health
```

## Docker-on-Mac Clients

If a service running on the same Mac but inside Docker needs TTS, call:

- `http://host.docker.internal:8000`

This is for Mac-local containers only. Spark should not use `host.docker.internal`.

## Spark Reachability

If Spark must call Kokoro directly:

1. widen the service bind to `0.0.0.0`
2. restrict reachability with Tailscale/DNS/reverse-proxy controls
3. document the stable Mac endpoint in NemoClaw

Prefer a single stable tailnet hostname over raw IPs.

## Integration Target for NemoClaw

Treat Kokoro as a modality service, not a new reasoning model.

The intended role is:

- the assistant decides to return speech
- the gateway or a small host-side adapter requests audio from Kokoro
- the resulting audio is delivered back through the user-facing channel or device

This keeps the Mac focused on output modalities while Spark remains the intelligence node.
