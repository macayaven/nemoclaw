# NemoClaw — Multi-Machine AI Deployment
# Run `make help` to see all targets
#
# Prerequisites:
#   - openshell-env venv at ~/workspace/nemoclaw/openshell-env/
#   - orchestrator-env venv at ~/workspace/nemoclaw/orchestrator-env/
#   - sshpass installed (for Mac SSH)

SHELL := /bin/bash
.DEFAULT_GOAL := help

# --- Paths ---
OS_ENV := source ~/workspace/nemoclaw/openshell-env/bin/activate
ORC_ENV := source ~/workspace/nemoclaw/orchestrator-env/bin/activate
ORC_RUN := ~/workspace/nemoclaw/orchestrator-env/bin/python -m orchestrator
SSH_SANDBOX := ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR \
	-o "ProxyCommand=openshell ssh-proxy --gateway-name openshell --name nemoclaw-main" \
	sandbox@openshell-nemoclaw-main
MAC := sshpass -p "1685" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null carlos@100.116.228.36
OPENCLAW_GATEWAY_LOG := /tmp/gateway.log
OPENCLAW_SUPERVISOR_LOCAL := scripts/openclaw-gateway-supervise.sh
OPENCLAW_SUPERVISOR_REMOTE := /tmp/openclaw-gateway-supervise.sh
OPENCLAW_BOOTSTRAP_LOCAL := scripts/openclaw-bootstrap-profile.sh
OPENCLAW_BOOTSTRAP_REMOTE := /tmp/openclaw-bootstrap-profile
OPENCLAW_SUPERVISOR_LOG := /tmp/gateway-supervisor.log
OPENCLAW_SUPERVISOR_PID := /tmp/gateway-supervisor.pid
OPENCLAW_GATEWAY_PID := /tmp/openclaw-native.pid
OPENCLAW_PROFILE := native
OPENCLAW_STATE_DIR := /sandbox/.openclaw-$(OPENCLAW_PROFILE)
OPENCLAW_FORWARD_LOG := /tmp/openclaw-port-forward.log
OPENCLAW_FORWARD_SESSION := openclaw-ui-bridge
OPENCLAW_HOST_PORT := 39989
OPENCLAW_UI_PORT := 18789
OPENCLAW_FORWARD_PORT_FILE := /tmp/openclaw-forward-port
OPENCLAW_POD_EXEC := $(OS_ENV) && openshell doctor exec -- kubectl -n openshell exec nemoclaw-main --
OPENCLAW_POD_EXEC_TTY := $(OS_ENV) && openshell doctor exec -- kubectl -n openshell exec -it nemoclaw-main --

# --- IPs ---
SPARK_IP := 100.93.220.104
MAC_IP := 100.116.228.36
PI_IP := 100.85.6.21

# ===========================================================================
# Status & Health
# ===========================================================================

.PHONY: status
status: ## Show full system status (all machines)
	@./scripts/status.sh

.PHONY: health
health: ## Quick health check (gateway + sandboxes only)
	@$(OS_ENV) && openshell status && echo "---" && openshell sandbox list

.PHONY: models
models: ## Show loaded models on Spark
	@ollama ps 2>/dev/null || echo "No models loaded"

.PHONY: models-all
models-all: ## Show all downloaded models on Spark
	@ollama list

.PHONY: providers
providers: ## List registered inference providers
	@$(OS_ENV) && openshell provider list

.PHONY: route
route: ## Show active inference route
	@$(OS_ENV) && openshell inference get

# ===========================================================================
# Full Setup (from scratch or after gateway recreate)
# ===========================================================================

.PHONY: setup
setup: ## Full automated setup: gateway + providers + sandboxes + config + UI (zero manual steps)
	@echo "=== Step 1/7: Starting gateway ==="
	@$(OS_ENV) && openshell gateway start 2>&1 | tail -3
	@echo ""
	@echo "=== Step 2/7: Registering providers ==="
	@$(OS_ENV) && \
		openshell provider create --name local-ollama --type openai \
			--credential OPENAI_API_KEY=not-needed \
			--config OPENAI_BASE_URL=http://host.openshell.internal:11434/v1 2>&1 | tail -1; \
		openshell provider create --name local-lmstudio --type openai \
			--credential OPENAI_API_KEY=lm-studio \
			--config OPENAI_BASE_URL=http://host.openshell.internal:1234/v1 2>&1 | tail -1; \
		openshell provider create --name mac-ollama --type openai \
			--credential OPENAI_API_KEY=not-needed \
			--config OPENAI_BASE_URL=http://$(MAC_IP):11435/v1 2>&1 | tail -1
	@echo ""
	@echo "=== Step 2b/7: Registering cloud API providers (credential isolation) ==="
	@$(OS_ENV) && \
		openshell provider create --name anthropic-cloud --type openai \
			--credential OPENAI_API_KEY=$${ANTHROPIC_API_KEY} \
			--config OPENAI_BASE_URL=https://api.anthropic.com/v1 2>&1 | tail -1; \
		openshell provider create --name gemini-cloud --type openai \
			--credential OPENAI_API_KEY=$${GEMINI_API_KEY} \
			--config OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta 2>&1 | tail -1
	@echo "Cloud providers registered. Credentials held at gateway level."
	@echo ""
	@echo "=== Step 3/7: Setting inference route ==="
	@$(OS_ENV) && openshell inference set --provider local-ollama --model nemotron-3-super:120b 2>&1 | tail -1
	@echo ""
	@echo "=== Step 4/7: Creating sandboxes ==="
	@$(OS_ENV) && \
		openshell sandbox create --keep --forward 18789 --name nemoclaw-main --from openclaw -- bash 2>&1 | tail -3; \
		openshell sandbox create --keep --name claude-dev -- claude 2>&1 | tail -1; \
		openshell sandbox create --keep --name codex-dev -- bash 2>&1 | tail -1; \
		openshell sandbox create --keep --name gemini-dev -- bash 2>&1 | tail -1
	@echo ""
	@echo "=== Step 4b/7: Configuring sandbox inference routes ==="
	@$(OS_ENV) && \
		openshell inference set --sandbox claude-dev --provider anthropic-cloud --model claude-sonnet-4-20250514 2>&1 | tail -1; \
		openshell inference set --sandbox codex-dev --provider local-ollama --model nemotron-3-super:120b 2>&1 | tail -1; \
		openshell inference set --sandbox gemini-dev --provider gemini-cloud --model gemini-2.5-pro 2>&1 | tail -1
	@echo ""
	@echo "=== Step 5/7: Writing OpenClaw config (named profile, bundled channels) ==="
	@$(MAKE) --no-print-directory setup-openclaw-profile
	@echo ""
	@echo "=== Step 6/7: Starting OpenClaw gateway (with auto-restart) ==="
	@$(MAKE) --no-print-directory start-openclaw
	@$(OS_ENV) && $(SSH_SANDBOX) '\
		for i in $$(seq 1 15); do \
			CODE=$$(curl -s -o /dev/null -w "HTTP %{http_code}" http://127.0.0.1:18789/ || true); \
			if [ "$$CODE" = "HTTP 200" ]; then \
				echo "$$CODE — gateway up"; \
				exit 0; \
			fi; \
			sleep 1; \
		done; \
		echo "Gateway failed to become ready"; \
		tail -n 50 $(OPENCLAW_GATEWAY_LOG) 2>/dev/null || true; \
		exit 1' 2>&1
	@echo ""
	@echo "=== Step 7/7: Starting port forward + configuring agent sandboxes ==="
	@$(MAKE) --no-print-directory start-forward
	@$(MAKE) --no-print-directory _setup-codex
	@$(MAKE) --no-print-directory _setup-gemini
	@echo ""
	@echo "=== Setup complete ==="
	@echo "  UI:     https://spark-caeb.tail48bab7.ts.net/"
	@echo "  Status: make status"
	@echo "  Note:   First browser visit needs device approval: make mac-approve"

.PHONY: setup-openclaw-profile
setup-openclaw-profile: ## Write the OpenClaw named-profile config used by NemoClaw
	@$(OS_ENV) && openshell sandbox upload nemoclaw-main $(OPENCLAW_BOOTSTRAP_LOCAL) $(OPENCLAW_BOOTSTRAP_REMOTE) >/dev/null
	@$(OS_ENV) && openshell sandbox upload nemoclaw-main $(OPENCLAW_SUPERVISOR_LOCAL) $(OPENCLAW_SUPERVISOR_REMOTE) >/dev/null
	@$(OPENCLAW_POD_EXEC) sh -lc 'HOME=/sandbox sh $(OPENCLAW_BOOTSTRAP_REMOTE)/openclaw-bootstrap-profile.sh $(OPENCLAW_PROFILE)'

.PHONY: _setup-codex
_setup-codex:
	@$(OS_ENV) && ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR \
		-o "ProxyCommand=openshell ssh-proxy --gateway-name openshell --name codex-dev" \
		sandbox@openshell-codex-dev '\
mkdir -p ~/.codex && \
printf "[model_providers.nemoclaw]\nname = \"NemoClaw Local\"\nbase_url = \"https://inference.local/v1\"\nenv_key = \"OPENAI_API_KEY\"\n" > ~/.codex/config.toml && \
sed -i "1i model = \"nemotron-3-super:120b\"\nmodel_provider = \"nemoclaw\"\napproval_policy = \"never\"\nsandbox_mode = \"danger-full-access\"\nsuppress_unstable_features_warning = true\n" ~/.codex/config.toml && \
echo "export OPENAI_API_KEY=unused" >> ~/.bashrc && \
cd /sandbox && git init 2>/dev/null && git config user.email "sandbox@nemoclaw" && git config user.name "sandbox" && \
echo "Codex configured"' 2>&1

.PHONY: _setup-gemini
_setup-gemini:
	@$(OS_ENV) && ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR \
		-o "ProxyCommand=openshell ssh-proxy --gateway-name openshell --name gemini-dev" \
		sandbox@openshell-gemini-dev '\
mkdir -p ~/.npm-global && npm config set prefix ~/.npm-global && \
export PATH=~/.npm-global/bin:$$PATH && \
npm install -g @google/gemini-cli 2>&1 | tail -1 && \
echo "export PATH=~/.npm-global/bin:\$$PATH" >> ~/.bashrc && \
echo "Gemini CLI installed"' 2>&1

.PHONY: onboard
onboard: ## Open the OpenClaw wizard manually (only if make setup config doesn't work)
	@$(OS_ENV) && openshell sandbox connect nemoclaw-main

.PHONY: post-onboard
post-onboard: start-openclaw start-forward _setup-codex _setup-gemini ## Finish after manual onboard
	@echo ""
	@echo "=== Done ==="
	@echo "  UI: https://spark-caeb.tail48bab7.ts.net/"
	@echo "  Pair browser/device requests with: make devices-list"
	@echo "  Status: make status"

.PHONY: devices-list
devices-list: ## List pending and approved OpenClaw device-pairing requests
	@$(OPENCLAW_POD_EXEC) sh -lc \
		'HOME=/sandbox openclaw --profile $(OPENCLAW_PROFILE) devices list'

.PHONY: approve-latest-device
approve-latest-device: ## Approve the oldest pending OpenClaw device request
	@$(OPENCLAW_POD_EXEC) sh -lc '\
PENDING=$$(HOME=/sandbox openclaw --profile $(OPENCLAW_PROFILE) devices list 2>/dev/null | \
	awk '\''BEGIN{pending=0} /^Pending/{pending=1; next} /^Paired/{pending=0} pending && /^│/ { line=$$0; sub(/^│[[:space:]]*/, "", line); split(line, cols, "│"); req=cols[1]; gsub(/^[[:space:]]+|[[:space:]]+$$/, "", req); if (req != "" && req != "Request") { print req; exit } }'\''); \
if [ -n "$$PENDING" ]; then \
    HOME=/sandbox openclaw --profile $(OPENCLAW_PROFILE) devices approve "$$PENDING"; \
else \
    echo "No pending devices"; \
fi'

.PHONY: gateway-token
gateway-token: ## Print the live OpenClaw gateway token (fallback auth only)
	@$(OPENCLAW_POD_EXEC) sh -lc \
		'python3 -c "import json; print(json.load(open(\"$(OPENCLAW_STATE_DIR)/openclaw.json\")).get(\"gateway\",{}).get(\"auth\",{}).get(\"token\",\"\"))"'

.PHONY: mac-approve
mac-approve: ## Approve pending Mac node pairing requests
	@$(OPENCLAW_POD_EXEC) sh -lc '\
	PENDING=$$(HOME=/sandbox openclaw --profile $(OPENCLAW_PROFILE) devices list 2>/dev/null | \
		awk '\''BEGIN{pending=0} /^Pending/{pending=1; next} /^Paired/{pending=0} pending && /^│/ { line=$$0; sub(/^│[[:space:]]*/, "", line); split(line, cols, "│"); req=cols[1]; gsub(/^[[:space:]]+|[[:space:]]+$$/, "", req); if (req != "" && req != "Request") { print req; exit } }'\''); \
if [ -n "$$PENDING" ]; then \
    HOME=/sandbox openclaw --profile $(OPENCLAW_PROFILE) devices approve $$PENDING 2>&1 | tail -1; \
else \
    echo "No pending devices"; \
fi'

# ===========================================================================
# Start & Stop
# ===========================================================================

.PHONY: start
start: start-gateway start-openclaw start-forward ## Start everything on Spark
	@echo "NemoClaw started. Run 'make status' to verify."

.PHONY: start-gateway
start-gateway: ## Start OpenShell gateway
	@$(OS_ENV) && openshell gateway start
	@for i in $$(seq 1 60); do \
		if $(OS_ENV) >/dev/null 2>&1 && openshell status >/dev/null 2>&1; then \
			echo "OpenShell gateway connected"; \
			exit 0; \
		fi; \
		sleep 2; \
	done; \
	echo "OpenShell gateway failed to become ready"; \
	$(OS_ENV) >/dev/null 2>&1 && openshell status || true; \
	exit 1

.PHONY: start-gateway-fresh
start-gateway-fresh: ## Recreate and start gateway (clean slate)
	@$(OS_ENV) && openshell gateway start --recreate

.PHONY: start-forward
start-forward: ## Start the preferred OpenShell port forward and repoint Tailscale Serve
	@set -eu; \
	rm -f $(OPENCLAW_FORWARD_PORT_FILE); \
	$(OS_ENV) >/dev/null 2>&1 && openshell forward stop 127.0.0.1:$(OPENCLAW_UI_PORT) 2>/dev/null || true; \
	$(OS_ENV) >/dev/null 2>&1 && openshell forward stop $(OPENCLAW_UI_PORT) 2>/dev/null || true; \
	if tmux has-session -t $(OPENCLAW_FORWARD_SESSION) 2>/dev/null; then \
		tmux kill-session -t $(OPENCLAW_FORWARD_SESSION); \
	fi; \
	if $(OS_ENV) >/dev/null 2>&1 && openshell forward start 127.0.0.1:$(OPENCLAW_UI_PORT) nemoclaw-main --background >/dev/null 2>&1; then \
		PORT=$(OPENCLAW_UI_PORT); \
		echo "OpenClaw UI forward started via openshell forward"; \
	else \
		tmux new-session -d -s $(OPENCLAW_FORWARD_SESSION) \
			'cd $(CURDIR) && source ~/workspace/nemoclaw/openshell-env/bin/activate && OPENCLAW_UI_BRIDGE_PORT=$(OPENCLAW_HOST_PORT) python3 scripts/openclaw-ui-bridge.py >> $(OPENCLAW_FORWARD_LOG) 2>&1'; \
		PORT=$(OPENCLAW_HOST_PORT); \
		echo "OpenClaw UI forward started via local bridge fallback"; \
	fi; \
	printf '%s\n' "$$PORT" > $(OPENCLAW_FORWARD_PORT_FILE); \
	for i in $$(seq 1 20); do \
		if curl -sf "http://127.0.0.1:$$PORT/health" >/dev/null 2>&1; then \
			break; \
		fi; \
		sleep 1; \
	done; \
	curl -sf "http://127.0.0.1:$$PORT/health" >/dev/null 2>&1 || { \
		echo "OpenClaw UI forward failed"; \
		if [ "$$PORT" = "$(OPENCLAW_HOST_PORT)" ]; then \
			tail -n 50 $(OPENCLAW_FORWARD_LOG) 2>/dev/null || true; \
		fi; \
		exit 1; \
	}; \
	tailscale serve --yes --bg "http://127.0.0.1:$$PORT" >/dev/null; \
	echo "Tailscale Serve -> http://127.0.0.1:$$PORT"

.PHONY: stop-forward
stop-forward: ## Stop the host-side Control UI forward
	@$(OS_ENV) >/dev/null 2>&1 && openshell forward stop 127.0.0.1:$(OPENCLAW_UI_PORT) 2>/dev/null || true
	@$(OS_ENV) >/dev/null 2>&1 && openshell forward stop $(OPENCLAW_UI_PORT) 2>/dev/null || true
	@if tmux has-session -t $(OPENCLAW_FORWARD_SESSION) 2>/dev/null; then \
		tmux kill-session -t $(OPENCLAW_FORWARD_SESSION); \
		echo "OpenClaw UI bridge fallback stopped"; \
	else \
		echo "OpenClaw UI bridge fallback not running"; \
	fi
	@rm -f $(OPENCLAW_FORWARD_PORT_FILE)

.PHONY: start-openclaw
start-openclaw: ## Start OpenClaw gateway inside sandbox under a restart supervisor
	@$(OS_ENV) && openshell sandbox upload nemoclaw-main $(OPENCLAW_SUPERVISOR_LOCAL) $(OPENCLAW_SUPERVISOR_REMOTE) >/dev/null
	@$(OPENCLAW_POD_EXEC) sh -lc '\
		if [ -f $(OPENCLAW_SUPERVISOR_PID) ] && kill -0 "$$(cat $(OPENCLAW_SUPERVISOR_PID))" 2>/dev/null; then \
			echo "OpenClaw gateway supervisor already running"; \
		elif pgrep -f "[o]penclaw-gateway-supervise.sh" >/dev/null; then \
			echo "OpenClaw gateway supervisor already running"; \
		else \
			pkill -f "[o]penclaw --profile $(OPENCLAW_PROFILE) gateway run" 2>/dev/null || true; \
			rm -f $(OPENCLAW_SUPERVISOR_PID) $(OPENCLAW_GATEWAY_PID) && \
			HOME=/sandbox OPENCLAW_PROFILE=$(OPENCLAW_PROFILE) OPENCLAW_GATEWAY_LOG=$(OPENCLAW_GATEWAY_LOG) OPENCLAW_GATEWAY_RESTART_DELAY=5 \
			nohup setsid sh -lc "exec sh $(OPENCLAW_SUPERVISOR_REMOTE)" </dev/null > $(OPENCLAW_SUPERVISOR_LOG) 2>&1 & \
			echo $$! > $(OPENCLAW_SUPERVISOR_PID) && \
			echo "OpenClaw gateway supervisor started"; \
		fi'
	@$(OPENCLAW_POD_EXEC) sh -lc '\
		for i in $$(seq 1 60); do \
			if curl -sf http://127.0.0.1:$(OPENCLAW_UI_PORT)/health >/dev/null 2>&1; then \
				echo "OpenClaw gateway ready"; \
				exit 0; \
			fi; \
			sleep 2; \
		done; \
		echo "OpenClaw gateway failed to become ready"; \
		tail -n 100 $(OPENCLAW_SUPERVISOR_LOG) 2>/dev/null || true; \
		tail -n 100 $(OPENCLAW_GATEWAY_LOG) 2>/dev/null || true; \
		exit 1'

.PHONY: restart-openclaw
restart-openclaw: ## Restart OpenClaw gateway under the sandbox supervisor
	@$(OPENCLAW_POD_EXEC) sh -lc '\
		pkill -f "[o]penclaw-gateway-supervise.sh" 2>/dev/null || true; \
		pkill -f "[o]penclaw --profile $(OPENCLAW_PROFILE) gateway run" 2>/dev/null || true; \
		rm -f $(OPENCLAW_SUPERVISOR_PID) $(OPENCLAW_GATEWAY_PID)' || true
	@$(MAKE) --no-print-directory start-openclaw

.PHONY: start-lmstudio
start-lmstudio: ## Start LM Studio on Spark (:1234)
	@lms server start --port 1234 --bind 0.0.0.0 --cors 2>/dev/null || echo "LM Studio may already be running"

.PHONY: stop-lmstudio
stop-lmstudio: ## Stop LM Studio on Spark
	@lms server stop 2>/dev/null && echo "LM Studio stopped" || echo "LM Studio was not running"

.PHONY: mac-start-lmstudio
mac-start-lmstudio: ## Start LM Studio on Mac (opens the app)
	@$(MAC) 'open -a "LM Studio"' 2>/dev/null && echo "LM Studio started on Mac"

.PHONY: mac-stop-lmstudio
mac-stop-lmstudio: ## Stop LM Studio on Mac
	@$(MAC) 'pkill -f "LM Studio"' 2>/dev/null && echo "LM Studio stopped on Mac" || echo "Not running"

.PHONY: stop
stop: ## Stop gateway (sandboxes freeze, restore on next start)
	@$(OS_ENV) && openshell gateway stop

.PHONY: stop-all
stop-all: ## Stop everything on Spark
	@$(OS_ENV) && openshell gateway stop 2>/dev/null; \
	lms server stop 2>/dev/null; \
	echo "Stopped. Ollama still running (systemd). Use 'make stop-ollama' to free GPU."

.PHONY: stop-ollama
stop-ollama: ## Stop Ollama and free GPU memory
	@sudo systemctl stop ollama && echo "Ollama stopped, GPU memory freed"

.PHONY: restart
restart: stop start ## Restart everything

# ===========================================================================
# Model Management
# ===========================================================================

.PHONY: use-nemotron
use-nemotron: ## Switch to Nemotron 120B (Spark, local)
	@$(OS_ENV) && openshell inference set --provider local-ollama --model nemotron-3-super:120b

.PHONY: use-coder
use-coder: ## Switch to Qwen3 Coder (Spark, local)
	@$(OS_ENV) && openshell inference set --provider local-ollama --model qwen3-coder-next:q4_K_M

.PHONY: use-mac
use-mac: ## Switch to Qwen3 8B (Mac, fast)
	@$(OS_ENV) && openshell inference set --provider mac-ollama --model qwen3:8b

.PHONY: use-lmstudio
use-lmstudio: ## Switch to LM Studio (Spark)
	@$(OS_ENV) && openshell inference set --provider local-lmstudio --model $(or $(MODEL),default)

.PHONY: warmup
warmup: ## Pre-load Nemotron into GPU memory
	@echo "Loading Nemotron 120B into GPU (30-60s)..."
	@curl -s http://localhost:11434/api/generate \
		-d '{"model":"nemotron-3-super:120b","prompt":"hello","stream":false}' > /dev/null && \
		echo "Model loaded and warm"

.PHONY: unload
unload: ## Unload all models from GPU (free memory)
	@curl -s http://localhost:11434/api/generate \
		-d '{"model":"nemotron-3-super:120b","keep_alive":0}' > /dev/null 2>&1; \
	echo "Models unloading. GPU memory will be freed shortly."

.PHONY: pull
pull: ## Pull a model (usage: make pull MODEL=qwen3:8b)
	@ollama pull $(MODEL)

# ===========================================================================
# Sandbox Management
# ===========================================================================

.PHONY: sandboxes
sandboxes: ## List all sandboxes
	@$(OS_ENV) && openshell sandbox list

.PHONY: connect-openclaw
connect-openclaw: ## Connect to OpenClaw sandbox
	@$(OS_ENV) && openshell sandbox connect nemoclaw-main

.PHONY: connect-claude
connect-claude: ## Connect to Claude Code sandbox
	@$(OS_ENV) && openshell sandbox connect claude-dev

.PHONY: connect-codex
connect-codex: ## Connect to Codex sandbox
	@$(OS_ENV) && openshell sandbox connect codex-dev

.PHONY: connect-gemini
connect-gemini: ## Connect to Gemini CLI sandbox
	@$(OS_ENV) && openshell sandbox connect gemini-dev

.PHONY: logs
logs: ## Tail sandbox logs (usage: make logs NAME=nemoclaw-main)
	@$(OS_ENV) && openshell logs $(or $(NAME),nemoclaw-main) --tail

.PHONY: monitor
monitor: ## Open the real-time TUI monitor
	@$(OS_ENV) && openshell term

# ===========================================================================
# Orchestrator
# ===========================================================================

.PHONY: orch-health
orch-health: ## Check orchestrator can reach all sandboxes
	@export PATH=~/workspace/nemoclaw/openshell-env/bin:$$PATH && \
		cd ~/workspace/nemoclaw && $(ORC_RUN) health

.PHONY: orch-status
orch-status: ## Show orchestrator task queue
	@export PATH=~/workspace/nemoclaw/openshell-env/bin:$$PATH && \
		cd ~/workspace/nemoclaw && $(ORC_RUN) status

.PHONY: delegate
delegate: ## Delegate to an agent (usage: make delegate AGENT=codex PROMPT="write hello world")
	@export PATH=~/workspace/nemoclaw/openshell-env/bin:$$PATH && \
		cd ~/workspace/nemoclaw && $(ORC_RUN) delegate --agent $(AGENT) --prompt "$(PROMPT)"

.PHONY: pipeline
pipeline: ## Run a multi-agent pipeline (usage: make pipeline STEPS="gemini:research,codex:implement" PROMPT="...")
	@export PATH=~/workspace/nemoclaw/openshell-env/bin:$$PATH && \
		cd ~/workspace/nemoclaw && $(ORC_RUN) pipeline --steps "$(STEPS)" --prompt "$(PROMPT)"

# ===========================================================================
# Mac Studio
# ===========================================================================

.PHONY: mac-status
mac-status: ## Check Mac Ollama status
	@curl -s --max-time 5 http://$(MAC_IP):11435/api/tags 2>/dev/null | \
		python3 -c "import json,sys; [print(m['name']) for m in json.load(sys.stdin)['models']]" 2>/dev/null || \
		echo "Mac Ollama not reachable (forwarder dead or Ollama not running)"

.PHONY: mac-restart
mac-restart: ## Restart Ollama on Mac (correct boot order)
	@$(MAC) 'pkill -f Cursor; pkill -f Ollama; sleep 3; open -a Ollama; sleep 5; open -a Cursor' 2>/dev/null && \
		echo "Mac restarted: Ollama first, then Cursor"

.PHONY: mac-forwarder
mac-forwarder: ## Start the TCP forwarder on Mac (0.0.0.0:11435 → 127.0.0.1:11434)
	@$(MAC) 'python3 -c "\
import socket, threading\n\
def forward(src, dst):\n\
    try:\n\
        while True:\n\
            data = src.recv(65536)\n\
            if not data: break\n\
            dst.sendall(data)\n\
    except: pass\n\
    src.close(); dst.close()\n\
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n\
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n\
server.bind((\"0.0.0.0\", 11435))\n\
server.listen(50)\n\
print(\"Forwarding 0.0.0.0:11435 -> 127.0.0.1:11434\")\n\
while True:\n\
    client, _ = server.accept()\n\
    upstream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n\
    upstream.connect((\"127.0.0.1\", 11434))\n\
    threading.Thread(target=forward, args=(client, upstream), daemon=True).start()\n\
    threading.Thread(target=forward, args=(upstream, client), daemon=True).start()\n\
" &' 2>/dev/null && echo "Mac forwarder started"

# ===========================================================================
# Testing
# ===========================================================================

.PHONY: test
test: ## Run all tests
	@cd tests && uv run pytest -v

.PHONY: test-phase
test-phase: ## Run a specific phase (usage: make test-phase PHASE=1)
	@cd tests && uv run pytest phase$(PHASE)_*/ -v

.PHONY: lint
lint: ## Run all linting checks
	@cd tests && uv run ruff check . && uv run ruff format --check . && \
		uv run mypy . --config-file ../pyproject.toml && echo "All checks passed"

.PHONY: fix
fix: ## Auto-fix lint issues
	@cd tests && uv run ruff check . --fix && uv run ruff format . && uv run isort .

.PHONY: security-audit
security-audit: ## Run all security posture checks in one shot
	@echo ""
	@echo "=== NemoClaw Security Audit ==="
	@echo "=== $$(date '+%Y-%m-%d %H:%M:%S') ==="
	@echo ""
	@echo "--- 1. OpenClaw Config Integrity (Gap 9) ---"
	@cd tests && uv run pytest phase4_agents/test_openclaw_integrity.py -v --tb=short 2>&1; echo ""
	@echo "--- 2. Sandbox Hardening (Gap 2) ---"
	@cd tests && uv run pytest phase4_agents/test_sandbox_hardening.py -v --tb=short 2>&1; echo ""
	@echo "--- 3. Network Policy (Gap 3) ---"
	@cd tests && uv run pytest phase4_agents/test_network_policy.py -v --tb=short 2>&1; echo ""
	@echo "--- 4. Secret Hygiene ---"
	@cd tests && uv run pytest phase4_agents/test_secret_hygiene.py -v --tb=short 2>&1; echo ""
	@echo "--- 5. Sandbox Isolation ---"
	@cd tests && uv run pytest phase4_agents/test_sandbox_isolation.py -v --tb=short 2>&1; echo ""
	@echo "--- 6. SSRF Protection (Gap 11) ---"
	@cd tests && uv run pytest phase4_agents/test_ssrf_protection.py -v --tb=short 2>&1; echo ""
	@echo "--- 7. Device Auth (Gap 5) ---"
	@cd tests && uv run pytest phase1_core/test_gateway.py::TestDeviceAuth -v --tb=short 2>&1; echo ""
	@echo "--- 8. Credential Routing Audit ---"
	@$(MAKE) --no-print-directory secret-audit 2>&1; echo ""
	@echo ""
	@echo "=== Audit Complete ==="

.PHONY: secret-audit
secret-audit: ## Check no API keys are visible inside sandboxes
	@echo "Checking sandbox environments for leaked credentials..."
	@for sb in claude-dev codex-dev gemini-dev; do \
		echo "  $$sb:"; \
		$(OS_ENV) && openshell sandbox connect $$sb -- env 2>/dev/null | grep -iE "(api.key|api_key|secret|token)" && \
			echo "    WARNING: Credentials visible!" || echo "    OK — no credentials in env"; \
	done

# ===========================================================================
# Chat (quick inference)
# ===========================================================================

.PHONY: chat
chat: ## Quick chat with Nemotron (usage: make chat MSG="hello")
	@curl -s http://localhost:11434/v1/chat/completions \
		-H "Content-Type: application/json" \
		-d '{"model":"nemotron-3-super:120b","messages":[{"role":"user","content":"$(MSG)"}],"max_tokens":200}' | \
		python3 -c "import json,sys; print(json.load(sys.stdin)['choices'][0]['message']['content'])"

.PHONY: chat-mac
chat-mac: ## Quick chat with Mac's qwen3:8b (usage: make chat-mac MSG="hello")
	@curl -s http://$(MAC_IP):11435/v1/chat/completions \
		-H "Content-Type: application/json" \
		-d '{"model":"qwen3:8b","messages":[{"role":"user","content":"$(MSG)"}],"max_tokens":200}' | \
		python3 -c "import json,sys; print(json.load(sys.stdin)['choices'][0]['message']['content'])"

# ===========================================================================
# Updates (one at a time, verify after each)
# ===========================================================================

.PHONY: update-ollama
update-ollama: ## Update Ollama on Spark (models preserved)
	@curl -fsSL https://ollama.com/install.sh | sh && \
		sudo systemctl restart ollama && sleep 2 && \
		ollama --version && echo "Ollama updated. Run 'make warmup' to reload model."

.PHONY: update-openshell
update-openshell: ## Update OpenShell (stops gateway, restarts after)
	@echo "Stopping gateway..." && $(OS_ENV) && openshell gateway stop 2>/dev/null; \
		pip install --upgrade openshell && \
		echo "Restarting gateway..." && openshell gateway start && \
		echo "Updated. Run 'make status' to verify."

.PHONY: update-litellm
update-litellm: ## Update LiteLLM on Pi
	@ssh carlos@$(PI_IP) 'source ~/litellm-env/bin/activate && pip install --upgrade litellm && sudo systemctl restart litellm' && \
		echo "LiteLLM updated on Pi"

.PHONY: versions
versions: ## Show all component versions and save to versions.lock
	@echo "Ollama:    $$(ollama --version 2>/dev/null || echo 'not found')"
	@echo "LM Studio: $$(lms --version 2>/dev/null || echo 'not found')"
	@echo "OpenShell: $$($(OS_ENV) && openshell --version 2>/dev/null || echo 'not found')"
	@echo "NemoClaw:  $$(nemoclaw --version 2>/dev/null || echo 'not found')"
	@echo "Node.js:   $$(node --version 2>/dev/null || echo 'not found')"
	@echo "Docker:    $$(docker --version 2>/dev/null || echo 'not found')"
	@echo ""
	@echo "Writing versions.lock..."
	@echo "# NemoClaw Version Lock — $$(date -u +%Y-%m-%dT%H:%M:%SZ)" > versions.lock
	@echo "ollama=$$(ollama --version 2>/dev/null | grep -oP '\d+\.\d+\.\d+' || echo 'unknown')" >> versions.lock
	@echo "openshell=$$($(OS_ENV) && openshell --version 2>/dev/null | grep -oP '\d+\.\d+\.\d+' || echo 'unknown')" >> versions.lock
	@echo "nemoclaw=$$(nemoclaw --version 2>/dev/null | grep -oP '\d+\.\d+\.\d+' || echo 'unknown')" >> versions.lock
	@echo "node=$$(node --version 2>/dev/null | tr -d 'v' || echo 'unknown')" >> versions.lock
	@echo "docker=$$(docker --version 2>/dev/null | grep -oP '\d+\.\d+\.\d+' || echo 'unknown')" >> versions.lock
	@echo "Saved to versions.lock"

# ===========================================================================
# Messaging Channels
# ===========================================================================

.PHONY: telegram-setup
telegram-setup: ## Configure Telegram bot channel in OpenClaw
	@if [ -z "$(TELEGRAM_BOT_TOKEN)" ]; then \
		echo "Set TELEGRAM_BOT_TOKEN=... when invoking make telegram-setup"; \
		exit 1; \
	fi
	@$(OPENCLAW_POD_EXEC) sh -lc \
		'HOME=/sandbox openclaw --profile $(OPENCLAW_PROFILE) channels add --channel telegram --token "$(TELEGRAM_BOT_TOKEN)" && \
		echo "Telegram channel added. Send /start to your bot to verify."'

.PHONY: whatsapp-setup
whatsapp-setup: ## Enable the native WhatsApp channel in the OpenClaw named profile
	@$(OPENCLAW_POD_EXEC) sh -lc \
		'HOME=/sandbox openclaw --profile $(OPENCLAW_PROFILE) channels add --channel whatsapp && \
		HOME=/sandbox openclaw --profile $(OPENCLAW_PROFILE) config set channels.whatsapp.groupPolicy open && \
		HOME=/sandbox openclaw --profile $(OPENCLAW_PROFILE) config set channels.whatsapp.accounts.default.groupPolicy open && \
		echo "WhatsApp channel added. Run make whatsapp-login to scan the QR code."'

.PHONY: whatsapp-login
whatsapp-login: ## Start native WhatsApp QR login for the OpenClaw named profile
	@$(OPENCLAW_POD_EXEC_TTY) sh -lc \
		'HOME=/sandbox openclaw --profile $(OPENCLAW_PROFILE) channels login --channel whatsapp'

.PHONY: channels-status
channels-status: ## Show status of all messaging channels
	@$(OPENCLAW_POD_EXEC) sh -lc \
		'HOME=/sandbox openclaw --profile $(OPENCLAW_PROFILE) channels status --probe || HOME=/sandbox openclaw --profile $(OPENCLAW_PROFILE) channels list'

.PHONY: policy-telegram
policy-telegram: ## Add Telegram network policy to nemoclaw-main sandbox
	@TMP=$$(mktemp) && OUT=$$(mktemp) && \
		trap 'rm -f $$TMP $$OUT' EXIT && \
		$(OS_ENV) && openshell policy get nemoclaw-main --full | sed -n '/^---/,$$p' > $$TMP && \
		/usr/bin/python3 scripts/merge-openshell-policy-preset.py --input $$TMP --output $$OUT --preset telegram_bot && \
		$(OS_ENV) && openshell policy set nemoclaw-main --policy $$OUT --wait 2>&1 | tail -1

.PHONY: policy-whatsapp
policy-whatsapp: ## Add WhatsApp network policy to nemoclaw-main sandbox
	@TMP=$$(mktemp) && OUT=$$(mktemp) && \
		trap 'rm -f $$TMP $$OUT' EXIT && \
		$(OS_ENV) && openshell policy get nemoclaw-main --full | sed -n '/^---/,$$p' > $$TMP && \
		/usr/bin/python3 scripts/merge-openshell-policy-preset.py --input $$TMP --output $$OUT --preset whatsapp_web && \
		$(OS_ENV) && openshell policy set nemoclaw-main --policy $$OUT --wait 2>&1 | tail -1

# ===========================================================================
# End-to-End Tests
# ===========================================================================

.PHONY: e2e
e2e: ## Run scriptable E2E tests (basic chat, delegation)
	@echo "=== E2E Test Suite (Automated Subset) ==="
	@echo ""
	@echo "--- E2E-1: Basic Chat ---"
	@curl -sf http://localhost:11434/v1/chat/completions \
		-H "Content-Type: application/json" \
		-d '{"model":"nemotron-3-super:120b","messages":[{"role":"user","content":"What GPU are you running on?"}],"max_tokens":100}' | \
		python3 -c "import json,sys; r=json.load(sys.stdin); print('PASS' if r['choices'][0]['message']['content'] else 'FAIL')"
	@echo ""
	@echo "--- E2E-5: Inter-Agent Delegation ---"
	@$(MAKE) --no-print-directory delegate AGENT=openclaw PROMPT="Say hello in one sentence" 2>&1 | \
		python3 -c "import sys; out=sys.stdin.read(); print('PASS' if len(out.strip()) > 0 else 'FAIL')"
	@echo ""
	@echo "=== E2E Complete ==="

# ===========================================================================
# Disaster Recovery
# ===========================================================================

.PHONY: snapshot
snapshot: ## Save full system state for disaster recovery
	@echo "=== Creating NemoClaw Snapshot ==="
	@mkdir -p snapshots
	@SNAP="snapshots/nemoclaw-$$(date +%Y%m%d-%H%M%S)" && mkdir -p $$SNAP && \
		echo "Saving provider config..." && \
		$(OS_ENV) && openshell provider list > $$SNAP/providers.txt 2>&1 && \
		echo "Saving inference route..." && \
		openshell inference get > $$SNAP/inference-route.txt 2>&1 && \
		echo "Saving sandbox list..." && \
		openshell sandbox list > $$SNAP/sandboxes.txt 2>&1 && \
		echo "Saving versions..." && \
		$(MAKE) --no-print-directory versions > $$SNAP/versions.txt 2>&1 && \
		echo "Saving OpenClaw config..." && \
		$(OPENCLAW_POD_EXEC) sh -lc 'cat $(OPENCLAW_STATE_DIR)/openclaw.json 2>/dev/null' > $$SNAP/openclaw.json 2>&1 && \
		cp tests/.env $$SNAP/tests-env.bak 2>/dev/null || true && \
		echo "Snapshot saved to $$SNAP"

.PHONY: snapshot-list
snapshot-list: ## List available snapshots
	@ls -1d snapshots/nemoclaw-* 2>/dev/null || echo "No snapshots found. Run: make snapshot"

# ===========================================================================
# NemoClaw CLI
# ===========================================================================

.PHONY: nemoclaw-sync
nemoclaw-sync: ## Validate nemoclaw CLI state matches Makefile config
	@echo "Comparing nemoclaw CLI state with Makefile config..."
	@if [ -f ~/.nemoclaw/sandboxes.json ]; then \
		echo "  nemoclaw sandboxes.json exists"; \
		echo "  Expected sandboxes: nemoclaw-main, claude-dev, codex-dev, gemini-dev"; \
		cat ~/.nemoclaw/sandboxes.json | python3 -c "import json,sys; \
			data=json.load(sys.stdin); \
			names=set(s.get('name','') for s in data if isinstance(data, list)) if isinstance(data, list) else set(data.keys()); \
			expected={'nemoclaw-main','claude-dev','codex-dev','gemini-dev'}; \
			missing=expected-names; extra=names-expected; \
			print(f'  Missing: {missing}') if missing else None; \
			print(f'  Extra: {extra}') if extra else None; \
			print('  OK — in sync') if not missing and not extra else print('  DRIFT detected')"; \
	else \
		echo "  ~/.nemoclaw/sandboxes.json not found (nemoclaw CLI not used — this is expected)"; \
	fi

.PHONY: onboard-nemoclaw
onboard-nemoclaw: ## Run official nemoclaw onboard (hardened blueprint defaults)
	@echo "Running official NemoClaw onboard (blueprint with security defaults)..."
	@nemoclaw onboard
	@echo "Onboard complete. Run 'make post-onboard' to finish custom setup."

.PHONY: verify-blueprint
verify-blueprint: ## Compare running config against official NemoClaw blueprint
	@echo "Checking NemoClaw blueprint status..."
	@nemoclaw status 2>&1 || echo "nemoclaw CLI not installed — run: curl -fsSL https://www.nvidia.com/nemoclaw.sh | bash"

# ===========================================================================
# Git & CI
# ===========================================================================

.PHONY: push
push: ## Push current branch and create PR
	@git push -u origin $$(git branch --show-current) && \
		gh pr create --fill --repo macayaven/nemoclaw 2>/dev/null || echo "PR may already exist"

.PHONY: ci
ci: ## Run CI checks locally (same as pre-push hook)
	@./scripts/pre-push.sh

# ===========================================================================
# Help
# ===========================================================================

.PHONY: help
help: ## Show this help
	@echo ""
	@echo "NemoClaw — Multi-Machine AI Deployment"
	@echo "======================================="
	@echo ""
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Examples:"
	@echo "  make status                              # Full system overview"
	@echo "  make use-nemotron                        # Switch to 120B model"
	@echo "  make chat MSG='explain transformers'     # Quick chat"
	@echo "  make delegate AGENT=codex PROMPT='...'   # Delegate to agent"
	@echo "  make connect-claude                      # Shell into Claude sandbox"
	@echo ""
