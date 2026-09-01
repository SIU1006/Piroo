import json
from pathlib import Path

"helper function to get the model size from baseline.json, or fallback to 'base' if it doesn't exist yet"

BASELINE_PATH = Path(__file__).parent / "baseline.json"
FALLBACK_SIZE = "base"


def main():
    if not BASELINE_PATH.exists():
        print(FALLBACK_SIZE)
        return
    baseline = json.loads(BASELINE_PATH.read_text())
    print(baseline.get("model_size", FALLBACK_SIZE))


if __name__ == "__main__":
    main()