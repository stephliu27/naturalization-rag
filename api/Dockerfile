# Built from the repository root, not from `api/`:
#   docker build -f api/Dockerfile -t naturalization-api .
# The image needs `scripts/`, `data/processed/` and `data/chroma.tar.gz`, all of which live
# above this file, and a build context cannot reach outside itself.

# 3.12 rather than the 3.9.6 the development venv runs, and the range is not arbitrary:
# `onnxruntime==1.19.2` is pinned because it is the library that computes the embeddings, and it
# publishes wheels for 3.9 through 3.12. CI already proves the pins resolve at both ends of that
# range on every push, so the top of it is a tested choice rather than a hopeful one.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies before source, so editing a Python file does not re-resolve and re-download the
# whole runtime. These two lines change about once a month; the source below changes constantly.
COPY requirements.txt ./requirements.txt
COPY api/requirements.txt ./api/requirements.txt

# `streamlit` is dropped rather than kept in a second pin file. It is a direct dependency of
# `app.py`, which is not what this image serves, and it drags pandas, pyarrow, altair and pillow
# behind it — roughly 250 MB installed for code that never executes here. Filtering one line out
# of the canonical list keeps every version that protects a recorded number in exactly one file,
# which a parallel `requirements-api.txt` would not.
RUN grep -v '^streamlit==' requirements.txt > /tmp/runtime.txt \
    && pip install -r /tmp/runtime.txt -r api/requirements.txt

COPY scripts/ ./scripts/
COPY api/ ./api/
COPY data/ ./data/

# The index unpacked at build time rather than on first request. `ensure_index()` does this
# lazily and takes about a tenth of a second, so this is not about speed — it is about failing
# here, where a broken or missing archive stops the build, instead of at 3 a.m. inside a
# container that has already reported itself healthy.
RUN python -c "import sys; sys.path.insert(0, 'scripts'); \
from query import ensure_index; \
raise SystemExit(0 if ensure_index() else 'no index and no archive to unpack')"

# Non-root, and the UID is fixed rather than left to the distro so the numeric owner of anything
# written at run time is predictable.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app
USER app
ENV HOME=/home/app

# The ~80 MB MiniLM download baked in, and this one genuinely matters. Chroma fetches the model
# to `$HOME/.cache` on first use, so without this the first request after every restart waits on
# a download — and a network blip at that moment takes down an app that is otherwise entirely
# self-contained. Run *after* `USER app` on purpose: the cache path is derived from `$HOME`, so
# downloading as root would put it in `/root/.cache` where this user cannot read it, and the
# failure would be a silent re-download at run time rather than a build error.
RUN python -c "from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2; \
ONNXMiniLM_L6_V2()._download_model_if_not_exists()"

# Container Apps injects PORT and routes to whatever `targetPort` says; 8000 is the default for
# a local run. One worker, because there is one Chroma index and one encoder behind a lock in
# the process — a second worker would double the memory for no additional throughput on the
# half that is actually slow, which is the network call to the provider.
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
