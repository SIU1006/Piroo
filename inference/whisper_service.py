import logging
import os

import bentoml
from faster_whisper import WhisperModel

model_size = os.getenv("WHISPER_MODEL_SIZE", "base")  # canary
logger = logging.getLogger(__name__)


@bentoml.service(traffic={"timeout": 600})
class WhisperService:
    def __init__(self):
        self.model = WhisperModel(model_size, device="cpu", compute_type="int8")

    @bentoml.api
    def transcribe(self, audio_file: str) -> str:
        segments, info = self.model.transcribe(audio_file)
        logger.info(
            f"Detected language '{info.language}' with probability {info.language_probability:f}"
        )        
        return " ".join([segment.text for segment in segments])
