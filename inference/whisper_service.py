import bentoml
from faster_whisper import WhisperModel

@bentoml.service
class WhisperService:
    def __init__(self):
        self.model = WhisperModel("base", device="cpu", compute_type="int8")

    @bentoml.api
    def transcribe(self, audio_file: str) -> str:
        segments, info = self.model.transcribe(audio_file)
        print("Detected language '%s' with probability %f" % (info.language, info.language_probability))
        return " ".join([segment.text for segment in segments])