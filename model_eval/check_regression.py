import argparse
import json
import sys
from pathlib import Path

from benchmark import benchmark_model

'''CI Gate Benchmark current candidate against eval_data/
- fails if WER regressed past threshold vs baseline.json

Usage:
    python model_eval/check_regression.py
    python model_eval/check_regression.py --model-size base --max-regression 0.02
'''

BASELINE_PATH = Path(__file__).parent / "baseline.json"

def load_baseline() -> dict | None:
    if not BASELINE_PATH.exists():
        return None
    return json.loads(BASELINE_PATH.read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-size", default=None,
        help="Which size to benchmark (default: baseline.json's model_size, or 'base' if no baseline exists yet)",
    )
    parser.add_argument(
        "--n-clips", type=int, default=20,
        help="How many clips from the fixed eval set to check against (default: 20)",
    )
    parser.add_argument(
        "--max-regression", type=float, default=0.02,
        help="Max allowed WER increase over the baseline before failing (default: 0.02)",
    )
    parser.add_argument(
        "--hard-ceiling", type=float, default=0.30,
        help="Absolute WER ceiling regardless of baseline - catches a bad/stale baseline too (default: 0.30)",
    )
    args = parser.parse_args()

    baseline = load_baseline()
    model_size = args.model_size or (baseline["model_size"] if baseline else "base")

    print(f"Benchmarking '{model_size}' against {args.n_clips} clips from the fixed eval set...")
    result = benchmark_model(model_size, max_clips=args.n_clips)
    wer, rtf = result["wer"], result["rtf"]
    print(f"  WER: {wer:.3f} | RTF: {rtf:.3f} (n_clips={result['n_clips']})")

    if wer > args.hard_ceiling:
        print(f"FAIL: WER {wer:.3f} exceeds the hard ceiling ({args.hard_ceiling}).")
        sys.exit(1)

    if baseline is None:
        print(f"No {BASELINE_PATH.name} yet - skipping the regression check (only the hard ceiling applies).")
        print("Run model_eval/promote_model.py locally after a real promotion to create one, then commit it.")
        return

    baseline_wer = baseline["wer"]
    regression = wer - baseline_wer
    print(f"  baseline WER: {baseline_wer:.3f} (model_size={baseline['model_size']}) | delta: {regression:+.3f}")

    if regression > args.max_regression:
        print(f"FAIL: WER regressed by {regression:.3f}, which exceeds the allowed {args.max_regression:.3f}.")
        sys.exit(1)

    print("PASS: no meaningful WER regression detected.")


if __name__ == "__main__":
    main()