"""MiniLM on onnxruntime: the one place the corpus and a question get turned into vectors.

Same model as before — `all-MiniLM-L6-v2`, same weights, same 384 numbers — but run through
`onnxruntime` instead of PyTorch. That is not a quality decision, it is a deployment one:
`sentence-transformers` drags in torch, scipy, transformers and scikit-learn, about **520 MB**
of dependencies for a job that is "text in, 384 floats out." Retrieval has to embed the
question on whatever machine serves it, so that weight lands on the host, and on a free tier
it is most of the cold start a visitor waits through. onnxruntime was already installed —
chromadb depends on it — so this removes a dependency rather than adding one.

**It changes no measured number, and that was verified rather than assumed:**

  - the 15 eval questions embedded both ways: **cosine 1.000000 on every pair**, max
    elementwise difference 1.9e-07, which is float32 rounding
  - token counts on 800 real corpus lines: **0 mismatches**, so chunk sizing is untouched and
    a future re-index produces the same boundaries

Both checks matter for different reasons. The vectors decide whether the committed index and
`data/eval/results.json` still describe this code. The tokenizer decides whether the 220/256
chunk ceiling still means what it meant — it is measured in the model's own WordPiece tokens,
so counting them differently would quietly reintroduce the truncation the ceiling prevents.

**Deliberately not done: int8 quantization.** ONNX makes it easy and it would shrink the model
further, but it changes the weights, and every number in the repo is only inherited for free
because the weights are identical. Cheap is the point; different is not.
"""

import os

import numpy as np
from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
from tokenizers import Tokenizer

# Kept as a literal so the id that names the model lives next to the code that loads it. Must
# match whatever built the index: embedding a question with a different model than the corpus
# gives two vectors in unrelated spaces, every distance is meaningless, and nothing looks broken.
MODEL_NAME = "all-MiniLM-L6-v2"

# Chroma ships this model for its own default embedding function, downloads it to ~/.cache on
# first use, and does the mean-pooling and normalization that `sentence-transformers` used to
# do invisibly. Reusing it is what makes the swap 20 lines instead of a hand-written pooling
# pass over the attention mask — which is the step where getting it subtly wrong degrades
# search while nothing appears to fail.
_BATCH = 64


def load_encoder():
    """The encoder. One object with `.encode()`, so callers do not know what runs underneath."""
    return Encoder()


class Encoder:
    """`.encode(texts)` -> normalized float32 vectors, one row per text.

    The signature mirrors the `SentenceTransformer` call it replaces, keyword arguments
    included, so the five scripts that embed things did not each need editing beyond the
    constructor. `normalize_embeddings` defaults to True here rather than False, because every
    caller in this repo passed True — the collection is cosine space over unit vectors.
    """

    def __init__(self):
        self._embed = ONNXMiniLM_L6_V2()

    def encode(self, texts, batch_size=_BATCH, show_progress_bar=False,
               normalize_embeddings=True, convert_to_numpy=True):
        texts = list(texts)
        rows = []
        for start in range(0, len(texts), batch_size):
            rows.extend(self._embed(texts[start:start + batch_size]))
            if show_progress_bar:
                # A plain counter, not tqdm: the indexer is the only caller that wants one,
                # it runs for minutes, and a carriage return is enough to watch it move.
                print("\r  {}/{} chunks".format(min(start + batch_size, len(texts)),
                                                len(texts)), end="", flush=True)
        if show_progress_bar:
            print()

        vectors = np.asarray(rows, dtype="float32")
        if normalize_embeddings:
            # Idempotent if the embedding function already normalized, and cheap either way.
            # Explicit because the collection's cosine space assumes unit vectors, and that
            # assumption should not rest on a library's undocumented internal choice.
            vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors


def load_tokenizer():
    """The model's own WordPiece tokenizer, for counting chunk sizes.

    A separate `Tokenizer` read off the same file rather than the encoder's instance, because
    Chroma configures its copy to pad every input to 256 tokens — borrowing it would report
    256 for a four-word line, and turning padding off would break the embedding function that
    depends on it. Truncation is off for the same reason: an over-long line has to report its
    real length so the chunker can split it, which is the whole point of counting.
    """
    embed = ONNXMiniLM_L6_V2()
    embed._download_model_if_not_exists()
    path = os.path.join(embed.DOWNLOAD_PATH, embed.EXTRACTED_FOLDER_NAME, "tokenizer.json")
    tokenizer = Tokenizer.from_file(path)
    tokenizer.no_padding()
    tokenizer.no_truncation()
    return tokenizer


def token_length(tokenizer, text):
    """Tokens in `text`, excluding `[CLS]`/`[SEP]`.

    Matches what `SentenceTransformer(...).tokenizer.tokenize()` returned — verified on 800
    corpus lines with zero mismatches. The special tokens are excluded there too, which is why
    the 256 ceiling has always had two tokens of slack it never advertised.
    """
    return len(tokenizer.encode(text, add_special_tokens=False).ids)
