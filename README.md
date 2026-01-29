# Samaritan Memory MCP Server

Hybrid memory system for AI agents, exposed as an [MCP](https://modelcontextprotocol.io/) server. Combines:

- **Qdrant** — Semantic vector search with automatic deduplication
- **Neo4j** — Knowledge graph with entities, relationships, and facts
- **Reranker** — Optional Qwen3-Reranker via vLLM for improved relevance

## Prerequisites

- **Qdrant** running (vector database)
- **Ollama** running with an embedding model (default: `snowflake-arctic-embed:335m`)
- **Neo4j** running (graph database)
- **vLLM** with reranker model (optional, for better search relevance)

## Quick Setup

One command to install and configure for Claude Code and/or Claude Desktop:

```bash
curl -fsSL https://raw.githubusercontent.com/damanijb/samaritan-memory-mcp/main/setup.sh | bash -s -- --both --neo4j-password YOUR_PASSWORD
```

Or with custom backend URLs (e.g., pointing to a Tailscale server):

```bash
curl -fsSL https://raw.githubusercontent.com/damanijb/samaritan-memory-mcp/main/setup.sh | bash -s -- \
  --both \
  --neo4j-password YOUR_PASSWORD \
  --qdrant-url http://samaritan.tail8478e1.ts.net:6333 \
  --ollama-url http://samaritan.tail8478e1.ts.net:11434 \
  --neo4j-uri bolt://samaritan.tail8478e1.ts.net:7687

```

Flags: `--claude-code`, `--claude-desktop`, `--both`, `--yes` (skip prompts).

## Manual Install

```bash
pip install git+https://github.com/damanijb/samaritan-memory-mcp.git
```

## Configuration

All settings via environment variables. Copy `.env.example` to `.env` and edit:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|----------|---------|-------------|
| `QDRANT_URL` | `http://localhost:6333` | Qdrant endpoint |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama endpoint |
| `EMBEDDING_MODEL` | `snowflake-arctic-embed:335m` | Ollama embedding model |
| `EMBEDDING_DIM` | `1024` | Embedding dimensions |
| `COLLECTION_NAME` | `samaritan_memory` | Qdrant collection name |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j endpoint |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | *(required)* | Neo4j password |
| `VLLM_RERANKER_URL` | `http://localhost:8004` | vLLM reranker (optional) |
| `RERANKER_MODEL` | `Qwen/Qwen3-Reranker-0.6B` | Reranker model name |

## Usage with Claude Code

Add to your `claude_desktop_config.json` or `.claude/settings.json`:

```json
{
  "mcpServers": {
    "samaritan-memory": {
      "command": "samaritan-memory",
      "env": {
        "NEO4J_PASSWORD": "your-password",
        "QDRANT_URL": "http://your-qdrant:6333",
        "NEO4J_URI": "bolt://your-neo4j:7687"
      }
    }
  }
}
```

For Tailscale devices, point the URLs to your Tailscale hostnames:

```json
{
  "env": {
    "QDRANT_URL": "http://your-server.tail1234.ts.net:6333",
    "NEO4J_URI": "bolt://your-server.tail1234.ts.net:7687",
    "OLLAMA_URL": "http://your-server.tail1234.ts.net:11434"
  }
}
```

## Tools

### Semantic Memory (Qdrant)
- `memory_add` — Store a memory with semantic embedding and auto-deduplication
- `memory_search` — Search by semantic similarity with optional reranking
- `memory_recent` — Get most recent memories
- `memory_stats` — System statistics

### Knowledge Graph (Neo4j)
- `graph_add_entity` — Add an entity node
- `graph_add_relationship` — Link two entities
- `graph_add_fact` — Store subject-predicate-object triple
- `graph_get_entity` — Get entity with relationships
- `graph_get_facts` — Get facts about a subject
- `graph_search` — Search entities and facts
- `graph_get_related` — Traverse related entities

### Hybrid
- `recall` — Search both semantic memory and knowledge graph in parallel
- `record` — Store to both systems atomically (memory + entities + facts)

## Architecture

```
┌─────────────────────────────────────┐
│         MCP Server (stdio)          │
├─────────────────────────────────────┤
│  recall/record (hybrid tools)       │
├────────────────┬────────────────────┤
│  Qdrant        │  Neo4j             │
│  (semantic)    │  (graph)           │
├────────────────┤                    │
│  Ollama        │                    │
│  (embeddings)  │                    │
├────────────────┤                    │
│  vLLM          │                    │
│  (reranker)    │                    │
└────────────────┴────────────────────┘
```
