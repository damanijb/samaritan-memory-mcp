#!/usr/bin/env bash
set -euo pipefail

# Samaritan Memory MCP — Setup Script
# Installs the MCP server and configures it for Claude Code and/or Claude Desktop.

REPO="https://github.com/damanijb/samaritan-memory-mcp.git"

# Defaults (override with env vars or flags)
QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
NEO4J_URI="${NEO4J_URI:-bolt://localhost:7687}"
NEO4J_USER="${NEO4J_USER:-neo4j}"
NEO4J_PASSWORD="${NEO4J_PASSWORD:-}"
VLLM_RERANKER_URL="${VLLM_RERANKER_URL:-http://localhost:8004}"
EMBEDDING_MODEL="${EMBEDDING_MODEL:-snowflake-arctic-embed:335m}"
COLLECTION_NAME="${COLLECTION_NAME:-samaritan_memory}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[x]${NC} $*"; }

# ── Parse flags ──────────────────────────────────────────────────────────────

INSTALL_CLAUDE_CODE=false
INSTALL_CLAUDE_DESKTOP=false
SKIP_PROMPTS=false

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --claude-code       Configure for Claude Code (~/.claude/settings.json)"
    echo "  --claude-desktop    Configure for Claude Desktop (claude_desktop_config.json)"
    echo "  --both              Configure for both"
    echo "  --neo4j-password X  Set Neo4j password"
    echo "  --qdrant-url X      Set Qdrant URL"
    echo "  --ollama-url X      Set Ollama URL"
    echo "  --neo4j-uri X       Set Neo4j URI"
    echo "  --yes               Skip confirmation prompts"
    echo "  --help              Show this help"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --claude-code)      INSTALL_CLAUDE_CODE=true; shift ;;
        --claude-desktop)   INSTALL_CLAUDE_DESKTOP=true; shift ;;
        --both)             INSTALL_CLAUDE_CODE=true; INSTALL_CLAUDE_DESKTOP=true; shift ;;
        --neo4j-password)   NEO4J_PASSWORD="$2"; shift 2 ;;
        --qdrant-url)       QDRANT_URL="$2"; shift 2 ;;
        --ollama-url)       OLLAMA_URL="$2"; shift 2 ;;
        --neo4j-uri)        NEO4J_URI="$2"; shift 2 ;;
        --yes|-y)           SKIP_PROMPTS=true; shift ;;
        --help|-h)          usage ;;
        *)                  error "Unknown option: $1"; usage ;;
    esac
done

# If no target specified, ask
if ! $INSTALL_CLAUDE_CODE && ! $INSTALL_CLAUDE_DESKTOP; then
    echo "Where do you want to install the MCP server?"
    echo "  1) Claude Code"
    echo "  2) Claude Desktop"
    echo "  3) Both"
    read -rp "Choice [1/2/3]: " choice
    case $choice in
        1) INSTALL_CLAUDE_CODE=true ;;
        2) INSTALL_CLAUDE_DESKTOP=true ;;
        3) INSTALL_CLAUDE_CODE=true; INSTALL_CLAUDE_DESKTOP=true ;;
        *) error "Invalid choice"; exit 1 ;;
    esac
fi

# Prompt for Neo4j password if not set
if [[ -z "$NEO4J_PASSWORD" ]]; then
    read -rsp "Neo4j password: " NEO4J_PASSWORD
    echo
fi

# ── Install package ──────────────────────────────────────────────────────────

info "Installing samaritan-memory-mcp..."
pip install --quiet "git+${REPO}" 2>/dev/null || pip install "git+${REPO}"

# Verify install
if ! command -v samaritan-memory &>/dev/null; then
    # Check if it's in a pip --user path
    SAMARITAN_CMD="$(python3 -c 'import shutil; print(shutil.which("samaritan-memory") or "")' 2>/dev/null)"
    if [[ -z "$SAMARITAN_CMD" ]]; then
        SAMARITAN_CMD="python3 -m samaritan_memory.server"
        warn "samaritan-memory not on PATH, using: $SAMARITAN_CMD"
    fi
else
    SAMARITAN_CMD="samaritan-memory"
fi

info "Installed. Command: $SAMARITAN_CMD"

# ── Build MCP config block ──────────────────────────────────────────────────

MCP_ENTRY=$(python3 -c "
import json
entry = {
    'command': '$SAMARITAN_CMD',
    'env': {
        'QDRANT_URL': '$QDRANT_URL',
        'OLLAMA_URL': '$OLLAMA_URL',
        'NEO4J_URI': '$NEO4J_URI',
        'NEO4J_USER': '$NEO4J_USER',
        'NEO4J_PASSWORD': '$NEO4J_PASSWORD',
        'VLLM_RERANKER_URL': '$VLLM_RERANKER_URL',
        'EMBEDDING_MODEL': '$EMBEDDING_MODEL',
        'COLLECTION_NAME': '$COLLECTION_NAME'
    }
}
# If command has spaces (python3 -m ...), split into command + args
parts = '$SAMARITAN_CMD'.split()
if len(parts) > 1:
    entry['command'] = parts[0]
    entry['args'] = parts[1:]
print(json.dumps(entry, indent=2))
")

# ── Helper: merge MCP config into a JSON file ───────────────────────────────

merge_mcp_config() {
    local config_file="$1"
    local config_dir
    config_dir="$(dirname "$config_file")"

    mkdir -p "$config_dir"

    if [[ ! -f "$config_file" ]]; then
        echo '{}' > "$config_file"
    fi

    python3 -c "
import json, sys

config_file = '$config_file'
mcp_entry = json.loads('''$MCP_ENTRY''')

with open(config_file) as f:
    config = json.load(f)

if 'mcpServers' not in config:
    config['mcpServers'] = {}

config['mcpServers']['samaritan-memory'] = mcp_entry

with open(config_file, 'w') as f:
    json.dump(config, f, indent=2)
    f.write('\n')

print(f'Updated {config_file}')
"
}

# ── Claude Code ──────────────────────────────────────────────────────────────

if $INSTALL_CLAUDE_CODE; then
    CLAUDE_CODE_CONFIG="$HOME/.claude/settings.json"
    info "Configuring Claude Code..."
    merge_mcp_config "$CLAUDE_CODE_CONFIG"
    info "Claude Code configured: $CLAUDE_CODE_CONFIG"
fi

# ── Claude Desktop ───────────────────────────────────────────────────────────

if $INSTALL_CLAUDE_DESKTOP; then
    # Detect OS for config path
    case "$(uname -s)" in
        Darwin)
            CLAUDE_DESKTOP_CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
            ;;
        Linux)
            CLAUDE_DESKTOP_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/Claude/claude_desktop_config.json"
            ;;
        MINGW*|MSYS*|CYGWIN*)
            CLAUDE_DESKTOP_CONFIG="$APPDATA/Claude/claude_desktop_config.json"
            ;;
        *)
            warn "Unknown OS. Using ~/.config/Claude/claude_desktop_config.json"
            CLAUDE_DESKTOP_CONFIG="$HOME/.config/Claude/claude_desktop_config.json"
            ;;
    esac

    info "Configuring Claude Desktop..."
    merge_mcp_config "$CLAUDE_DESKTOP_CONFIG"
    info "Claude Desktop configured: $CLAUDE_DESKTOP_CONFIG"
fi

# ── Done ─────────────────────────────────────────────────────────────────────

echo ""
info "Setup complete!"
echo ""
echo "  Services needed on the backend:"
echo "    - Qdrant at $QDRANT_URL"
echo "    - Ollama at $OLLAMA_URL (model: $EMBEDDING_MODEL)"
echo "    - Neo4j  at $NEO4J_URI"
echo "    - vLLM   at $VLLM_RERANKER_URL (optional reranker)"
echo ""
echo "  Restart Claude Code / Claude Desktop to activate."
