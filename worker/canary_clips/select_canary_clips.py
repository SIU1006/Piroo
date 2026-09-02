import argparse
import json
import shutil
from pathlib import Path

'''
Pick X clips out from model_eval/eval_data/ into worker/canary_clips, so periodic canary task has labeled audio to run.

The clips will be inside the celery image >> keep it small

Usage:
    python worker/canary_clips/select_canary_clips.py --n-clips X
'''

EVAL_DATA_DIR = Path(__file__).parent.parent.parent / "model_eval" / "eval_data"
EVAL_MANIFEST = EVAL_DATA_DIR / "manifest.json"
CANARY_DIR = Path(__file__).parent
CANARY_MANIFEST = CANARY_DIR / "manifest.json"

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-clips", type=int, default=2, help="How many clips to copy (default: 2)")
    args = parser.parse_args()

    if not EVAL_MANIFEST.exists():
        raise FileNotFoundError(
            f"{EVAL_MANIFEST} not found. Run `python model_eval/prepare_eval_set.py` first."
        )

    eval_manifest = json.loads(EVAL_MANIFEST.read_text())
    if len(eval_manifest) < args.n_clips:
        raise ValueError(f"Only {len(eval_manifest)} clips available in {EVAL_MANIFEST}, need {args.n_clips}")

    canary_manifest = []
    for clip in eval_manifest[: args.n_clips]:
        src = EVAL_DATA_DIR / clip["filename"]
        dst = CANARY_DIR / clip["filename"]
        shutil.copyfile(src, dst)
        canary_manifest.append({"filename": clip["filename"], "reference_text": clip["reference_text"]})
        print(f"  copied {clip['filename']}")

    CANARY_MANIFEST.write_text(json.dumps(canary_manifest, indent=2))
    print(f"\nWrote {len(canary_manifest)} clips + manifest to {CANARY_DIR}")
    print("Commit worker/canary_clips/ (wavs + manifest.json) - it needs to ship inside the celery image.")


if __name__ == "__main__":
    main()