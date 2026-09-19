"""Hybrid multi-level retrieval, graph expansion, deduplication and source budgeting."""

import math
import re
from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session, defer

from aimeet_api.core.config import Settings
from aimeet_api.modules.rag.models import RagEdge, RagNode
from aimeet_api.modules.rag.schemas import Source


def tokens(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", text.casefold(), flags=re.UNICODE)


def lexical_scores(nodes: list[RagNode], question: str) -> dict:
    """BM25 over this bounded meeting, with Unicode tokens for RU/KK/EN."""
    query = set(tokens(question))
    documents = {node.id: Counter(tokens(node.text)) for node in nodes}
    lengths = {node_id: sum(counts.values()) for node_id, counts in documents.items()}
    average = sum(lengths.values()) / max(len(nodes), 1) or 1
    frequency = Counter(term for counts in documents.values() for term in query if term in counts)
    scores = {}
    for node_id, counts in documents.items():
        score = 0.0
        for term in query:
            tf = counts[term]
            if tf:
                idf = math.log(1 + (len(nodes) - frequency[term] + 0.5) / (frequency[term] + 0.5))
                score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * lengths[node_id] / average))
        if score > 0:
            scores[node_id] = score
    return scores


def retrieve(db: Session, index_id, question: str, vector: list[float], config: Settings):
    return retrieve_many(db, [index_id], question, vector, config)


def retrieve_many(db: Session, index_ids, question: str, vector: list[float], config: Settings):
    # Maximum transcript length is 200k code points; scoped exact search preserves recall.
    nodes = list(
        db.scalars(
            select(RagNode)
            .options(defer(RagNode.embedding))
            .where(
                RagNode.index_id.in_(index_ids),
            )
            .order_by(RagNode.start_char, RagNode.kind)
        )
    )
    by_id = {node.id: node for node in nodes}
    lexical = {}
    for kind in ("child", "parent"):
        lexical.update(lexical_scores([node for node in nodes if node.kind == kind], question))
    semantic = {}
    for kind in ("child", "parent"):
        if db.bind.dialect.name == "postgresql":
            distance = RagNode.embedding.cosine_distance(vector)
            rows = db.execute(
                select(RagNode.id, distance)
                .where(
                    RagNode.index_id.in_(index_ids),
                    RagNode.kind == kind,
                )
                .order_by(distance, RagNode.start_char)
                .limit(config.rag_candidate_count)
            )
            ranked = [(node_id, 1 - float(value)) for node_id, value in rows]
        else:
            # Test-only exact reference implementation; never used in PostgreSQL deployments.
            ranked = sorted(
                [
                    (node.id, sum(a * b for a, b in zip(node.embedding, vector, strict=True)))
                    for node in nodes
                    if node.kind == kind
                ],
                key=lambda item: -item[1],
            )[: config.rag_candidate_count]
        semantic.update(
            {node_id: score for node_id, score in ranked if score >= config.rag_min_similarity}
        )
    fused = defaultdict(float)
    for kind in ("child", "parent"):
        for scores in (semantic, lexical):
            ranked = sorted(
                (node_id for node_id in scores if by_id[node_id].kind == kind),
                key=lambda node_id: (-scores[node_id], by_id[node_id].start_char),
            )
            for rank, node_id in enumerate(ranked[: config.rag_candidate_count], 1):
                # Parent vectors improve broad recall; children retain retrieval priority.
                fused[node_id] += (1 if kind == "child" else 0.8) / (60 + rank)
    seeds = sorted(fused, key=lambda node_id: (-fused[node_id], by_id[node_id].start_char))[
        : config.rag_top_k
    ]
    adjacency = defaultdict(list)
    for edge in db.scalars(select(RagEdge).where(RagEdge.index_id.in_(index_ids))):
        adjacency[(edge.source_id, edge.relation)].append(edge.target_id)

    chosen: dict = {}
    used = 0

    def include(node_id, reason):
        nonlocal used
        node = by_id[node_id]
        # Parent promotion replaces contained hits without duplicating their text.
        if any(
            existing.index_id == node.index_id
            and existing.start_char <= node.start_char
            and existing.end_char >= node.end_char
            for existing, _ in chosen.values()
        ):
            return
        contained = [
            key
            for key, (existing, _) in chosen.items()
            if existing.index_id == node.index_id
            and node.start_char <= existing.start_char
            and node.end_char >= existing.end_char
        ]
        freed = sum(len(chosen[key][0].text) + 160 for key in contained)
        cost = len(node.text) + 160  # Reserve space for source labels/offset metadata.
        if used - freed + cost > config.rag_context_chars:
            return
        for key in contained:
            del chosen[key]
        chosen[node_id] = (node, reason)
        used = used - freed + cost

    for node_id in seeds:
        include(node_id, "hit")
    for node_id in seeds:
        frontier = [node_id]
        for _ in range(config.rag_neighbor_radius):
            frontier = [
                target
                for source in frontier
                for direction in ("next", "previous")
                for target in adjacency[(source, direction)]
            ]
            for target in frontier:
                include(target, "neighbor")
    for node_id in seeds:
        for parent_id in adjacency[(node_id, "parent")]:
            include(parent_id, "parent")
    return [
        Source(
            source_id=f"S{number}",
            node_id=node.id,
            parent_id=node.parent_id,
            start_char=node.start_char,
            end_char=node.end_char,
            text=node.text,
            reason=reason,
        )
        for number, (node, reason) in enumerate(
            sorted(chosen.values(), key=lambda item: item[0].start_char),
            1,
        )
    ]
