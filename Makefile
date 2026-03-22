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
# Start & Stop
# ===========================================================================

.PHONY: start
start: start-gateway start-forward start-openclaw ## Start everything on Spark
	@echo "NemoClaw started. Run 'make status' to verify."

.PHONY: start-gateway
start-gateway: ## Start OpenShell gateway
	@$(OS_ENV) && openshell gateway start 2>&1 | tail -5

.PHONY: start-gateway-fresh
start-gateway-fresh: ## Recreate and start gateway (clean slate)
	@$(OS_ENV) && openshell gateway start --recreate

.PHONY: start-forward
start-forward: ## Start port forward for UI (18789)
	@$(OS_ENV) && openshell forward start 18789 nemoclaw-main --background 2>/dev/null || \
		echo "Forward may already be running"

.PHONY: start-openclaw
start-openclaw: ## Start OpenClaw gateway inside sandbox
	@$(OS_ENV) && $(SSH_SANDBOX) \
		'pgrep -f "openclaw gateway" > /dev/null && echo "Already running" || \
		(openclaw gateway run > /tmp/gateway.log 2>&1 & echo "OpenClaw gateway started")'

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
versions: ## Show all component versions
	@echo "Ollama:    $$(ollama --version 2>/dev/null || echo 'not found')"
	@echo "LM Studio: $$(lms --version 2>/dev/null || echo 'not found')"
	@echo "OpenShell: $$($(OS_ENV) && openshell --version 2>/dev/null || echo 'not found')"
	@echo "NemoClaw:  $$(nemoclaw --version 2>/dev/null || echo 'not found')"
	@echo "Node.js:   $$(node --version 2>/dev/null || echo 'not found')"

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
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Examples:"
	@echo "  make status                              # Full system overview"
	@echo "  make use-nemotron                        # Switch to 120B model"
	@echo "  make chat MSG='explain transformers'     # Quick chat"
	@echo "  make delegate AGENT=codex PROMPT='...'   # Delegate to agent"
	@echo "  make connect-claude                      # Shell into Claude sandbox"
	@echo ""
