# The site

Next.js and Tailwind, deployed to Vercel, talking to the FastAPI service in [`../api`](../api).
Two pages:

- **`/`** — the question box. User-facing, no jargon: an answer whose every claim links to the
  passage behind it, and the passages underneath it.
- **`/evaluation`** — the numbers, for a reader who wants to know whether to believe the first
  page. Kept off the main page on purpose; `recall@8` is not a phrase anybody asking about a fee
  waiver needs to read.

The Streamlit app at the repository root is unaffected and stays live. This deploys separately
and shares nothing but `scripts/`.

## Running it

```bash
npm install
cp .env.local.example .env.local     # points at http://localhost:8000
npm run dev
```

The API has to be running too — see [`../api/README.md`](../api/README.md). Without it the pages
still render, because every number on them is baked in at build time; only asking a question
needs the backend.

## Two things worth knowing before changing it

**Every number is read from `../data/eval/` at build time, and nothing is typed in.** That is
[`lib/evals.ts`](lib/evals.ts). It also parses `TOP_K`, `THINKING_LEVEL` and `GEMINI_MODEL` out
of the Python that defines them, rather than restating them here — the generation grid scored
four configurations, and naming the wrong one would report a score for a setting the API is not
running. Every read throws instead of defaulting, so a misconfigured build fails loudly rather
than shipping a page of zeroes that looks measured and is not.

**Both requests fire at once.** `/search` returns in about a third of a second and gives the
reader something true to look at while `/answer` spends ten seconds at the model. Responses carry
the id of the request that produced them, because asking a second question while the first is
still generating otherwise lets the older answer land last and overwrite the newer one.

The answer is rendered by splitting the string and emitting React nodes — never
`dangerouslySetInnerHTML`. Model output cannot become markup.

## Deploying to Vercel

Set the project's **root directory** to `web/`, and enable **"Include source files outside of the
Root Directory in the Build Step."** Without that second setting the build cannot reach
`../data/eval/` and will fail with a message saying exactly this.

One environment variable:

```
NEXT_PUBLIC_API_BASE = https://<your-container-app>.azurecontainerapps.io
```

`NEXT_PUBLIC_` is required because the value is read in the browser. It is not a secret — it is a
public URL. The model API key lives only on the container and never reaches the front end.

Then add the deployed origin to `ALLOWED_ORIGINS` on the container app, or the browser will
block every request from it.
