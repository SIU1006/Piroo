import argparse
from pathlib import Path

import mlflow
from benchmark import benchmark_model
from mlflow.tracking import MlflowClient
from model_wrapper import WhisperPyfuncWrapper

'''
Benchmarking for real here

Usage:
    python model_eval/register_model.py --sizes tiny base small medium
'''



DEFAULT_SIZES = ["tiny", "base", "small", "medium"]
DEVICE = "cpu"
COMPUTE_TYPE = "int8"
DEFAULT_MODEL_NAME = "video-transcriber-whisper"
MODEL_WRAPPER_PATH = str(Path(__file__).parent / "model_wrapper.py")


def log_and_register(result: dict, registered_model_name: str):
    with mlflow.start_run(run_name=f"whisper-{result['model_size']}") as run:
        mlflow.log_param("model_size", result["model_size"])
        mlflow.log_param("device", result["device"])
        mlflow.log_param("compute_type", result["compute_type"])
        mlflow.log_param("n_eval_clips", result["n_clips"])

        mlflow.log_metric("load_seconds", result["load_seconds"])
        mlflow.log_metric("total_audio_seconds", result["total_audio_seconds"])
        mlflow.log_metric("total_transcribe_seconds", result["total_transcribe_seconds"])
        mlflow.log_metric("rtf", result["rtf"])
        mlflow.log_metric("wer", result["wer"])

        # Full per-clip breakdown as an artifact, for audit/debugging
        mlflow.log_dict({"per_clip": result["per_clip"]}, "per_clip_results.json")

        mlflow.pyfunc.log_model(
            name="model",
            python_model=WhisperPyfuncWrapper(),
            code_paths=[MODEL_WRAPPER_PATH],
            model_config={
                "model_size": result["model_size"],
                "device": result["device"],
                "compute_type": result["compute_type"],
            },
        )

        model_uri = f"runs:/{run.info.run_id}/model"
        model_version = mlflow.register_model(model_uri, registered_model_name)

    return model_version


def promote_to_staging(client: MlflowClient, name: str, version: str) -> None:
    client.set_registered_model_alias(name=name, alias="staging", version=version)
    print(f"Promoted {name} v{version} -> @staging")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", default=DEFAULT_SIZES)
    parser.add_argument("--device", default=DEVICE)
    parser.add_argument("--compute-type", default=COMPUTE_TYPE)
    parser.add_argument("--tracking-uri", default="http://localhost:3002")
    parser.add_argument("--registered-model-name", default=DEFAULT_MODEL_NAME)
    parser.add_argument(
        "--wer-threshold", type=float, default=0.20,
        help="Max acceptable WER for a candidate to be eligible for @staging (default: 0.20)",
    )
    parser.add_argument(
        "--rtf-budget", type=float, default=1.0,
        help="Max acceptable RTF for a candidate to be eligible for @staging (default: 1.0 = real-time)",
    )
    args = parser.parse_args()

    mlflow.set_tracking_uri(args.tracking_uri)
    client = MlflowClient()

    candidates = []
    for size in args.sizes:
        print(f"Benchmarking whisper '{size}' against the fixed eval set...")
        result = benchmark_model(size, device=args.device, compute_type=args.compute_type)
        print(f"  WER: {result['wer']:.3f} | RTF: {result['rtf']:.3f} | audio: {result['total_audio_seconds']:.1f}s")

        version = log_and_register(result, args.registered_model_name)
        candidates.append({"result": result, "version": version.version})

    print("\n--- Tradeoff summary ---")
    for c in candidates:
        r = c["result"]
        print(f"  {r['model_size']:<8} WER={r['wer']:.3f}  RTF={r['rtf']:.3f}  v{c['version']}")

    eligible = [c for c in candidates if c["result"]["rtf"] <= args.rtf_budget]
    if not eligible:
        print(f"\nNo candidate met the RTF budget ({args.rtf_budget}); not promoting anything.")
        return

    best = min(eligible, key=lambda c: c["result"]["wer"])
    if best["result"]["wer"] > args.wer_threshold:
        print(
            f"\nBest eligible candidate ({best['result']['model_size']}, "
            f"WER={best['result']['wer']:.3f}) exceeds --wer-threshold ({args.wer_threshold}); "
            "not promoting anything."
        )
        return

    print(
        f"\nBest candidate: {best['result']['model_size']} "
        f"(WER={best['result']['wer']:.3f}, RTF={best['result']['rtf']:.3f})"
    )
    promote_to_staging(client, args.registered_model_name, best["version"])
    print(
        f"\nRun `python model_eval/promote_model.py --registered-model-name "
        f"{args.registered_model_name}` after validation to move @staging -> @production."
    )


if __name__ == "__main__":
    main()