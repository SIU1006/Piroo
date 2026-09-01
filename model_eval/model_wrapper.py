import mlflow

'''wrap a whisper model behind mlflow.pyfunc so a model can be logged/versioned in the registry'''

class WhisperPyfuncWrapper(mlflow.pyfunc.PythonModel):
    def load_context(self, context):
        from faster_whisper import WhisperModel

        cfg = context.model_config
        self.model = WhisperModel(
            cfg["model_size"],
            device=cfg.get("device", "cpu"),
            compute_type=cfg.get("compute_type", "int8"),
        )

    def predict(self, context, model_input, params=None):
        audio_paths = (
            model_input["audio_path"].tolist() if hasattr(model_input, "columns") else list(model_input)
        )
        transcripts = []
        for audio_path in audio_paths:
            segments, _ = self.model.transcribe(audio_path)
            transcripts.append(" ".join(segment.text for segment in segments))
        return transcripts