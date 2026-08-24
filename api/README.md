# The HTTP service

A thin wrapper over `scripts/`. It adds transport, rationing and CORS; it adds no retrieval,
no chunking, no citation checking and no scoring. Every answer comes from `generate.py` at the
defaults the evaluation measured, so the served answer and the scored answer are the same
answer.

## Endpoints

| | | |
| --- | --- | --- |
| `GET /health` | instant | liveness and warmth. No model call, no provider call. |
| `POST /search` | ~0.4s | the passages for a question. No key, no provider, no quota. |
| `POST /answer` | ~10s | a cited answer plus the passages it was written from. |
| `GET /config` | instant | the model, retrieval depth and reasoning effort in use. |

Both `POST` bodies are `{"question": "..."}`. Interactive schema at `/docs`.

**Two endpoints rather than one** because the halves cost different things. Retrieval is local,
keyless and deterministic; generation is a network round trip. A single endpoint would make the
fast half wait for the slow one and show a visitor a blank spinner for the whole duration. The
front end calls both at once and paints twice.

`/answer` re-runs retrieval rather than accepting sources from the client. That costs a third of
a second and means the client cannot forge the passages an answer claims to be grounded in.

## Degrading rather than failing

`/answer` returns `200` with `result.answer = null` and a `degraded_category` whenever the model
cannot be reached — no key, budget spent, provider error, deadline passed. The passages are still
in the response, because they are the part that holds the information. The only `5xx` is `503`
when the index itself failed to load, which is the one failure that is genuinely the server's.

## Running it locally

```bash
venv/bin/pip install -r requirements.txt -r api/requirements.txt
set -a; . ./.env; set +a          # optional — without a key it serves retrieval only
venv/bin/uvicorn api.main:app --reload --port 8000
```

## Configuration

All optional; the defaults are what the container runs.

| variable | default | |
| --- | --- | --- |
| `GEMINI_API_KEY` | — | absent means retrieval-only, which is a supported mode, not an error |
| `ALLOWED_ORIGINS` | `http://localhost:3000` | comma-separated |
| `ALLOWED_ORIGIN_REGEX` | Vercel preview hosts | scoped to this project's prefix, not all of `*.vercel.app` |
| `PER_CLIENT_DAILY` | `10` | so one visitor cannot drain the day |
| `GLOBAL_DAILY` | `300` | headroom under the provider's own daily limit |
| `GENERATION_DEADLINE` | `200` | seconds; see below |
| `MAX_QUESTION_CHARS` | `500` | |
| `QUOTA_ZONE` | `America/Los_Angeles` | matched to the provider's own reset, not to UTC |

## Three things that are easy to get wrong here

**The ingress cuts a request at 240 seconds and the value is not configurable.** `generate.py`'s
own worst case is longer — three attempts at a 90-second read timeout with up to 60 seconds of
honored `Retry-After` between them is 390 seconds. Past 240 the visitor's connection is already
closed, so `GENERATION_DEADLINE` bounds the work at 200 and returns a degraded answer instead.

**`X-Forwarded-For` is read rightmost-first, not leftmost.** Azure Container Apps *appends* the
connecting address to whatever the client sent, and only that rightmost entry is vouched for.
The usual `split(",")[0]` would read a value the client chose, so sending a different fake
address on each request would mint a fresh budget every time and make the cap decorative.

**Retrieval is serialized behind a lock; generation is not.** Chroma writes a few bytes to its
sqlite file on every read, so concurrent queries are concurrent writers to one connection.
The lock covers the third of a second that retrieval takes and is released before the ten-second
provider call, so concurrent visitors queue only on the cheap half.

## Building the image

Built from the repository root, because the image needs `scripts/`, `data/processed/` and
`data/chroma.tar.gz`, all of which live above this directory. CI does this on every push that
touches them; the commands below are only for reproducing a build locally.

```bash
docker build -f api/Dockerfile -t naturalization-api .
docker run -p 8000:8000 -e GEMINI_API_KEY=... naturalization-api
```

The index is unpacked and the ~80 MB embedding model is downloaded **at build time**, so a
started container needs no network to serve its first request. Without that, the first request
after every restart waits on a download, and a network blip at that moment takes down an app
that is otherwise entirely self-contained.

No local Docker daemon is required anywhere in the deploy: the image is built by GitHub Actions.

## Where the image is built, and why not in Azure

`az acr build` is the obvious way to build without a local daemon — upload the context, build it
in Azure Container Registry. **It is blocked here.** Azure pauses ACR Tasks for any subscription
funded by trial credits, which includes Azure for Students:

```
(TasksOperationsNotAllowed) ACR Tasks requests for the registry <name> and <subscription>
are not permitted.
```

The documented fix is upgrading to Pay-As-You-Go, which means a credit card — the specific thing
this hosting choice was made to avoid. So the build moved to
[`.github/workflows/image.yml`](../.github/workflows/image.yml), which publishes to the GitHub
Container Registry. That turned out better than the original plan on every axis: free rather than
$5.07/month for a registry, no daemon on anyone's machine, and the build is reproducible in CI
rather than from one laptop.

The image must be **public** for Container Apps to pull it without credentials. GitHub creates
packages private, so the first successful workflow run needs a one-time visibility change under
*Packages → naturalization-api → Package settings*.

## Deploying to Container Apps

Nothing in this repository references an Azure account. The only link is `az login`, which caches
a token locally; the deploy then creates everything under whichever subscription that account
owns.

```bash
brew install azure-cli
az login
az extension add --name containerapp --upgrade
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.OperationalInsights
```

Then the environment and the app:

```bash
az group create --name naturalization --location westus2

az containerapp env create \
  --name naturalization-env --resource-group naturalization --location westus2

az containerapp create \
  --name naturalization-api --resource-group naturalization \
  --environment naturalization-env \
  --image ghcr.io/<owner>/naturalization-api:latest \
  --target-port 8000 --ingress external \
  --cpu 0.5 --memory 1.0Gi --min-replicas 1 --max-replicas 1 \
  --secrets gemini-key=<key> \
  --env-vars GEMINI_API_KEY=secretref:gemini-key
```

`az containerapp up` is *not* used, despite reading like the one-command version of this: in the
current CLI it accepts no `--cpu`, `--memory`, `--min-replicas` or `--secrets`, and no
`--dockerfile` to find a Dockerfile that is not at the context root. Check `--help` rather than
the docs page; the two disagree.

`--min-replicas 1` keeps the container warm, which removes the cold start without any scheduler.
`--max-replicas 1` is deliberate: the rate-limit counters live in process memory, so a second
replica would hand out a second copy of the day's budget.

The key goes in as a secret and is referenced by name. It never appears in the image, the repo,
or the front end — the browser talks only to this service, and only this service talks to the
provider.

`ALLOWED_ORIGINS` cannot be known until the front end is deployed, and the front end needs this
service's URL, so the two are deployed in two passes: this first, the site with the URL Azure
prints, then `az containerapp update --set-env-vars ALLOWED_ORIGINS=...` with the site's origin.
