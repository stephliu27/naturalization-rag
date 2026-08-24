"""HTTP over the retrieval and generation the CLI already runs. A wrapper, not a second app.

Same relationship to `scripts/` that `app.py` has: every answer comes from `answer_question()`
at the measured defaults, and nothing here re-implements retrieval, chunking, citation checking
or scoring. If a number moves in `scripts/`, it moves here without an edit.

**Two endpoints rather than one, because the two halves cost different things.** Retrieval is
local, keyless, deterministic and about a third of a second; generation is a network round trip
to Gemini and about ten. A single endpoint would make the fast half wait for the slow one and
show a visitor a blank spinner for the whole duration. So `/search` returns passages
immediately and `/answer` returns the written answer, and the page fills in twice.

`/answer` re-runs retrieval rather than accepting sources from the client. That costs a third of
a second and buys two things: the client cannot forge the passages an answer is grounded in, and
there is exactly one code path that builds a source block. Retrieval is deterministic, so the
passages `/answer` returns are the same objects `/search` already rendered.

Run locally:  venv/bin/uvicorn api.main:app --reload --port 8000
"""

import concurrent.futures
import contextlib
import datetime
import logging
import os
import sys
import threading
import zoneinfo

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# `query.py` resolves `data/chroma` against the working directory and a server process can be
# started from anywhere — a container's WORKDIR, a systemd unit, a shell in `api/`. One chdir
# keeps this reading the same index as the CLI, exactly as `app.py` does.
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import chromadb  # noqa: E402  (after the chdir, which nothing above may depend on)

from encoder import load_encoder  # noqa: E402
from generate import GEMINI_MODEL, THINKING_LEVEL, build_sources, generate  # noqa: E402
from query import COLLECTION, INDEX_ARCHIVE, INDEX_DIR, TOP_K, ensure_index, search  # noqa: E402

log = logging.getLogger("api")

# --- configuration, all overridable from the environment so a deploy needs no code change ---

# Vercel gives every branch and every commit its own preview hostname, so an explicit list would
# have to be edited on each one. The regex covers previews; ALLOWED_ORIGINS covers the
# production domain and local development. Both are needed: a bare regex would also match
# anybody else's `*.vercel.app` project, which is why it is scoped to this project's prefix.
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]
ALLOWED_ORIGIN_REGEX = os.environ.get(
    "ALLOWED_ORIGIN_REGEX", r"https://naturalization-rag[a-z0-9-]*\.vercel\.app")

# Two caps, because they protect against different things. The per-client cap stops one visitor
# draining the day; the global cap stops fifty visitors doing it collectively, and leaves
# headroom under Flash-Lite's ~500 requests a day for running the eval by hand.
PER_CLIENT_DAILY = int(os.environ.get("PER_CLIENT_DAILY", "10"))
GLOBAL_DAILY = int(os.environ.get("GLOBAL_DAILY", "300"))

# Gemini's free-tier day rolls over at midnight Pacific, so the counters that ration it have to
# roll over at the same moment. A UTC day would reset the local budget eight hours early half
# the year and seven the other half, handing out a second day's quota against a tier that had
# not refilled. `tzdata` is a pinned dependency for this one line — a slim container image
# carries no zone database.
QUOTA_ZONE = zoneinfo.ZoneInfo(os.environ.get("QUOTA_ZONE", "America/Los_Angeles"))

# Azure Container Apps' HTTP ingress cuts a request off at 240 seconds and the value is not
# configurable. `generate()`'s own worst case is longer than that: three attempts at a 90-second
# read timeout with up to 60 seconds of honored Retry-After between them is 390 seconds. Past
# 240 the visitor's connection is already gone, so the remaining work is spent on nobody. The
# deadline turns that into a degraded answer the page can render.
GENERATION_DEADLINE = int(os.environ.get("GENERATION_DEADLINE", "200"))

# A question is a question. The cap is not about abuse so much as about the prompt: the model
# sees the question after 3.5K tokens of statute, and something pasted in at length stops being
# the thing the sources were retrieved for.
MAX_QUESTION_CHARS = int(os.environ.get("MAX_QUESTION_CHARS", "500"))


# --- process state, built once at startup -------------------------------------------------


class State:
    """The expensive objects, plus the lock that serializes access to the index.

    Loaded at startup rather than on first request. With `min-replicas = 1` the container is
    always running, so a lazily-loaded encoder would mean the *first visitor after a deploy*
    pays the model load — which is the one visitor most likely to be the person who was sent
    the link. Startup loading also makes `/health` honest: it can report whether the thing is
    actually able to answer, rather than only that a process is listening.
    """

    def __init__(self):
        self.collection = None
        self.encoder = None
        self.error = None
        # Chroma writes about eighteen bytes to its sqlite file on every *read*, so two threads
        # querying at once are two writers to one connection rather than two readers. FastAPI
        # runs `def` endpoints in a threadpool, so that is the default situation, not an edge
        # case. Retrieval is a third of a second and this demo will never see enough
        # concurrency for the queueing to show; generation is the slow half and is deliberately
        # left outside the lock, so ten-second calls still overlap.
        self.lock = threading.Lock()


state = State()


def load_state():
    """Unpack the shipped index, open the collection, load the encoder. Records its own failure.

    `load_collection()` from `query.py` is not reused because it calls `sys.exit`, which is
    right for a CLI and fatal here — `SystemExit` during startup kills the worker with a stack
    trace instead of leaving a server that can explain itself. `ensure_index()` is the half
    worth sharing, and it neither exits nor raises precisely so that every caller can fail in
    its own way.
    """
    if not ensure_index():
        state.error = "no index at {}/ and no archive at {}".format(INDEX_DIR, INDEX_ARCHIVE)
        return
    try:
        state.collection = chromadb.PersistentClient(path=INDEX_DIR).get_collection(COLLECTION)
    except Exception as error:  # noqa: BLE001 — startup reports, it does not diagnose
        state.error = "collection '{}' unavailable: {}".format(COLLECTION, error)
        return
    state.encoder = load_encoder()


# --- rationing -----------------------------------------------------------------------------


class DailyQuota:
    """Per-client and global request counts against a Pacific day boundary.

    In process memory on purpose. The alternative is Redis, which is a second service and a
    second bill to protect a free tier — and at `min-replicas = 1` there is exactly one process,
    so a dict is not an approximation of shared state, it *is* the shared state. What it does
    not survive is a restart or a scale-out, and the failure there is generous rather than
    unsafe: counters reset and the day's budget is briefly re-offered. The global cap sits far
    enough under the real limit to absorb that.
    """

    def __init__(self, per_client, global_limit, zone):
        self.per_client = per_client
        self.global_limit = global_limit
        self.zone = zone
        self.lock = threading.Lock()
        self.day = None
        self.clients = {}
        self.total = 0

    def _roll(self):
        today = datetime.datetime.now(self.zone).date()
        if today != self.day:
            self.day, self.clients, self.total = today, {}, 0

    def take(self, client):
        """Spend one request for `client`. Returns None, or the reason it was refused.

        Counted before the call rather than after, the same way `app.py` counts: a request that
        fails upstream has still been spent against the provider's quota, and this exists to
        protect that quota rather than to count successes.
        """
        with self.lock:
            self._roll()
            if self.total >= self.global_limit:
                return "global"
            if self.clients.get(client, 0) >= self.per_client:
                return "client"
            self.clients[client] = self.clients.get(client, 0) + 1
            self.total += 1
            return None

    def remaining(self, client):
        with self.lock:
            self._roll()
            return min(self.per_client - self.clients.get(client, 0),
                       self.global_limit - self.total)


quota = DailyQuota(PER_CLIENT_DAILY, GLOBAL_DAILY, QUOTA_ZONE)


def client_id(request):
    """Who to ration, read from `X-Forwarded-For` — **rightmost** entry, not leftmost.

    This is the one line where the conventional snippet is actively wrong on this platform.
    Azure Container Apps' ingress *appends* the connecting address to whatever the client sent,
    and its own documentation says "only the rightmost IP is provided by Azure Container Apps.
    Any other values must be validated by the user to prevent IP spoofing." Every tutorial takes
    `split(",")[0]`, which here is the value the *client* chose: sending a different fake
    address on each request would mint a fresh per-client budget every time and make the cap
    decorative. The rightmost entry is the only one the ingress vouches for.

    Falls back to the socket address for local development, where there is no proxy at all.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.rsplit(",", 1)[-1].strip()
    return request.client.host if request.client else "unknown"


# --- generation, bounded -------------------------------------------------------------------

# One worker thread per in-flight generation, and threads that outlive their request are left to
# finish rather than being killed — a blocking `requests` call cannot be interrupted from
# outside, and pretending otherwise is how a timeout becomes a lie. An abandoned thread is
# parked on a socket read costing no CPU, and it exits on its own within the provider timeout.
POOL = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="generate")


def retrieve(question):
    """The passages for a question, under the index lock. The only place `search` is called.

    `answer_question()` is not used by this module even though it is the documented seam, and
    the reason is the lock: it retrieves and generates in one call, and the lock has to be
    released *between* those two halves. Holding it across a ten-second provider round trip
    would make two concurrent visitors wait twenty seconds for what should cost ten. This is
    the same two lines `answer_question` runs, composed differently — not a second
    implementation of either half.
    """
    with state.lock:
        return build_sources(state.collection,
                             search(state.collection, state.encoder, question, k=TOP_K))


def generate_bounded(question, sources):
    """`generate()` with a wall-clock ceiling. Returns None if it ran past the deadline.

    The ceiling exists because of the ingress, not because of the model: Container Apps ends a
    request at 240 seconds regardless, so work past that point is spent on a connection that is
    already closed. Returning None lets the caller degrade the way every other failure here
    degrades — passages shown, one sentence explaining the missing answer.
    """
    future = POOL.submit(generate, question, sources)
    try:
        return future.result(timeout=GENERATION_DEADLINE)
    except concurrent.futures.TimeoutError:
        log.warning("generation exceeded %ss", GENERATION_DEADLINE)
        return None


def key_available():
    """Whether generation can run at all.

    Checked here rather than inside `generate()` because `api_key()` calls `sys.exit`, and
    `SystemExit` derives from `BaseException` — `generate()` catches `FetchError` and
    `ParseError`, so a missing key does not degrade, it escapes `answer_question()` and takes
    the worker with it. One `os.environ` read in front of the call is the whole fix.
    """
    return bool(os.environ.get("GEMINI_API_KEY"))


def degraded(reason, category, sources):
    """The shape `generate()` returns when it could not produce an answer.

    Built by hand rather than imported, because these three failures happen *before* the
    provider is ever called: there is no key, the budget is spent, or the deadline passed. Keys
    match `generate()`'s own result dict so the client has one shape to render, not two.
    """
    return {"question": None, "answer": None, "degraded": reason, "degraded_category": category,
            "cited": [], "unknown": [], "uncited": [s["label"] for s in sources],
            "malformed": [], "citations": 0, "truncated": False, "usage": {},
            "model": GEMINI_MODEL, "provider": "gemini"}


# --- the app ---------------------------------------------------------------------------------

@contextlib.asynccontextmanager
async def lifespan(_app):
    """Startup work, in the form FastAPI still supports — `on_event` is deprecated.

    The yield separates startup from shutdown. There is no shutdown work: the encoder and the
    Chroma client hold no connection worth closing, and the generation pool's threads are
    daemons parked on socket reads.
    """
    load_state()
    if state.error:
        log.error("startup: %s", state.error)
    yield


app = FastAPI(
    title="Naturalization Barrier Navigator",
    description="Retrieval and cited generation over USCIS Policy Manual chapters and federal "
                "court opinions on naturalization.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=ALLOWED_ORIGIN_REGEX,
    # No credentials, so no cookies and no `Authorization` — the API is public and read-only,
    # and allowing them alongside a permissive origin regex is the classic way to turn CORS into
    # a vulnerability rather than a control.
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class Ask(BaseModel):
    """The only thing a client sends. Validation lives here so no endpoint repeats it."""

    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)


# Responses are plain dicts rather than declared `response_model`s, and that is a deliberate
# trade against the free OpenAPI schema. A source block's shape is defined by `build_sources()`
# in `generate.py`; restating its fourteen fields as a pydantic model would be a second
# definition of the same contract, in a different file, that nothing checks against the first.
# The repo's standing rule is that there is one implementation of anything, and a schema that
# silently drifts from the objects it claims to describe is worse than no schema.


def unavailable():
    """503 for the case where the index never loaded.

    A real status code rather than a 200 carrying an `error` key, because this is the one
    failure that is genuinely the server's fault and not a degradation the page should render
    as content. Everything else here — no key, no budget, provider down — returns 200 with
    passages, because in those cases the page still has something worth showing.
    """
    return JSONResponse(status_code=503,
                        content={"error": "index unavailable", "detail": state.error})


@app.get("/health")
def health():
    """Liveness plus warmth, with no model call and no provider call.

    Deliberately cheap enough to be hit on every page load. That ping is not about
    `min-replicas`, which already keeps a container running — it is the layer that makes a cold
    start invisible if the replica count is ever set back to zero, and it fires from the
    visitor's browser at exactly the moment warmth is about to matter.
    """
    return {
        "status": "ok" if state.encoder is not None else "degraded",
        "index": state.collection is not None,
        "encoder": state.encoder is not None,
        "generation": key_available(),
        "detail": state.error,
    }


@app.post("/search")
def do_search(ask: Ask, request: Request):
    """Passages for a question. No key, no provider, no quota — this half is free.

    It is also the half that keeps working when everything else does not, which is why the
    front end calls it first and renders from it immediately rather than waiting on `/answer`.
    """
    if state.encoder is None:
        return unavailable()

    question = ask.question.strip()
    return {"question": question, "sources": retrieve(question), "k": TOP_K,
            "generation_remaining": quota.remaining(client_id(request))}


@app.post("/answer")
def do_answer(ask: Ask, request: Request):
    """A cited answer, with the passages it was written from.

    Never fails on a provider problem — it degrades and returns the passages, because a page
    showing eight citable sources and one sentence about why the model is quiet is strictly
    better than an error. Quota exhaustion is a normal day on a free tier.
    """
    if state.encoder is None:
        return unavailable()

    question = ask.question.strip()
    client = client_id(request)

    # Retrieved first, unconditionally, because every path below returns passages — including
    # all three of the paths that never reach the provider. Retrieval is free and keyless, so
    # there is nothing to save by ordering the checks in front of it, and doing so would mean
    # three call sites instead of one.
    sources = retrieve(question)

    def refuse(reason, category):
        return {"question": question, "sources": sources,
                "result": degraded(reason, category, sources), "generation_remaining": 0}

    if not key_available():
        return refuse("no provider key configured", "no_key")

    refusal = quota.take(client)
    if refusal == "client":
        return refuse("this session has used its {} generated answers".format(PER_CLIENT_DAILY),
                      "cap_reached")
    if refusal == "global":
        return refuse("the day's shared budget is spent", "cap_reached")

    # Outside the lock, deliberately: this is the ten-second half, and serializing it would make
    # two concurrent visitors wait twenty seconds for what should cost ten.
    result = generate_bounded(question, sources)
    if result is None:
        result = degraded("generation took longer than {}s".format(GENERATION_DEADLINE),
                          "deadline", sources)

    return {"question": question, "sources": sources, "result": result,
            "generation_remaining": quota.remaining(client)}


@app.get("/config")
def config():
    """What this deployment is running, so the front end never hardcodes a default.

    The same principle the Streamlit sidebar follows: a value typed into a UI drifts from the
    code the first time the code changes. `TOP_K` and `THINKING_LEVEL` are the measured winners
    of a grid, and a page that names them should read them.
    """
    return {"model": GEMINI_MODEL, "k": TOP_K, "thinking": THINKING_LEVEL,
            "per_client_daily": PER_CLIENT_DAILY, "max_question_chars": MAX_QUESTION_CHARS}
