"""Export a completed lifecycle once, sharing identities and transcript content."""
import argparse
import json
from pathlib import Path

from model_eval.provenance import digest, require_comparable


def export(directory):
    directory = Path(directory)

    def read(name):
        return json.loads((directory / f"{name}.json").read_text())

    release = read("release")
    baseline = release["previous_baseline"]
    identity = baseline["evaluation"]
    transcripts = {}

    def result(report):
        require_comparable(baseline, report)
        clips = report["per_clip"]
        content = [{key: clip[key] for key in
                    ("filename", "reference", "hypothesis", "audio_seconds")} for clip in clips]
        content_id = digest(content)
        transcripts.setdefault(content_id, content)
        return {**{key: value for key, value in report.items()
                   if key not in ("evaluation", "per_clip")},
                "transcripts_sha256": content_id,
                "clip_measurements": [[clip["transcribe_seconds"], clip["wer"]] for clip in clips]}

    reports = {"baseline": baseline, "candidate": read("candidate"),
               "fresh_promotion": release["fresh_evaluation"],
               "verification": read("verify"), "rollback_verification": read("verify-rollback")}
    if (directory / "ci-evaluation.json").exists():
        reports["ci_evaluation"] = read("ci-evaluation")
    evidence = {"schema_version": 1, "evaluation": identity,
                "clip_measurement_columns": ["transcribe_seconds", "wer"],
                "results": {name: result(report) for name, report in reports.items()},
                "transcripts": transcripts,
                "release": {key: value for key, value in release.items()
                            if key not in ("previous_baseline", "fresh_evaluation")},
                "deployment": read("deployment"), "rollback": read("rollback"),
                "rollback_deployment": read("rollback-deployment")}
    # One row per clip keeps measurements readable without repeating transcript bodies.
    text = json.dumps(evidence, indent=2)
    import re

    text = re.sub(r"\[\n\s+([0-9.eE+-]+),\n\s+([0-9.eE+-]+)\n\s+\]",
                  r"[\1, \2]", text)
    (directory / "evidence.json").write_text(text + "\n")
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    export(parser.parse_args().directory)
