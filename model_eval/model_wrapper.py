import mlflow

'''wrap a whisper model behind mlflow.pyfunc so a model can be logged/versioned in the registry'''

class WhisperPyfuncWrapper(mlflow.pyfunc.PythonModel):
    def load_context(self, context):
        from faster_whisper import WhisperModel
        from huggingface_hub import snapshot_download

        cfg = context.model_config
        self.model = WhisperModel(
            snapshot_download(f"Systran/faster-whisper-{cfg['model_size']}",
                              revision=cfg["model_revision"]),
            device=cfg.get("device", "cpu"),
            compute_type=cfg.get("compute_type", "int8"),
        )
        self.transcribe_config = cfg["transcribe"]

    def predict(self, context, model_input, params=None):
        audio_paths = (
            model_input["audio_path"].tolist() if hasattr(model_input, "columns") else list(model_input)
        )
        transcripts = []
        for audio_path in audio_paths:
            segments, _ = self.model.transcribe(audio_path, **self.transcribe_config)
            transcripts.append(" ".join(segment.text for segment in segments))
        return transcripts


# MLflow models-from-code bundles this file without importing the repository
# package on a different evaluator or serving machine.
mlflow.models.set_model(WhisperPyfuncWrapper())
