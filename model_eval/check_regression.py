"""CI compares exactly the baseline's dataset and selected configuration."""
import argparse
import json
from pathlib import Path

from model_eval.benchmark import benchmark_model
from model_eval.provenance import DEFAULT_CONFIG, dataset_identity, load_config, require_comparable
from model_eval.registry import BASELINE_PATH, gate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-size")
    parser.add_argument("--model-revision")
    parser.add_argument("--endpoint")
    parser.add_argument("--deployed-image", default="not-deployed")
    parser.add_argument("--baseline-path", type=Path, default=BASELINE_PATH)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--max-regression", type=float, default=0.02)
    parser.add_argument("--hard-ceiling", type=float, default=0.30)
    parser.add_argument("--rtf-budget", type=float, default=2.0)
    parser.add_argument("--output", type=Path, default=Path("ci-evaluation.json"))
    args = parser.parse_args()
    baseline = json.loads(args.baseline_path.read_text())
    identity, _ = dataset_identity(Path(__file__).parent / "eval_data", load_config(args.config))
    require_comparable(baseline, {"evaluation": identity})
    size = args.model_size or baseline["model_size"]
    revision = args.model_revision or (baseline["model_revision"]
                                       if size == baseline["model_size"] else None)
    candidate = benchmark_model(size, config_path=args.config, revision=revision,
                                endpoint=args.endpoint, deployed_image=args.deployed_image)
    args.output.write_text(json.dumps(candidate, indent=2) + "\n")
    gate(candidate, baseline, args.hard_ceiling, args.max_regression, args.rtf_budget)
    print(f"PASS: same examples; WER={candidate['wer']:.4f}, RTF={candidate['rtf']:.3f}")


if __name__ == "__main__":
    main()
