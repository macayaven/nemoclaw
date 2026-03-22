#!/usr/bin/env bash
# NemoClaw System Status — full state in one command
set -euo pipefail

export PATH="$HOME/workspace/nemoclaw/openshell-env/bin:$HOME/workspace/nemoclaw/orchestrator-env/bin:$PATH"

SPARK_IP="100.93.220.104"
MAC_IP="100.116.228.36"
PI_IP="100.85.6.21"

G='\033[0;32m' R='\033[0;31m' Y='\033[1;33m' C='\033[0;36m' N='\033[0m'
ok() { echo -e "  ${G}OK${N}  $1"; }
fail() { echo -e "  ${R}FAIL${N} $1"; }
warn() { echo -e "  ${Y}WARN${N} $1"; }

echo -e "${C}========================================${N}"
echo -e "${C}  NemoClaw System Status${N}"
echo -e "${C}  $(date '+%Y-%m-%d %H:%M:%S')${N}"
echo -e "${C}========================================${N}"

# --- SPARK ---
echo -e "\n${Y}DGX Spark ($SPARK_IP)${N}"

echo -e "\n  Ollama:"
if curl -s --max-time 3 http://localhost:11434/ > /dev/null 2>&1; then
    ok "Running on :11434"
    MODELS=$(ollama ps 2>/dev/null | tail -n +2)
    if [ -n "$MODELS" ]; then
        echo "$MODELS" | while read -r line; do echo "       $line"; done
    else
        warn "No models loaded (will cold-start on next request)"
    fi
else
    fail "Not responding"
fi

echo -e "\n  LM Studio:"
if curl -s --max-time 3 http://localhost:1234/v1/models > /dev/null 2>&1; then
    ok "Running on :1234"
else
    warn "Not responding (optional)"
fi

echo -e "\n  OpenShell Gateway:"
if openshell status 2>&1 | grep -q "Connected"; then
    ok "Connected"
else
    fail "Not connected"
fi

echo -e "\n  Providers:"
openshell provider list 2>&1 | grep -v "^$" | while read -r line; do echo "       $line"; done

echo -e "\n  Inference Route:"
openshell inference get 2>&1 | grep -E "Provider|Model" | while read -r line; do echo "       $line"; done

echo -e "\n  Sandboxes:"
openshell sandbox list 2>&1 | tail -n +2 | while read -r line; do
    if echo "$line" | grep -q "Ready"; then
        ok "$line"
    else
        fail "$line"
    fi
done

echo -e "\n  Port Forward (18789):"
if ss -tlnp | grep -q 18789; then
    ok "Active"
else
    fail "Dead — run: openshell forward start 18789 nemoclaw-main --background"
fi

echo -e "\n  Tailscale Serve:"
if tailscale serve status 2>&1 | grep -q "proxy"; then
    URL=$(tailscale serve status 2>&1 | grep "https://" | awk '{print $1}')
    ok "$URL"
else
    warn "Not configured"
fi

echo -e "\n  OpenClaw Gateway (inside sandbox):"
SANDBOX_GW=$(ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR \
    -o "ProxyCommand=openshell ssh-proxy --gateway-name openshell --name nemoclaw-main" \
    sandbox@openshell-nemoclaw-main \
    "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18789/" 2>&1 || echo "000")
if [ "$SANDBOX_GW" = "200" ]; then
    ok "Running (HTTP 200)"
else
    fail "Not responding ($SANDBOX_GW)"
fi

echo -e "\n  Nodes:"
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR \
    -o "ProxyCommand=openshell ssh-proxy --gateway-name openshell --name nemoclaw-main" \
    sandbox@openshell-nemoclaw-main \
    "openclaw nodes status 2>&1" 2>&1 | grep -E "Known|Mac|paired|connected" | while read -r line; do echo "       $line"; done

# --- MAC ---
echo -e "\n${Y}Mac Studio ($MAC_IP)${N}"

echo -e "\n  Ollama (via forwarder :11435):"
if curl -s --max-time 5 http://$MAC_IP:11435/api/tags > /dev/null 2>&1; then
    COUNT=$(curl -s --max-time 5 http://$MAC_IP:11435/api/tags 2>/dev/null | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('models',[])))" 2>/dev/null || echo "?")
    ok "$COUNT models available"
else
    fail "Not reachable (forwarder dead or Ollama not running)"
fi

echo -e "\n  LM Studio (:1234):"
if curl -s --max-time 5 http://$MAC_IP:1234/v1/models > /dev/null 2>&1; then
    ok "Running"
else
    warn "Not reachable (Cursor may have port, or not running)"
fi

echo -e "\n  Node Host:"
NODE_STATUS=$(ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR \
    -o "ProxyCommand=openshell ssh-proxy --gateway-name openshell --name nemoclaw-main" \
    sandbox@openshell-nemoclaw-main \
    "openclaw nodes status 2>&1" 2>&1 | grep -c "connected" || echo "0")
if [ "$NODE_STATUS" -gt 0 ] 2>/dev/null; then
    ok "Connected"
else
    warn "Disconnected"
fi

# --- PI ---
echo -e "\n${Y}Raspberry Pi ($PI_IP)${N}"

echo -e "\n  LiteLLM (:4000):"
if curl -s --max-time 5 http://$PI_IP:4000/health > /dev/null 2>&1; then
    ok "Running"
else
    fail "Not responding"
fi

echo -e "\n  Uptime Kuma (:3001):"
if curl -s --max-time 5 http://$PI_IP:3001 > /dev/null 2>&1; then
    ok "Running"
else
    warn "Not responding"
fi

echo -e "\n  Tailscale:"
if ping -c 1 -W 2 $PI_IP > /dev/null 2>&1; then
    ok "Reachable"
else
    fail "Unreachable"
fi

# --- SUMMARY ---
echo -e "\n${C}========================================${N}"
echo -e "${C}  Quick Actions${N}"
echo -e "${C}========================================${N}"
echo "  Chat:        https://spark-caeb.tail48bab7.ts.net/"
echo "  Monitor:     openshell term"
echo "  Switch model: openshell inference set --provider <name> --model <model>"
echo "  All models:  curl http://$PI_IP:4000/v1/models"
echo "  Health:      http://$PI_IP:3001"
echo ""
