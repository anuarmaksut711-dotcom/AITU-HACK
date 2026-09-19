"""Local voice clustering and word-level attribution; never infer identities from text."""

import os
from bisect import bisect_left, bisect_right
from pathlib import Path


def diarize(audio, config, emit):
    from aimeet_api.modules.transcription.engine import TranscriptionError

    root = Path(config["diarization_model_path"])
    segmentation = root / "segmentation.onnx"
    embedding = root / "embedding.onnx"
    if any(not path.is_file() for path in (segmentation, embedding)):
        raise TranscriptionError("diarization_model_missing")
    try:
        if os.name == "nt":
            import ctypes

            import onnxruntime

            # Sherpa dynamically loads ORT. Preload our exact DLL so Windows
            # cannot select its older System32/onnxruntime.dll (API 17).
            ctypes.WinDLL(str(Path(onnxruntime.__file__).parent / "capi" / "onnxruntime.dll"))
        import sherpa_onnx as sherpa

        threads = config["cpu_threads"]
        settings = sherpa.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=str(segmentation),
                ),
                num_threads=threads,
                provider="cpu",
            ),
            embedding=sherpa.SpeakerEmbeddingExtractorConfig(
                model=str(embedding),
                num_threads=threads,
                provider="cpu",
            ),
            clustering=sherpa.FastClusteringConfig(
                num_clusters=config.get("num_speakers") or -1,
                threshold=config["diarization_threshold"],
            ),
            min_duration_on=0.3,
            min_duration_off=0.5,
        )
        if not settings.validate():
            raise TranscriptionError("diarization_failed")
        model = sherpa.OfflineSpeakerDiarization(settings)
        if model.sample_rate != 16000:
            raise TranscriptionError("diarization_failed")

        def progress(done, total):
            emit({"progress": min(24, 2 + int(22 * done / max(1, total)))})
            return 0

        turns = model.process(audio, callback=progress).sort_by_start_time()
        return [(float(turn.start), float(turn.end), int(turn.speaker)) for turn in turns]
    except TranscriptionError:
        raise
    except Exception as exc:
        raise TranscriptionError("diarization_failed") from exc


class SpeakerAligner:
    def __init__(self, turns):
        self.turns = sorted(turns)
        self.names = {}
        self.starts, self.max_ends = [], []
        for start, end, speaker in self.turns:
            self.names.setdefault(speaker, f"Участник {len(self.names) + 1}")
            self.starts.append(start)
            self.max_ends.append(max(end, self.max_ends[-1] if self.max_ends else end))

    def speaker(self, start, end):
        # Restrict lookup to intersecting voice intervals even for long recordings.
        first = bisect_right(self.max_ends, start)
        last = bisect_left(self.starts, end)
        scores = {}
        for left, right, voice in self.turns[first:last]:
            overlap = max(0, min(end, right) - max(start, left))
            scores[voice] = scores.get(voice, 0) + overlap
        ranked = sorted(scores, key=scores.get, reverse=True)
        if not ranked or scores[ranked[0]] <= 0:
            return None
        # Simultaneous voices cannot be reliably assigned from a mixed ASR word.
        if len(ranked) > 1 and scores[ranked[1]] >= scores[ranked[0]] * 0.8:
            return None
        return self.names[ranked[0]]

    def split(self, segment, duration):
        words = getattr(segment, "words", None)
        if not words:
            # Without word timing, preserve the text but do not guess its speaker.
            return [
                {
                    "start": max(0, min(segment.start, duration)),
                    "end": max(0, min(segment.end, duration)),
                    "text": segment.text.strip(),
                    "speaker": None,
                }
            ]
        groups = []
        for word in words:
            start = max(0.0, min(float(word.start), duration))
            end = max(start, min(float(word.end), duration))
            speaker = self.speaker(start, end)
            if groups and groups[-1]["speaker"] == speaker:
                groups[-1]["text"] += word.word
                groups[-1]["end"] = max(groups[-1]["end"], round(end, 3))
            else:
                groups.append(
                    {
                        "start": round(start, 3),
                        "end": round(end, 3),
                        "text": word.word,
                        "speaker": speaker,
                    }
                )
        for row in groups:
            row["text"] = row["text"].strip()
            if row["speaker"] and row["text"]:
                # Keep labels in immutable source text for citations, RAG and export,
                # matching the existing archived Live transcript convention.
                row["text"] = f"{row['speaker']}: {row['text']}"
        return [row for row in groups if row["text"]]
