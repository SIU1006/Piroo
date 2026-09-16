"""Generate a bounded three-case review set using the production summary contract."""
import argparse
import hashlib
import json
import time
from pathlib import Path

import ollama
import requests

from summary_contract import SUMMARY_OPTIONS, SUMMARY_PROMPT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", default="llama3.2")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--deployed-image", required=True)
    args = parser.parse_args()
    cases_path = Path(__file__).parent / "summary_cases.json"
    tags = requests.get(f"{args.endpoint}/api/tags", timeout=30).json()
    model = next(item for item in tags["models"]
                 if item["name"] in {args.model, f"{args.model}:latest"})
    client = ollama.Client(host=args.endpoint, timeout=300)
    results = []
    for case in json.loads(cases_path.read_text()):
        started = time.perf_counter()
        response = client.chat(model=args.model, options=SUMMARY_OPTIONS,
                               messages=[{"role": "user", "content":
                                          SUMMARY_PROMPT.format(transcript=case["transcript"])}])
        results.append({**case, "summary": response.message.content,
                        "seconds": time.perf_counter() - started})
    output = {"dataset_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
              "model_digest": model["digest"], "model": args.model,
              "deployed_image": args.deployed_image, "prompt": SUMMARY_PROMPT,
              "options": SUMMARY_OPTIONS, "generated_at": time.time(), "cases": results}
    args.output.write_text(json.dumps(output, indent=2) + "\n")


if __name__ == "__main__":
    main()
