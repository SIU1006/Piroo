import logging
import os
import time
from pathlib import Path
from typing import Annotated

import bentoml
from bentoml.io import File
from bentoml.metrics import Histogram
from bentoml.validators import FileSchema
from faster_whisper import WhisperModel

model_size = os.getenv("WHISPER_MODEL_SIZE", "base")
logger = logging.getLogger(__name__)

# how long transcription took
TRANSCRIBE_SECONDS = Histogram(
    name="whisper_transcribe_seconds",
    documentation="Wall-clock time spent transcribing one request.",
    labelnames=["model_size"],
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300, 600, float("inf")),
)

# transcribe_seconds / audio_seconds
RTF = Histogram(
    name="whisper_rtf",
    documentation=(
        "Real-time factor (transcribe_seconds / audio_seconds) per request. "
        "RTF < 1 means faster than real-time."
    ),
    labelnames=["model_size"],
    buckets=(0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.75, 1.0, 1.5, 2.0, 5.0, float("inf")),
)



@bentoml.service(traffic={"timeout": 600})
class WhisperService:
    def __init__(self):
        self.model_size = model_size
        self.model = WhisperModel(model_size, device="cpu", compute_type="int8")

    @bentoml.api
    def transcribe(
        self, audio_file: Annotated[Path, FileSchema(content_type="audio/mpeg")]
    ) -> str:
        start = time.perf_counter()
        segments, info = self.model.transcribe(str(audio_file))
        text = " ".join(segment.text for segment in segments)  # forces the lazy generator to run
        transcribe_seconds = time.perf_counter() - start

        logger.info(
            f"Detected language '{info.language}' with probability {info.language_probability:f}"
        )

        TRANSCRIBE_SECONDS.labels(model_size=self.model_size).observe(transcribe_seconds)
        if info.duration > 0:
            RTF.labels(model_size=self.model_size).observe(transcribe_seconds / info.duration)

        return text
