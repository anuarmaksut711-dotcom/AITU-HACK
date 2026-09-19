"""Bounded correction reconciliation, with exact citations and chronological checks."""

import json
from difflib import SequenceMatcher

from aimeet_api.modules.intelligence.evidence import locate
from aimeet_api.modules.intelligence.schemas import Revision
from aimeet_api.modules.rag.providers import RagError


def compact(card):
    return {
        key: card[key]
        for key in (
            "id",
            "kind",
            "title",
            "assignee",
            "due_text",
            "agreement",
            "quote",
            "start_char",
        )
    }


def reconciliation_batches(cards, limit=24000):
    candidates = sorted(
        (c for c in cards if c["kind"] in {"task", "decision"}),
        key=lambda c: c["start_char"],
    )
    if len(candidates) < 2:
        return
    if len(json.dumps([compact(c) for c in candidates], ensure_ascii=False)) <= limit:
        yield candidates
        return
    # For long meetings retrieve related earlier candidates across the entire
    # meeting, instead of losing corrections at the transcription chunk boundary.
    # This is a lexical shortlist, not a guarantee of semantic recall.
    for i, current in enumerate(candidates):
        scored = [
            (SequenceMatcher(None, c["title"].casefold(), current["title"].casefold()).ratio(), c)
            for c in candidates[:i]
            if c["kind"] == current["kind"]
        ]
        related = [
            c
            for score, c in sorted(scored, key=lambda pair: pair[0], reverse=True)[:6]
            if score >= 0.45
        ]
        if related:
            while (
                related
                and len(json.dumps([compact(c) for c in [*related, current]], ensure_ascii=False))
                > limit
            ):
                related.pop()
            if not related:
                continue
            batch = sorted([*related, current], key=lambda c: c["start_char"])
            yield batch


def reconcile(cards, transcript, provider, checkpoint):
    active = {c["id"]: c for c in cards}
    changed = False
    for batch in reconciliation_batches(cards):
        current = [c for c in batch if c["id"] in active]
        if len(current) < 2:
            continue
        checkpoint()
        result = provider.reconcile(json.dumps([compact(c) for c in current], ensure_ascii=False))
        allowed = {c["id"] for c in current}
        # Validate the complete response before applying any of its links.
        seen = set()
        for link in result.links:
            early_id, late_id = str(link.earlier_id), str(link.later_id)
            if early_id not in allowed or late_id not in allowed or early_id in seen:
                raise RagError("INVALID_CORRECTION", 502)
            early, late = active[early_id], active[late_id]
            if (
                early["kind"] != late["kind"]
                or early["start_char"] >= late["start_char"]
                or link.quote not in late["quote"]
                or (link.resolved and late["agreement"] != "confirmed")
            ):
                raise RagError("INVALID_CORRECTION", 502)
            seen.add(early_id)
        for link in sorted(
            result.links, key=lambda item: active[str(item.earlier_id)]["start_char"]
        ):
            early, late = active[str(link.earlier_id)], active[str(link.later_id)]
            changed = True
            if not link.resolved:
                early["agreement"] = late["agreement"] = "unclear"
                continue
            revisions = [*early.get("revisions", [])]
            before = locate(transcript, early["quote"], early["start_char"])
            after = locate(transcript, late["quote"], late["start_char"])
            for field in ("due_text", "assignee"):
                old, new = early[field], late[field]
                if old and new and old != new and old in before.quote and new in after.quote:
                    revisions.append(
                        Revision(
                            field=field,
                            before_value=old,
                            after_value=new,
                            before=before,
                            after=after,
                        ).model_dump(mode="json")
                    )
            if early["kind"] == "decision":
                revisions.append(
                    Revision(
                        field="decision",
                        before_value=early["title"],
                        after_value=late["title"],
                        before=before,
                        after=after,
                    ).model_dump(mode="json")
                )
            # If there is no source-backed change to retain, preserve both cards.
            if len(revisions) == len(early.get("revisions", [])):
                early["agreement"] = late["agreement"] = "unclear"
                continue
            late["revisions"] = [*revisions, *late.get("revisions", [])]
            del active[early["id"]]
    return list(active.values()), changed
