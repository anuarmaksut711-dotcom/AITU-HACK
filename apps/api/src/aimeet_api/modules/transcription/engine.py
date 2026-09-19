"""Offline Faster-Whisper adapter. Imported by a disposable inference process only."""

import hashlib
import json
import os
from pathlib import Path

from aimeet_api.modules.transcription.diarization import SpeakerAligner, diarize

# Set before importing libraries; missing assets must fail, never trigger a download.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"


class TranscriptionError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def model_metadata(path: Path) -> dict:
    required = ("model.bin", "config.json", "tokenizer.json")
    if not path.is_dir() or any(not (path / name).is_file() for name in required):
        raise TranscriptionError("model_missing")
    manifest = path / "aimeet-model.json"
    return json.loads(manifest.read_text()) if manifest.is_file() else {"source": path.name}


def decode_audio(path: Path, extension: str, max_seconds: int):
    import av
    import numpy as np

    samples = 0
    chunks = []
    demuxer = {".mp3": "mp3", ".wav": "wav", ".m4a": "mov"}[extension.lower()]
    try:
        # Force an audio container, never let uploaded playlist text select a network demuxer.
        with (
            path.open("rb") as stream,
            av.open(
                stream, format=demuxer, options={"enable_drefs": "0", "protocol_whitelist": "file"}
            ) as container,
        ):
            if not container.streams.audio:
                raise TranscriptionError("invalid_audio")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            for frame in container.decode(audio=0):
                for converted in resampler.resample(frame):
                    samples += converted.samples
                    if samples > max_seconds * 16000:
                        raise TranscriptionError("audio_too_long")
                    chunks.append(converted.to_ndarray().reshape(-1))
            for converted in resampler.resample(None):
                samples += converted.samples
                if samples > max_seconds * 16000:
                    raise TranscriptionError("audio_too_long")
                chunks.append(converted.to_ndarray().reshape(-1))
    except TranscriptionError:
        raise
    except Exception as exc:
        raise TranscriptionError("invalid_audio") from exc
    if not samples:
        raise TranscriptionError("invalid_audio")
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def transcribe(path: Path, config: dict, emit) -> dict:
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != config["input_sha256"]:
            raise TranscriptionError("source_changed")
    model_path = Path(config["model_path"])
    if model_metadata(model_path) != config["model"]:
        raise TranscriptionError("model_changed")
    audio = decode_audio(path, config["extension"], config["max_audio_seconds"])
    emit({"progress": 2})
    duration = len(audio) / 16000
    emit({"duration_seconds": round(duration, 3)})
    # Disable telemetry before loading either inference adapter.
    import onnxruntime

    onnxruntime.disable_telemetry_events()
    use_speakers = config.get("diarization_enabled", False)
    aligner = SpeakerAligner(diarize(audio, config, emit)) if use_speakers else None
    progress_start = 25 if use_speakers else 2
    emit({"progress": progress_start})
    from faster_whisper import WhisperModel

    model = WhisperModel(
        str(model_path),
        device=config["device"],
        compute_type=config["compute_type"],
        cpu_threads=config["cpu_threads"],
        local_files_only=True,
    )
    segments, info = model.transcribe(
        audio,
        language=None if config["language"] == "auto" else config["language"],
        task="transcribe",
        multilingual=True,
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=False,
        word_timestamps=use_speakers,
    )
    output = []
    emit({"detected_language": info.language})
    total_chars = 0
    last_progress = progress_start
    for segment in segments:
        start = max(0.0, min(float(segment.start), duration))
        end = max(start, min(float(segment.end), duration))
        rows = (
            aligner.split(segment, duration)
            if aligner
            else [{"start": round(start, 3), "end": round(end, 3), "text": segment.text.strip()}]
        )
        for row in rows:
            if not row["text"]:
                continue
            total_chars += len(row["text"]) + 1
            if total_chars > 200_000 or len(output) >= 20_000:
                raise TranscriptionError("transcript_too_long")
            output.append(row)
            emit({"segment": output[-1]})
        progress = min(
            99,
            max(
                progress_start, progress_start + int(segment.end / duration * (99 - progress_start))
            ),
        )
        if progress > last_progress:
            emit({"progress": progress})
            last_progress = progress
    if not output:
        raise TranscriptionError("no_speech")
    return {
        "transcript": "\n".join(item["text"] for item in output),
        "segments": output,
        "detected_language": info.language,
        "duration_seconds": round(duration, 3),
    }


def child_main(connection, path: str, config: dict):
    try:
        result = transcribe(Path(path), config, connection.send)
        connection.send({"result": result})
    except TranscriptionError as exc:
        connection.send({"error": exc.code})
    except Exception:
        # Native decoders/model exceptions may include paths or user content.
        connection.send({"error": "processing_failed"})
    finally:
        connection.close()
