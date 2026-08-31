import argparse
import time

from faster_whisper import WhisperModel

import mlflow

DEFAULT_SIZES = ["base", "small", "medium"]
DEVICE = "cpu"
COMPUTE_TYPE = "int8"

def benchmark_model(model_size: str, audio_path: str) -> dict:
    """Load one model size and time a real transcription of audio_path."""
    load_start = time.perf_counter()
    model = WhisperModel(model_size, device=DEVICE, compute_type=COMPUTE_TYPE)
    load_seconds = time.perf_counter() - load_start

    transcribe_start = time.perf_counter()

    segments, info = model.transcribe(audio_path)
    transcript = " ".join(segment.text for segment in segments)
    transcribe_seconds = time.perf_counter() - transcribe_start

    audio_duration = info.duration
    rtf = transcribe_seconds / audio_duration if audio_duration > 0 else float("nan")

    return {
    "model_size": model_size,
    "device": DEVICE,
    "compute_type": COMPUTE_TYPE,
    "load_seconds": load_seconds,
    "transcribe_seconds": transcribe_seconds,
    "audio_duration_seconds": audio_duration,
    "rtf": rtf,
    "detected_language": info.language,
    "language_probability": info.language_probability,
    "transcript_char_count": len(transcript),
}

def log_run(result: dict) -> None:
    with mlflow.start_run(run_name=f"whisper-{result['model_size']}"):
        mlflow.log_param("model_size", result["model_size"])
        mlflow.log_param("device", result["device"])
        mlflow.log_param("compute_type", result["compute_type"])
        mlflow.log_param("detected_language", result["detected_language"])

        mlflow.log_metric("load_seconds", result["load_seconds"])
        mlflow.log_metric("transcribe_seconds", result["transcribe_seconds"])
        mlflow.log_metric("audio_duration_seconds", result["audio_duration_seconds"])
        mlflow.log_metric("rtf", result["rtf"])
        mlflow.log_metric("language_probability", result["language_probability"])
        mlflow.log_metric("transcript_char_count", result["transcript_char_count"])

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "audio_path",
        help="Path to a sample audio file"
    )
    parser.add_argument(
        "--sizes",
        nargs="+",
        default=DEFAULT_SIZES,
        help=f"Model sizes to benchmark (default: {DEFAULT_SIZES})",
    )
    parser.add_argument(
        "--tracking-uri",
        default="http://localhost:3002",
        help="MLflow tracking server URI (default: http://localhost:3002)",
    )
    args = parser.parse_args()

    mlflow.set_tracking_uri(args.tracking_uri)

    for size in args.sizes:
        print(f"Benchmarking whisper '{size}'...")
        result = benchmark_model(size, args.audio_path)
        print(
            f"  load: {result['load_seconds']:.2f}s | "
            f"transcribe: {result['transcribe_seconds']:.2f}s | "
            f"audio: {result['audio_duration_seconds']:.2f}s | "
            f"RTF: {result['rtf']:.3f}"
        )
        log_run(result)

    print(f"\nDone. View results at {args.tracking_uri}")

if __name__ == "__main__":
    main()