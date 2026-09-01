import argparse
import io
import json
from pathlib import Path

import soundfile as sf
from datasets import Audio, load_dataset

'''
One-time setup.
- Pulls X fixed, labeled clips from LibriSpeech dev-clean
- Saves them + a manifest.json (reference transcripts) to eval_data/.
- Run this once before anything else.

Usage:
    python model_eval/prepare_eval_set.py --n-clips X
'''

EVAL_DIR = Path(__file__).parent / "eval_data"
MANIFEST_PATH = EVAL_DIR / "manifest.json"


def prepare(n_clips: int, seed: int) -> None:
    EVAL_DIR.mkdir(parents=True, exist_ok=True)

    ds = load_dataset(
        "parquet",
        data_files={
            "validation": "hf://datasets/openslr/librispeech_asr@refs%2Fconvert%2Fparquet/clean/validation/*.parquet"
        },
        split="validation",
        streaming=True,
    )

    ds = ds.cast_column("audio", Audio(decode=False))
    ds = ds.shuffle(seed=seed, buffer_size=100)

    manifest = []
    for i, example in enumerate(ds.take(n_clips)):
        audio_bytes = example["audio"]["bytes"]
        array, sampling_rate = sf.read(io.BytesIO(audio_bytes))

        filename = f"clip_{i:02d}.wav"
        out_path = EVAL_DIR / filename
        sf.write(out_path, array, sampling_rate)

        duration_seconds = len(array) / sampling_rate
        reference_text = example["text"].strip()
        manifest.append(
            {
                "filename": filename,
                "reference_text": reference_text,
                "duration_seconds": duration_seconds,
                "source": "librispeech_asr/clean/validation",
                "source_id": example.get("id", filename),
            }
        )
        print(f"  saved {filename} ({duration_seconds:.1f}s): {reference_text[:60]}...")

    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
    print(f"\nWrote {len(manifest)} clips + manifest to {EVAL_DIR}")
    print("Commit model_eval/eval_data/ (wavs + manifest.json) so CI benchmarks against the same fixed set.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-clips", type=int, default=5, help="Number of clips to sample (default: 5)")
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Shuffle seed - keep this fixed so re-runs reproduce the same eval set (default: 42)",
    )
    args = parser.parse_args()
    prepare(args.n_clips, args.seed)


if __name__ == "__main__":
    main()