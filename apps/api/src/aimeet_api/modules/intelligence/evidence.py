"""Source locations are computed from immutable text, never from model timestamps."""

import re

from aimeet_api.modules.intelligence.schemas import Evidence
from aimeet_api.modules.rag.providers import RagError


def locate(transcript, quote, start=None, *, offset=0):
    if start is not None:
        if transcript[start : start + len(quote)] != quote:
            raise RagError("QUOTE_NOT_FOUND", 422)
    else:
        start = transcript.find(quote)
        if start < 0:
            raise RagError("QUOTE_NOT_FOUND", 422)
        if transcript.find(quote, start + 1) >= 0:
            raise RagError("AMBIGUOUS_QUOTE", 422)
    return Evidence(quote=quote, start_char=offset + start, end_char=offset + start + len(quote))


class EvidenceIndex:
    def __init__(self, transcript, segments=None):
        self.text = transcript
        self.segments = []
        # The STT engine joins these exact strings with newlines. If alignment is
        # lost, keep the citation textual instead of inventing a timestamp.
        lines = [s["text"] for s in segments] if segments else []
        if segments and "\n".join(lines) != transcript:
            # Archived live meetings retain explicit timestamps before each speaker.
            # Accept only an exact reconstruction, just as for plain STT segments.
            lines = [
                f"[{int(s['start']) // 60:02}:{int(s['start']) % 60:02}] {s['text']}"
                for s in segments
            ]
        if segments and "\n".join(lines) == transcript:
            position = 0
            for segment, line in zip(segments, lines, strict=True):
                end = position + len(line)
                self.segments.append((position, end, segment))
                position = end + 1

    def enrich(self, evidence):
        if evidence is None:
            return None
        ref = Evidence.model_validate(evidence)
        if self.text[ref.start_char : ref.end_char] != ref.quote:
            return None
        ref.start_seconds = ref.end_seconds = None
        ref.speaker = None
        overlaps = [
            s for start, end, s in self.segments if start < ref.end_char and end > ref.start_char
        ]
        if overlaps:
            ref.start_seconds = overlaps[0]["start"]
            ref.end_seconds = overlaps[-1]["end"]
            # Only explicit speaker labels in the source are displayed.
            labels = [re.match(r"^([^:\n]{1,80}):\s", s["text"]) for s in overlaps]
        else:
            line_start = self.text.rfind("\n", 0, ref.start_char) + 1
            labels = [
                re.match(r"^([^:\n]{1,80}):\s", line)
                for line in self.text[line_start : ref.end_char].splitlines()
            ]
        if labels and all(label is not None for label in labels):
            names = {label.group(1) for label in labels}
            if len(names) == 1:
                ref.speaker = names.pop()
        return ref


def clarification_codes(card):
    codes = []
    if card.kind not in {"task", "decision"}:
        return codes
    if card.agreement != "confirmed":
        codes.append("agreement_unconfirmed")
    if card.kind == "task":
        if not card.assignee or not card.assignee.strip():
            codes.append("assignee_missing")
        if not card.due_date:
            codes.append("date_unresolved" if card.due_text else "deadline_missing")
        if card.priority == "unspecified":
            codes.append("priority_missing")
    return codes


def enrich_card(data, index):
    from aimeet_api.modules.intelligence.schemas import Card

    card = Card.model_validate(data)
    card.evidence = None
    if card.quote:
        try:
            ref = locate(index.text, card.quote, card.start_char)
            card.evidence = index.enrich(ref)
        except RagError:
            pass
    if card.evidence:
        card.quote_start = card.evidence.start_char
    for revision in card.revisions:
        # A source change must not silently point to different audio.
        before, after = index.enrich(revision.before), index.enrich(revision.after)
        if before is None or after is None:
            raise RagError("SOURCE_CHANGED", 409)
        revision.before, revision.after = before, after
    card.clarifications = clarification_codes(card)
    return card
