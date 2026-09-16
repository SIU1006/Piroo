# Reproducible model evaluation and release evidence

## Evaluation contract

The committed corpus contains 200 LibriSpeech validation clips. Registration,
CI and promotion use the **same explicit first 20 filenames** in
`model_eval/evaluation_config.json`, not different implicit clip limits.
References and every media file contribute to a full dataset SHA-256; the
ordered evaluated examples have a separate subset hash. The complete decoding,
normalization, device and compute configuration is hashed and recorded.
Changing audio, references, subset or configuration makes comparisons fail.

Each raw result includes immutable Hugging Face revision, runtime package versions,
image identity, evaluation time and per-clip reference/hypothesis/WER/latency.
Images download that pinned revision at build time and load it offline.
`POST /metadata` exposes the loaded model contract; `/transcribe` returns plain
text. HTTP evaluation checks that live metadata matches the intended model and
decoding configuration before sending the examples.

`baseline.json` now contains the measured tiny baseline from the isolated release
example below. The old small/v7 metrics are retained in `baseline-legacy.json`;
their missing dataset/subset identity makes them unsuitable for comparison.
This demonstrator baseline does not assert that the shared kind production
deployment was changed. CI builds the baseline's chosen size, evaluates the
actual image against the committed baseline, and publishes that same saved
image rather than rebuilding it after evaluation. Local-only evaluations are
explicitly marked `not-deployed`.

## Registration and fresh promotion

Use `python -m model_eval.register_model`. For deployed candidates, pass paired
`--endpoints size=http://candidate:3000` and `--images size=repository@sha256:...`
values. Local benchmarking remains available but cannot authorize production
promotion without a registered deployed-image identity.

Promotion requires the same candidate image, candidate-only endpoint, and an
identity-bearing baseline matching the current production registry version:

```bash
python -m model_eval.promote_model \
  --endpoint http://candidate:3000 \
  --deployed-image repository@sha256:<manifest-digest> \
  --baseline-path model_eval/baseline.json \
  --release-path release.json
```

It **runs fresh inference**, logs a new verification run, checks same-example
WER regression and absolute WER/RTF budgets, then updates `@production` and the
baseline. It does not deploy a container. `release.json` preserves the previous
version/baseline and exact authorized image for explicit deployment and rollback.
The rollback helper refuses stale receipts if the production alias has moved
since that release. Keep registry access under one release operator; alias and
filesystem updates are separate operations, not a distributed transaction.

## Captured complete example — 2026-09-16

**Scope:** isolated Docker network and a real local SQLite-backed MLflow registry.
No shared staging/production deployment or registry alias was changed. These
are real CPU model evaluations and Docker deployments, not simulated receipts.
Image IDs below are local Docker **configuration digests**; registry deployments
must use their published manifest digests. Hardware, evaluator image and source
hashes are in [environment.json](model-loop/20260916-tiny-to-base/environment.json).

| Phase | Registry/model | WER | RTF | Evidence |
|---|---|---|---|---|
| Baseline evaluation | tiny v3 | 6.68% | 0.059 | [Baseline](model-loop/20260916-tiny-to-base/evidence.json) |
| Candidate evaluation | base v4 → staging | 6.68% | 0.104 | [Candidate](model-loop/20260916-tiny-to-base/evidence.json) |
| Fresh evaluation and promotion | base v4 → production | 6.68% | 0.127 | [Release receipt](model-loop/20260916-tiny-to-base/evidence.json) |
| Deploy promoted base image | stable container uses candidate digest | — | — | [Deployment receipt](model-loop/20260916-tiny-to-base/evidence.json) |
| Fresh deployed verification | base, production v4 | 6.68% | 0.128 | [Verification](model-loop/20260916-tiny-to-base/evidence.json) |
| Roll back registry and deployment | tiny, production v3 | — | — | [Registry rollback](model-loop/20260916-tiny-to-base/evidence.json), [image rollback](model-loop/20260916-tiny-to-base/evidence.json) |
| Fresh rollback verification | tiny, production v3 | 6.68% | 0.076 | [Verification](model-loop/20260916-tiny-to-base/evidence.json) |

Versions 1/2 were preliminary evaluations before the final image-context
exclusions; the final evaluated images are v3/v4. All phases use the same
examples/configuration. WER is corpus-weighted, not the average clip WER.
The demo allowed WER ≤0.20, regression ≤0.02 and RTF ≤2.0. Base did not improve
WER on these examples and was slower; this is release-mechanism evidence,
not a claim that base is the better model. Twenty clean clips cannot establish
quality on long, noisy, multilingual or production recordings.

Final [registry state](model-loop/20260916-tiny-to-base/registry-final.json) points
production to v3 and staging to v4. The previous exact tiny image was restored.
The [updated CI command](model-loop/20260916-tiny-to-base/evidence.json) was also
run against the live rollback image and passed. The registry database and large
model artifacts stay local; portable JSON receipts contain the audit evidence.
The committed `evidence.json` stores one shared evaluation identity and each unique
transcript set once. Phase results reference transcript hashes and preserve
individual clip timings and WER. The runtime baseline keeps the hashes,
configuration and aggregate metrics needed by CI and promotion; its full details
are in this evidence file. Two reproduction runs retain compact receipts only.
Raw logs and exports remain local and are ignored by Git.
Reproduce in a fresh directory with `docs/model-loop/run-demo.ps1`; it also
exports the consolidated evidence after all phases complete.

The final models-from-code registration implementation also completed all five
phases in a [fresh registry](model-loop/20260916-portable-model-check/receipt.json).
A separate [artifact loading check](model-loop/20260916-portable-model-check/receipt.json)
loaded candidate v2 with `mlflow.pyfunc.load_model` from `/tmp` and transcribed
clip 00. Its transcript matched the candidate's HTTP evaluation. This checks
that the registered artifact can run outside the repository working directory.

## Candidate-only synthetic canary

The stable Service selects `app: whisper, track: stable`; the candidate Service
selects `app: whisper, track: canary`. [Rendered selector checks](model-loop/20260916-tiny-to-base/helm-isolation.json)
confirm they cannot select the same tracks. This Helm configuration was rendered
and linted; it was not installed over shared production during the local demo.
The checker requires `CANARY_WHISPER_URL` and `CANARY_WHISPER_IMAGE`; with no
candidate URL it skips instead of scoring mixed or stable traffic. Candidate
images can be specified through Helm's `whisper.canary.image.digest` value.
The synthetic check uses its own ten-clip identity and stores the full report
at Redis `canary:evaluation`. Its absolute score is a live diagnostic, not a
comparison against the different twenty-clip CI subset.

## Small summary-quality evaluation

Three authored cases in `model_eval/summary_cases.json` cover an incident,
model comparison and a pending infrastructure decision. Required facts and
contradictions were specified before generation. The runner uses the same
production prompt and options (`temperature=0`, `seed=42`), records the Ollama
model digest, deployed image, dataset hash, raw outputs and timing. A fixed seed
helps repeatability but does not guarantee identical output across runtimes.

- [Raw generated cases](model-loop/20260916-tiny-to-base/summary-quality.json)
- [Explicit qualitative rubric and checks](model-loop/20260916-tiny-to-base/summary-review.json)

| Case | Fully supported required facts | Main finding |
|---|---|---|
| Incident | 5/5 | Preserves specified facts; overstates a planned prevention step and adds a preface |
| Model comparison | 2/5 | Reverses 8% vs 6% WER; partially loses the exact speed ratio and noise caveat |
| Pending decision | 5/6 | Preserves uncertainty, cost, owner/deadline; omits proposed next-month timing |

The three bodies meet the requested 3–5 sentence length, but all add unwanted
prefaces. One case has a critical factual contradiction. Partial facts receive
no full coverage credit. This **assistant-assisted diagnostic review**, with
three synthetic examples and no independent human judge, does not establish
acceptable summary quality. Expand the set and obtain independent review before
claiming improvements; numerical relationships and uncertainty are clear targets
for the next prompt/model comparison.

Reproduce generation with `python -m model_eval.summary_eval --endpoint URL
--model llama3.2 --deployed-image repository@sha256:DIGEST --output review.json`.
Supply the actual Ollama image identity from the deployment and review outputs
against the predefined facts; generation alone is not a quality score.
