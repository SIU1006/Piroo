import bentoml
from faster_whisper import WhisperModel
import os

model_size = os.getenv("WHISPER_MODEL_SIZE", "base") # canary

@bentoml.service(traffic={"timeout": 600})
class WhisperService:
    def __init__(self):
        self.model = WhisperModel(model_size, device="cpu", compute_type="int8")

    @bentoml.api
    def transcribe(self, audio_file: str) -> str:
        segments, info = self.model.transcribe(audio_file)
        print("Detected language '%s' with probability %f" % (info.language, info.language_probability))
        return " ".join([segment.text for segment in segments])