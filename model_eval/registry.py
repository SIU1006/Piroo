"""Registry decisions require auditable same-example, fresh image evaluations."""
import argparse
import json
import math
from pathlib import Path

import mlflow
from mlflow.tracking import MlflowClient

from model_eval.benchmark import benchmark_model
from model_eval.provenance import DEFAULT_CONFIG, ROOT, require_comparable

DEFAULT_NAME = "video-transcriber-whisper"
BASELINE_PATH = ROOT / "baseline.json"


def log_and_register(result, name):
    with mlflow.start_run(run_name=f"whisper-{result['model_size']}") as run:
        mlflow.log_params({key: result[key] for key in (
            "model_size", "model_revision", "device", "compute_type", "deployed_image", "n_clips",
        )})
        mlflow.log_params({key: result["evaluation"][key] for key in (
            "dataset_sha256", "subset_sha256", "config_sha256",
        )})
        mlflow.log_metrics({key: result[key] for key in ("wer", "rtf", "load_seconds")})
        mlflow.log_dict(result, "evaluation.json")
        info = mlflow.pyfunc.log_model(
            name="model", python_model=str(ROOT / "model_wrapper.py"),
            model_config={
                "model_size": result["model_size"], "model_revision": result["model_revision"],
                "device": result["device"], "compute_type": result["compute_type"],
                "transcribe": result["evaluation"]["config"]["transcribe"],
            },
            pip_requirements=["mlflow==3.15.0", "faster-whisper==1.2.1"],
        )
        version = mlflow.register_model(info.model_uri, name)
        client = MlflowClient()
        client.set_model_version_tag(name, version.version, "deployed_image", result["deployed_image"])
        result = {**result, "registered_model_name": name, "version": version.version,
                  "run_id": run.info.run_id}
    return result


def gate(result, baseline, max_wer=0.20, max_regression=0.02, rtf_budget=1.0):
    require_comparable(baseline, result)
    if not all(math.isfinite(result[key]) for key in ("wer", "rtf")):
        raise ValueError("Non-finite evaluation metrics")
    if result["wer"] > max_wer or result["rtf"] > rtf_budget:
        raise ValueError("Candidate exceeds WER or RTF budget")
    if result["wer"] - baseline["wer"] > max_regression:
        raise ValueError("Candidate regressed on the identical evaluated subset")


def promote(name, endpoint, image, baseline_path=BASELINE_PATH, release_path=None,
            max_wer=0.20, max_regression=0.02, rtf_budget=1.0):
    client = MlflowClient()
    staged = client.get_model_version_by_alias(name, "staging")
    logged = mlflow.artifacts.load_dict(f"runs:/{staged.run_id}/evaluation.json")
    if logged["deployed_image"] != image:
        raise ValueError("Promotion image differs from the registered candidate image")
    baseline_path = Path(baseline_path)
    baseline = json.loads(baseline_path.read_text())
    config_path = baseline_path.parent / "promotion-config.json"
    config_path.write_text(json.dumps(logged["evaluation"]["config"]))
    # Never decide from registration's old WER. Call the live pinned candidate again.
    fresh = benchmark_model(logged["model_size"], config_path=config_path, endpoint=endpoint,
                            deployed_image=image, revision=logged["model_revision"])
    require_comparable(logged, fresh)
    gate(fresh, baseline, max_wer, max_regression, rtf_budget)
    previous = client.get_model_version_by_alias(name, "production")
    if str(previous.version) != str(baseline["version"]):
        raise ValueError("Production registry alias and baseline version disagree")
    with mlflow.start_run(run_name="fresh-promotion-verification") as run:
        mlflow.log_dict(fresh, "fresh_evaluation.json")
        mlflow.log_metrics({"wer": fresh["wer"], "rtf": fresh["rtf"]})
        release = {"name": name, "candidate_version": staged.version,
                   "previous_version": previous.version, "previous_baseline": baseline,
                   "fresh_evaluation": fresh, "verification_run_id": run.info.run_id,
                   "deployed_image": image}
        mlflow.log_dict(release, "release.json")
    # Registry promotion authorizes an image; deployment remains an explicit operation.
    client.set_registered_model_alias(name, "production", staged.version)
    fresh.update(registered_model_name=name, version=staged.version, run_id=staged.run_id)
    baseline_path.write_text(json.dumps(fresh, indent=2) + "\n")
    if release_path:
        Path(release_path).write_text(json.dumps(release, indent=2) + "\n")
    return release


def rollback(release, baseline_path):
    client = MlflowClient()
    current = client.get_model_version_by_alias(release["name"], "production")
    if str(current.version) != str(release["candidate_version"]):
        raise ValueError("Production moved since this release; refusing stale rollback")
    client.set_registered_model_alias(release["name"], "production", release["previous_version"])
    Path(baseline_path).write_text(json.dumps(release["previous_baseline"], indent=2) + "\n")
    return {"production_version": release["previous_version"],
            "deployed_image": release["previous_baseline"]["deployed_image"]}


def register_main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", nargs="+", default=["tiny", "base", "small", "medium"])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--tracking-uri", default="http://localhost:3002")
    parser.add_argument("--registered-model-name", default=DEFAULT_NAME)
    parser.add_argument("--endpoints", nargs="*", default=[])
    parser.add_argument("--images", nargs="*", default=[])
    parser.add_argument("--rtf-budget", type=float, default=1.0)
    parser.add_argument("--wer-threshold", type=float, default=0.20)
    args = parser.parse_args()
    mlflow.set_tracking_uri(args.tracking_uri)
    endpoints, images = (dict(item.split("=", 1) for item in values)
                         for values in (args.endpoints, args.images))
    results = [log_and_register(benchmark_model(
        size, config_path=args.config, endpoint=endpoints.get(size),
        deployed_image=images.get(size, "not-deployed")), args.registered_model_name)
        for size in args.sizes]
    eligible = [r for r in results if math.isfinite(r["wer"]) and math.isfinite(r["rtf"])
                and r["wer"] <= args.wer_threshold and r["rtf"] <= args.rtf_budget]
    if not eligible:
        raise ValueError("No eligible candidate")
    best = min(eligible, key=lambda r: r["wer"])
    MlflowClient().set_registered_model_alias(args.registered_model_name, "staging", best["version"])
    print(json.dumps({"staging_version": best["version"], "results": results}, indent=2))


def promote_main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tracking-uri", default="http://localhost:3002")
    parser.add_argument("--registered-model-name", default=DEFAULT_NAME)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--deployed-image", required=True)
    parser.add_argument("--baseline-path", type=Path, default=BASELINE_PATH)
    parser.add_argument("--release-path", type=Path, required=True)
    parser.add_argument("--wer-threshold", type=float, default=0.20)
    parser.add_argument("--max-regression", type=float, default=0.02)
    parser.add_argument("--rtf-budget", type=float, default=1.0)
    args = parser.parse_args()
    mlflow.set_tracking_uri(args.tracking_uri)
    print(json.dumps(promote(args.registered_model_name, args.endpoint, args.deployed_image,
                             args.baseline_path, args.release_path, args.wer_threshold,
                             args.max_regression, args.rtf_budget), indent=2))
