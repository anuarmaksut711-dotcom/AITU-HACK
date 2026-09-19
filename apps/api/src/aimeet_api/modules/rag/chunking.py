"""Lossless, deterministic hierarchy. Offsets are Unicode code points, end exclusive."""

import hashlib
import json
import re
import uuid

from aimeet_api.core.config import Settings
from aimeet_api.modules.rag.models import RagEdge, RagNode

CHUNKER_VERSION = "turn-boundaries-v1"


def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def embedding_profile(config: Settings) -> str:
    return source_hash(
        json.dumps(
            {
                "provider": config.rag_embedding_provider,
                "endpoint": "https://api.openai.com/v1"
                if config.rag_embedding_provider == "openai"
                else (config.rag_embedding_local_url or config.rag_local_url).rstrip("/"),
                "model": config.rag_embedding_model,
                "dimensions": config.rag_embedding_dimensions,
                "revision": config.rag_embedding_revision,
                "chunker": CHUNKER_VERSION,
                "child": config.rag_child_chars,
                "parent": config.rag_parent_chars,
            },
            sort_keys=True,
        )
    )


def spans(text: str, start: int, end: int, size: int):
    while start < end:
        stop = min(start + size, end)
        if stop < end:
            # Prefer a speaker turn/paragraph, then sentence/word, in the latter half.
            window = text[start + size // 2 : stop]
            boundaries = [m.end() for m in re.finditer(r"\n", window)]
            if not boundaries:
                boundaries = [m.end() for m in re.finditer(r"[.!?]\s+|\s+", window)]
            if boundaries:
                stop = start + size // 2 + boundaries[-1]
        yield start, stop
        start = stop


def build_graph(index_id: uuid.UUID, text: str, config: Settings):
    parents, children, edges = [], [], []
    for ordinal, (start, end) in enumerate(spans(text, 0, len(text), config.rag_parent_chars)):
        parent_id = uuid.uuid5(index_id, f"parent:{start}:{end}")
        parents.append(
            RagNode(
                id=parent_id,
                index_id=index_id,
                kind="parent",
                ordinal=ordinal,
                start_char=start,
                end_char=end,
                text=text[start:end],
            )
        )
        for cs, ce in spans(text, start, end, config.rag_child_chars):
            child_id = uuid.uuid5(index_id, f"child:{cs}:{ce}")
            children.append(
                RagNode(
                    id=child_id,
                    index_id=index_id,
                    kind="child",
                    ordinal=len(children),
                    parent_id=parent_id,
                    start_char=cs,
                    end_char=ce,
                    text=text[cs:ce],
                )
            )
            for source, target, relation in (
                (parent_id, child_id, "child"),
                (child_id, parent_id, "parent"),
            ):
                edges.append(
                    RagEdge(
                        index_id=index_id, source_id=source, target_id=target, relation=relation
                    )
                )
    for level in (parents, children):
        for left, right in zip(level, level[1:], strict=False):
            for source, target, relation in (
                (left.id, right.id, "next"),
                (right.id, left.id, "previous"),
            ):
                edges.append(
                    RagEdge(
                        index_id=index_id, source_id=source, target_id=target, relation=relation
                    )
                )
    return parents, children, edges
