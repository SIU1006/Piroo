"""Bounded real local registry example; each phase writes auditable evidence."""
import argparse
import json
from pathlib import Path

import mlflow
from mlflow.tracking import MlflowClient

from model_eval.benchmark import benchmark_model
from model_eval.provenance import require_comparable
from model_eval.registry import gate, log_and_register, promote, rollback


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["evaluate", "promote", "verify", "rollback", "verify-rollback"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-image", required=True)
    parser.add_argument("--candidate-image", required=True)
    parser.add_argument("--stable-endpoint", default="http://whisper-stable:3000")
    parser.add_argument("--candidate-endpoint", default="http://whisper-candidate:3000")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(f"sqlite:///{args.output.resolve()}/registry.db")
    name = "video-transcriber-whisper"
    baseline_path = args.output / "baseline.json"
    release_path = args.output / "release.json"
    client = MlflowClient()
    if args.phase == "evaluate":
        baseline = log_and_register(benchmark_model(
            "tiny", endpoint=args.stable_endpoint, deployed_image=args.baseline_image), name)
        candidate = log_and_register(benchmark_model(
            "base", endpoint=args.candidate_endpoint, deployed_image=args.candidate_image), name)
        gate(candidate, baseline, rtf_budget=2.0)
        client.set_registered_model_alias(name, "production", baseline["version"])
        client.set_registered_model_alias(name, "staging", candidate["version"])
        baseline_path.write_text(json.dumps(baseline, indent=2) + "\n")
        (args.output / "candidate.json").write_text(json.dumps(candidate, indent=2) + "\n")
    elif args.phase == "promote":
        promote(name, args.candidate_endpoint, args.candidate_image,
                baseline_path, release_path, rtf_budget=2.0)
    elif args.phase == "rollback":
        receipt = rollback(json.loads(release_path.read_text()), baseline_path)
        (args.output / "rollback.json").write_text(json.dumps(receipt, indent=2) + "\n")
    else:
        baseline = json.loads(baseline_path.read_text())
        image = args.baseline_image if args.phase == "verify-rollback" else args.candidate_image
        result = benchmark_model(baseline["model_size"], endpoint=args.stable_endpoint,
                                 deployed_image=image, revision=baseline["model_revision"])
        require_comparable(baseline, result)
        gate(result, baseline, rtf_budget=2.0)
        result["production_version"] = client.get_model_version_by_alias(name, "production").version
        if str(result["production_version"]) != str(baseline["version"]):
            raise ValueError("Registry alias does not match verified deployment baseline")
        (args.output / f"{args.phase}.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"Completed phase: {args.phase}")


if __name__ == "__main__":
    main()
