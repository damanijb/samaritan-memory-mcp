#!/usr/bin/env python3
"""
Samaritan Memory MCP Server

Hybrid memory system combining:
- Qdrant: Semantic vector search with embeddings
- Neo4j: Graph relationships between entities

Run: samaritan-memory (after pip install)
  or: python -m samaritan_memory.server
"""

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
import json
import asyncio

from . import qdrant
from . import graph

server = Server("samaritan-memory")


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="memory_add",
            description="Add a memory with semantic embedding. Use for storing insights, decisions, preferences, events.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The memory content to store"},
                    "memory_type": {
                        "type": "string",
                        "enum": ["preference", "decision", "insight", "event", "task"],
                        "description": "Type of memory", "default": "insight"
                    },
                    "importance": {
                        "type": "string",
                        "enum": ["low", "normal", "high", "critical"],
                        "description": "Importance level", "default": "normal"
                    },
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional tags for filtering"}
                },
                "required": ["content"]
            }
        ),
        Tool(
            name="memory_search",
            description="Search memories by semantic similarity. Returns most relevant memories for a query.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "description": "Max results (default 5)", "default": 5},
                    "memory_type": {
                        "type": "string",
                        "enum": ["preference", "decision", "insight", "event", "task"],
                        "description": "Filter by type (optional)"
                    },
                    "rerank": {"type": "boolean", "description": "Use reranker for better relevance (default true)", "default": True}
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="memory_recent",
            description="Get most recent memories",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max results (default 10)", "default": 10},
                    "memory_type": {
                        "type": "string",
                        "enum": ["preference", "decision", "insight", "event", "task"],
                        "description": "Filter by type (optional)"
                    }
                }
            }
        ),
        Tool(name="memory_stats", description="Get memory system statistics", inputSchema={"type": "object", "properties": {}}),
        Tool(
            name="graph_add_entity",
            description="Add an entity to the knowledge graph",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Entity name"},
                    "entity_type": {"type": "string", "description": "Type (e.g., 'Table', 'Person', 'System', 'Concept')"},
                    "properties": {"type": "object", "description": "Additional properties"}
                },
                "required": ["name", "entity_type"]
            }
        ),
        Tool(
            name="graph_add_relationship",
            description="Add a relationship between two entities",
            inputSchema={
                "type": "object",
                "properties": {
                    "from_entity": {"type": "string", "description": "Source entity name"},
                    "to_entity": {"type": "string", "description": "Target entity name"},
                    "relationship": {"type": "string", "description": "Relationship type (e.g., 'CONTAINS', 'USES')"}
                },
                "required": ["from_entity", "to_entity", "relationship"]
            }
        ),
        Tool(
            name="graph_add_fact",
            description="Add a fact as subject-predicate-object triple",
            inputSchema={
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "Subject entity"},
                    "predicate": {"type": "string", "description": "Relationship/predicate"},
                    "object": {"type": "string", "description": "Object value or entity"},
                    "context": {"type": "string", "description": "Optional context or source"}
                },
                "required": ["subject", "predicate", "object"]
            }
        ),
        Tool(
            name="graph_get_entity",
            description="Get an entity with its relationships",
            inputSchema={"type": "object", "properties": {"name": {"type": "string", "description": "Entity name"}}, "required": ["name"]}
        ),
        Tool(
            name="graph_get_facts",
            description="Get all facts about a subject",
            inputSchema={
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "Subject to get facts about"},
                    "limit": {"type": "integer", "description": "Max facts (default 20)", "default": 20}
                },
                "required": ["subject"]
            }
        ),
        Tool(
            name="graph_search",
            description="Search entities and facts by keyword",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "search_type": {"type": "string", "enum": ["entities", "facts", "both"], "default": "both"},
                    "limit": {"type": "integer", "default": 10}
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="graph_get_related",
            description="Get entities related to a given entity",
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_name": {"type": "string", "description": "Entity to find relations for"},
                    "relationship": {"type": "string", "description": "Filter by relationship type (optional)"},
                    "depth": {"type": "integer", "description": "Traversal depth (default 1)", "default": 1}
                },
                "required": ["entity_name"]
            }
        ),
        Tool(
            name="recall",
            description="Hybrid recall - searches both semantic memory and knowledge graph",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to recall"},
                    "limit": {"type": "integer", "description": "Max results per source", "default": 5}
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="record",
            description="Store information in both semantic memory and graph (when entities/facts are present)",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Content to store"},
                    "memory_type": {"type": "string", "enum": ["preference", "decision", "insight", "event", "task"], "default": "insight"},
                    "entities": {
                        "type": "array",
                        "items": {"type": "object", "properties": {"name": {"type": "string"}, "type": {"type": "string"}}},
                        "description": "Entities to add to graph"
                    },
                    "facts": {
                        "type": "array",
                        "items": {"type": "object", "properties": {"subject": {"type": "string"}, "predicate": {"type": "string"}, "object": {"type": "string"}}},
                        "description": "Facts to add to graph"
                    }
                },
                "required": ["content"]
            }
        )
    ]


def _parse_json_arg(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    for key in ["properties", "tags", "entities", "facts"]:
        if key in arguments:
            arguments[key] = _parse_json_arg(arguments[key])

    try:
        if name == "memory_add":
            memory_id = await qdrant.add_memory(
                content=arguments["content"],
                memory_type=arguments.get("memory_type", "insight"),
                importance=arguments.get("importance", "normal"),
                tags=arguments.get("tags"),
            )
            return [TextContent(type="text", text=json.dumps({"success": True, "memory_id": memory_id}))]

        elif name == "memory_search":
            results = await qdrant.search_memory(
                query=arguments["query"],
                limit=arguments.get("limit", 5),
                memory_type=arguments.get("memory_type"),
                rerank=arguments.get("rerank", True),
            )
            return [TextContent(type="text", text=json.dumps({"count": len(results), "memories": results}, indent=2))]

        elif name == "memory_recent":
            results = await qdrant.get_recent_memories(
                limit=arguments.get("limit", 10),
                memory_type=arguments.get("memory_type"),
            )
            return [TextContent(type="text", text=json.dumps({"count": len(results), "memories": results}, indent=2))]

        elif name == "memory_stats":
            stats = await qdrant.get_memory_stats()
            graph_instance = await graph.get_graph()
            try:
                graph_stats = await graph_instance.get_stats()
            except Exception:
                graph_stats = {"error": "Graph unavailable"}
            return [TextContent(type="text", text=json.dumps({"qdrant": stats, "neo4j": graph_stats}, indent=2))]

        elif name == "graph_add_entity":
            entity_id = await graph.add_entity(arguments["name"], arguments["entity_type"], arguments.get("properties"))
            return [TextContent(type="text", text=json.dumps({"success": True, "entity_id": entity_id}))]

        elif name == "graph_add_relationship":
            success = await graph.add_relationship(arguments["from_entity"], arguments["to_entity"], arguments["relationship"])
            return [TextContent(type="text", text=json.dumps({"success": success}))]

        elif name == "graph_add_fact":
            fact_id = await graph.add_fact(arguments["subject"], arguments["predicate"], arguments["object"], arguments.get("context"))
            return [TextContent(type="text", text=json.dumps({"success": True, "fact_id": fact_id}))]

        elif name == "graph_get_entity":
            entity = await graph.get_entity(arguments["name"])
            return [TextContent(type="text", text=json.dumps(entity, indent=2))]

        elif name == "graph_get_facts":
            facts = await graph.get_facts_about(arguments["subject"], arguments.get("limit", 20))
            return [TextContent(type="text", text=json.dumps({"count": len(facts), "facts": facts}, indent=2))]

        elif name == "graph_search":
            search_type = arguments.get("search_type", "both")
            limit = arguments.get("limit", 10)
            results = {}
            if search_type in ("entities", "both"):
                results["entities"] = await graph.search_entities(arguments["query"], limit=limit)
            if search_type in ("facts", "both"):
                results["facts"] = await graph.search_facts(arguments["query"], limit=limit)
            return [TextContent(type="text", text=json.dumps(results, indent=2))]

        elif name == "graph_get_related":
            related = await graph.get_related(arguments["entity_name"], arguments.get("relationship"), arguments.get("depth", 1))
            return [TextContent(type="text", text=json.dumps({"count": len(related), "related": related}, indent=2))]

        elif name == "recall":
            query = arguments["query"]
            limit = arguments.get("limit", 5)
            memories, entities, facts = await asyncio.gather(
                qdrant.search_memory(query, limit=limit, rerank=True),
                graph.search_entities(query, limit=limit),
                graph.search_facts(query, limit=limit),
                return_exceptions=True
            )
            result = {
                "memories": memories if not isinstance(memories, Exception) else [],
                "entities": entities if not isinstance(entities, Exception) else [],
                "facts": facts if not isinstance(facts, Exception) else [],
            }
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "record":
            results = {"memory_id": None, "entities": [], "facts": []}
            memory_id = await qdrant.add_memory(
                content=arguments["content"],
                memory_type=arguments.get("memory_type", "insight"),
            )
            results["memory_id"] = memory_id
            for entity in arguments.get("entities", []):
                await graph.add_entity(entity["name"], entity.get("type", "Unknown"))
                results["entities"].append(entity["name"])
            for fact in arguments.get("facts", []):
                await graph.add_fact(fact["subject"], fact["predicate"], fact["object"])
                results["facts"].append(f"{fact['subject']} {fact['predicate']} {fact['object']}")
            return [TextContent(type="text", text=json.dumps({"success": True, **results}, indent=2))]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=json.dumps({"error": str(e), "tool": name}))]


async def _main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    asyncio.run(_main())


if __name__ == "__main__":
    main()
