import hashlib
from types import SimpleNamespace

import pytest

from aimeet_api.modules.intelligence.evidence import EvidenceIndex, locate
from aimeet_api.modules.meetings.schemas import TranscriptSegment
from aimeet_api.modules.transcription import engine


def test_speaker_survives_api_serialization():
    row = {"start": 0, "end": 1, "text": "Участник 1: Привет!", "speaker": "Участник 1"}
    assert TranscriptSegment.model_validate(row).model_dump().get("speaker") == "Участник 1"


@pytest.mark.parametrize(
    "turns, expected",
    [
        ([(0, 1, 8), (1, 2, 3), (2, 3, 8)], ["Участник 1", "Участник 2", "Участник 1"]),
        ([(0, 3, 8)], ["Участник 1"]),
        ([], [None]),
    ],
)
def test_adapter_splits_words_by_voice_and_keeps_returning_speaker(
    tmp_path,
    monkeypatch,
    turns,
    expected,
):
    import sys

    import numpy as np

    path = tmp_path / "audio.wav"
    path.write_bytes(b"audio")
    model_path = tmp_path / "model"
    model_path.mkdir()
    for name in ("model.bin", "config.json", "tokenizer.json"):
        (model_path / name).write_text("{}")
    words = [
        SimpleNamespace(start=i + 0.1, end=i + 0.9, word=text)
        for i, text in enumerate([" Привет!", " Добрый день.", " Начнём."])
    ]

    class Model:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, audio, **kwargs):
            assert kwargs["word_timestamps"] is True
            assert kwargs["multilingual"] is True
            return iter(
                [SimpleNamespace(start=0, end=3, text=" Привет! Добрый день. Начнём.", words=words)]
            ), SimpleNamespace(language="ru")

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr(engine, "decode_audio", lambda *args: np.zeros(48000, dtype=np.float32))
    monkeypatch.setattr(engine, "diarize", lambda *args: turns, raising=False)
    config = {
        "model_path": str(model_path),
        "model": engine.model_metadata(model_path),
        "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "extension": ".wav",
        "max_audio_seconds": 4,
        "device": "cpu",
        "compute_type": "int8",
        "cpu_threads": 2,
        "language": "ru",
        "diarization_enabled": True,
    }
    emitted = []
    result = engine.transcribe(path, config, emitted.append)
    assert [row.get("speaker") for row in result["segments"]] == expected
    assert [message["segment"] for message in emitted if "segment" in message] == result["segments"]
    index = EvidenceIndex(result["transcript"], result["segments"])
    evidence = index.enrich(locate(result["transcript"], "Добрый день."))
    assert evidence.speaker == (expected[1] if len(expected) > 1 else expected[0])
    if len(expected) == 3:
        assert [(row["start"], row["end"]) for row in result["segments"]] == [
            (0.1, 0.9),
            (1.1, 1.9),
            (2.1, 2.9),
        ]


def test_old_segments_remain_readable():
    assert TranscriptSegment(start=0, end=1, text="Привет!").model_dump().get("speaker") is None


def test_overlap_and_silence_do_not_invent_speaker():
    from aimeet_api.modules.transcription.diarization import SpeakerAligner

    aligner = SpeakerAligner([(0, 2, 1), (1, 3, 2)])
    assert aligner.speaker(0.2, 0.9) == "Участник 1"
    assert aligner.speaker(1.2, 1.8) is None
    assert aligner.speaker(2.2, 2.8) == "Участник 2"
    assert aligner.speaker(4, 5) is None


def test_missing_diarization_models_fail_without_network(tmp_path, monkeypatch):
    import socket

    from aimeet_api.modules.transcription.diarization import diarize

    def forbidden(*args, **kwargs):
        raise AssertionError("Inference attempted network access")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    with pytest.raises(engine.TranscriptionError, match="diarization_model_missing"):
        diarize([], {"diarization_model_path": str(tmp_path)}, lambda _: None)


def test_mixed_language_words_are_preserved_under_one_speaker():
    from aimeet_api.modules.transcription.diarization import SpeakerAligner

    words = [
        SimpleNamespace(start=i, end=i + 0.9, word=text)
        for i, text in enumerate([" Бүгін", " обсуждаем", " release plan."])
    ]
    rows = SpeakerAligner([(0, 3, 4)]).split(SimpleNamespace(words=words), 3)
    assert rows == [
        {
            "start": 0,
            "end": 2.9,
            "speaker": "Участник 1",
            "text": "Участник 1: Бүгін обсуждаем release plan.",
        }
    ]
