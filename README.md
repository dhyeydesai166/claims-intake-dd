# Claims Intake Service

This service accepts a first notice of loss (FNOL). It checks the notice against the policy master and the rule table in `docs/api-contract.md`, then either records the notification and returns a claim reference or refuses it with a typed reason.

`docs/api-contract.md` is the authority: what the service accepts, what it returns, and under what conditions it refuses. Where the code and that document disagree, the document is correct.

You are already in the course Linux container. Dependencies are present. If a tool you need is missing, that is a defect in the image specification — report it rather than working around it.

## Where things are

| Path | What it holds |
| --- | --- |
| `docs/api-contract.md` | What the service accepts, returns, and refuses. The authority. |
| `docs/requirements-brief.md` | The open work items and their acceptance criteria. |
| `docs/payload-triage.md` | Day 1 classification of the edge payloads. |
| `data/` | Synthetic policies and notification payloads. |
| `src/claims/` | The service. |
| `tests/` | Unit tests mirror `src/claims/`. Integration tests exercise HTTP. |

The HTTP app object is `app` in `src/claims/api/routes.py`. The intake endpoint is `POST /notifications`. A well-formed, admissible notice is recorded and returns `201` with a claim reference. A refusal uses the error envelope in section 5 of the contract.

## Run the service

From the repository root:

```
uv run uvicorn claims.api.routes:app --host 0.0.0.0 --port 8000
```

Open the generated docs at `http://127.0.0.1:8000/docs`.

## Test

```
uv run pytest
uv run ruff check .
uv run mypy
```

## Docker image

This workspace is often Linux on ARM (`aarch64`). Docker on that machine will default to an ARM image. GitHub `ubuntu-latest` and most servers you will actually run on are `linux/amd64`. If you skip the platform, you get an image that builds here and then fails to run in CI or on a typical x86_64 host. `--platform linux/amd64` is the override that produces the architecture those environments expect. `buildx` is the builder that honours that flag.

From the repository root:

```
docker buildx build --platform linux/amd64 -t claims-intake:day4 .
```

The image listens on port 8000. `StubPolicyClient` reads `data/policies.json`, so `data/` is in the image.

## Data

Everything in `data/` is synthetic and was authored for this program. It contains no real client data and no named clients. Recorded notifications live in memory for this week.
