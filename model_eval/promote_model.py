import argparse
import json
import sys
from pathlib import Path

import mlflow
from mlflow.exceptions import MlflowException
from mlflow.tracking import MlflowClient

"""
Run after you are happy with the model in @staging.
- Re-check its WER, then promote it to @production
- Writes a new baseline.json for CI's regression gate to having something new to compare against next time.


Meant to be called from CI

Usage:
    python model_eval/promote_model.py --wer-threshold 0.20
"""

DEFAULT_MODEL_NAME = "video-transcriber-whisper"
BASELINE_PATH = Path(__file__).parent / "baseline.json"

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracking-uri", default="http://localhost:3002")
    parser.add_argument("--registered-model-name", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--wer-threshold", type=float, default=0.20)
    args = parser.parse_args()

    mlflow.set_tracking_uri(args.tracking_uri)
    client = MlflowClient()

    try:
        staged = client.get_model_version_by_alias(args.registered_model_name, "staging")
    except MlflowException:
        print(f"No @staging version found for '{args.registered_model_name}'.")
        sys.exit(1)

    run = client.get_run(staged.run_id)
    wer = run.data.metrics.get("wer")
    rtf = run.data.metrics.get("rtf")
    if wer is None:
        print(f"Run {staged.run_id} has no 'wer' metric logged; refusing to promote.")
        sys.exit(1)

    model_size = run.data.params.get("model_size", "unknown")
    print(f"@staging is v{staged.version} (model_size={model_size}, WER={wer:.3f})")

    if wer > args.wer_threshold:
        print(f"WER {wer:.3f} exceeds threshold {args.wer_threshold}; not promoting.")
        sys.exit(1)

    client.set_registered_model_alias(
        name=args.registered_model_name, alias="production", version=staged.version
    )
    print(f"Promoted {args.registered_model_name} v{staged.version} -> @production")

    baseline = {
        "model_size": model_size,
        "wer": wer,
        "rtf": rtf,
        "registered_model_name": args.registered_model_name,
        "version": staged.version,
    }
    BASELINE_PATH.write_text(json.dumps(baseline, indent=2))
    print(f"\nWrote {BASELINE_PATH} - commit this so CI's regression gate uses the new baseline:")
    print("  git add model_eval/baseline.json")


if __name__ == "__main__":
    main()