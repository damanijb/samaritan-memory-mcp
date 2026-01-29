"""
Neo4j-based knowledge graph for entity relationships.

Configurable via environment variables:
- NEO4J_URI (default: bolt://localhost:7687)
- NEO4J_USER (default: neo4j)
- NEO4J_PASSWORD (required)
"""

import os
from neo4j import AsyncGraphDatabase
from typing import Optional
from datetime import datetime, timezone
import uuid

# Configuration from environment
NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")


class MemoryGraph:
    """Neo4j-based memory graph for entity relationships."""

    def __init__(self):
        self._driver = None

    async def connect(self):
        if not self._driver:
            self._driver = AsyncGraphDatabase.driver(
                NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
            )

    async def close(self):
        if self._driver:
            await self._driver.close()
            self._driver = None

    async def add_entity(self, name: str, entity_type: str, properties: dict = None) -> str:
        await self.connect()
        entity_id = str(uuid.uuid4())
        props = properties or {}
        props["name"] = name
        props["entity_type"] = entity_type
        props["created_at"] = datetime.now(timezone.utc).isoformat()

        async with self._driver.session() as session:
            result = await session.run(
                """
                MERGE (e:Entity {name: $name, entity_type: $type})
                ON CREATE SET e.uuid = $uuid, e += $props
                ON MATCH SET e += $props, e.updated_at = $updated
                RETURN e.uuid as uuid
                """,
                name=name, type=entity_type, uuid=entity_id,
                props=props, updated=datetime.now(timezone.utc).isoformat()
            )
            record = await result.single()
            return record["uuid"]

    async def add_relationship(self, from_entity: str, to_entity: str, relationship: str, properties: dict = None) -> bool:
        await self.connect()
        props = properties or {}
        props["created_at"] = datetime.now(timezone.utc).isoformat()

        async with self._driver.session() as session:
            await session.run(
                f"""
                MATCH (a:Entity {{name: $from_name}})
                MATCH (b:Entity {{name: $to_name}})
                MERGE (a)-[r:{relationship}]->(b)
                SET r += $props
                """,
                from_name=from_entity, to_name=to_entity, props=props
            )
        return True

    async def add_fact(self, subject: str, predicate: str, obj: str, context: str = None) -> str:
        await self.connect()
        fact_id = str(uuid.uuid4())

        async with self._driver.session() as session:
            await session.run(
                """
                MERGE (s:Entity {name: $subject})
                ON CREATE SET s.entity_type = 'Unknown', s.uuid = $s_uuid
                CREATE (f:Fact {
                    uuid: $uuid, subject: $subject, predicate: $predicate,
                    object: $object, context: $context, created_at: $created
                })
                CREATE (s)-[:HAS_FACT]->(f)
                """,
                subject=subject, predicate=predicate, object=obj,
                context=context, uuid=fact_id, s_uuid=str(uuid.uuid4()),
                created=datetime.now(timezone.utc).isoformat()
            )
        return fact_id

    async def get_entity(self, name: str) -> Optional[dict]:
        await self.connect()
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (e:Entity {name: $name})
                OPTIONAL MATCH (e)-[r]->(related:Entity)
                RETURN e, collect({type: type(r), target: related.name}) as relationships
                """,
                name=name
            )
            record = await result.single()
            if not record:
                return None
            entity = dict(record["e"])
            entity["relationships"] = [r for r in record["relationships"] if r["target"]]
            return entity

    async def get_facts_about(self, subject: str, limit: int = 20) -> list[dict]:
        await self.connect()
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (s:Entity {name: $subject})-[:HAS_FACT]->(f:Fact)
                RETURN f ORDER BY f.created_at DESC LIMIT $limit
                """,
                subject=subject, limit=limit
            )
            records = await result.data()
            return [dict(r["f"]) for r in records]

    async def search_entities(self, query: str, entity_type: str = None, limit: int = 10) -> list[dict]:
        await self.connect()
        type_filter = "AND e.entity_type = $type" if entity_type else ""
        async with self._driver.session() as session:
            result = await session.run(
                f"""
                MATCH (e:Entity)
                WHERE toLower(e.name) CONTAINS toLower($search_query) {type_filter}
                RETURN e LIMIT $limit
                """,
                search_query=query, type=entity_type, limit=limit
            )
            records = await result.data()
            return [dict(r["e"]) for r in records]

    async def search_facts(self, query: str, limit: int = 10) -> list[dict]:
        await self.connect()
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (f:Fact)
                WHERE toLower(f.subject) CONTAINS toLower($search_query)
                   OR toLower(f.predicate) CONTAINS toLower($search_query)
                   OR toLower(f.object) CONTAINS toLower($search_query)
                RETURN f ORDER BY f.created_at DESC LIMIT $limit
                """,
                search_query=query, limit=limit
            )
            records = await result.data()
            return [dict(r["f"]) for r in records]

    async def get_related(self, entity_name: str, relationship: str = None, depth: int = 1) -> list[dict]:
        await self.connect()
        rel_filter = f":{relationship}" if relationship else ""
        async with self._driver.session() as session:
            result = await session.run(
                f"""
                MATCH (e:Entity {{name: $name}})-[r{rel_filter}*1..{depth}]-(related:Entity)
                RETURN DISTINCT related, [rel in r | type(rel)] as relationship_path
                LIMIT 50
                """,
                name=entity_name
            )
            records = await result.data()
            return [{**dict(r["related"]), "path": r["relationship_path"]} for r in records]

    async def get_stats(self) -> dict:
        await self.connect()
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (e:Entity) WITH count(e) as entities
                MATCH (f:Fact) WITH entities, count(f) as facts
                MATCH ()-[r]->() WITH entities, facts, count(r) as relationships
                RETURN entities, facts, relationships
                """
            )
            record = await result.single()
            return {
                "entities": record["entities"],
                "facts": record["facts"],
                "relationships": record["relationships"],
            }


# Singleton
_graph = None

async def get_graph() -> MemoryGraph:
    global _graph
    if _graph is None:
        _graph = MemoryGraph()
    return _graph


# Convenience functions
async def add_entity(name, entity_type, properties=None):
    return await (await get_graph()).add_entity(name, entity_type, properties)

async def add_relationship(from_entity, to_entity, relationship, properties=None):
    return await (await get_graph()).add_relationship(from_entity, to_entity, relationship, properties)

async def add_fact(subject, predicate, obj, context=None):
    return await (await get_graph()).add_fact(subject, predicate, obj, context)

async def get_entity(name):
    return await (await get_graph()).get_entity(name)

async def get_facts_about(subject, limit=20):
    return await (await get_graph()).get_facts_about(subject, limit)

async def search_entities(query, entity_type=None, limit=10):
    return await (await get_graph()).search_entities(query, entity_type, limit)

async def search_facts(query, limit=10):
    return await (await get_graph()).search_facts(query, limit)

async def get_related(entity_name, relationship=None, depth=1):
    return await (await get_graph()).get_related(entity_name, relationship, depth)
