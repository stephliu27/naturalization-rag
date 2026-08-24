"use client";

import { useEffect, useRef, useState } from "react";

import { Answer } from "@/components/Answer";
import { Sources } from "@/components/Sources";
import { answer, search, warm, type Result, type Source } from "@/lib/api";

const EXAMPLES = [
  "Can I get a fee waiver for naturalization?",
  "Do I still qualify if I was arrested but never charged?",
  "What happens if I fail the English test?",
  "How long can USCIS take to decide my application?",
];

export function Ask() {
  const [question, setQuestion] = useState("");
  const [asked, setAsked] = useState("");
  const [sources, setSources] = useState<Source[] | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [writing, setWriting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Every response carries the id of the request that produced it. Without this, asking a second
  // question while the first is still generating lets the slower, older answer land last and
  // overwrite the newer one — the two calls are ten seconds apart and racing by construction.
  const current = useRef(0);

  // One throwaway request at page load. With `min-replicas = 1` the container is already up, so
  // this is insurance rather than a dependency: it is what hides a cold start if the replica
  // count is ever set back to zero, and it fires from the visitor's browser at exactly the
  // moment warmth is about to matter.
  useEffect(warm, []);

  async function ask(text: string) {
    const q = text.trim();
    if (!q) return;
    const id = ++current.current;

    setAsked(q);
    setSources(null);
    setResult(null);
    setError(null);
    setWriting(true);

    // Both calls fire at once rather than in sequence. `/search` comes back in about a third of
    // a second and gives the reader something true to look at while `/answer` spends ten seconds
    // at the model. `/answer` runs its own retrieval, so it does not wait on `/search` — the
    // extra third of a second of server work buys the page its first paint.
    const fast = search(q)
      .then((r) => {
        // Only if the answer has not already landed: `/answer` returns the same passages plus
        // the knowledge of which were cited, so it must never be overwritten by the older call.
        if (id === current.current) setSources((existing) => existing ?? r.sources);
      })
      .catch(() => {});

    const slow = answer(q)
      .then((r) => {
        if (id !== current.current) return;
        setSources(r.sources);
        setResult(r.result);
      })
      .catch((e: Error) => {
        if (id !== current.current) return;
        setError(e.message);
      });

    await Promise.allSettled([fast, slow]);
    if (id === current.current) setWriting(false);
  }

  return (
    <>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void ask(question);
        }}
        className="mt-8"
      >
        <label htmlFor="q" className="block text-sm font-medium text-ink">
          What would you like to know?
        </label>
        <div className="mt-2 flex flex-col gap-2 sm:flex-row">
          <input
            id="q"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            maxLength={500}
            placeholder="e.g. Can I get a fee waiver for naturalization?"
            className="flex-1 rounded border border-rule bg-white px-3 py-2.5 text-ink outline-none placeholder:text-muted/60 focus:border-accent focus:ring-1 focus:ring-accent"
          />
          <button
            type="submit"
            disabled={writing || !question.trim()}
            className="rounded bg-accent px-5 py-2.5 font-medium text-paper transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {writing ? "Looking…" : "Ask"}
          </button>
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          {EXAMPLES.map((example) => (
            <button
              key={example}
              type="button"
              onClick={() => {
                setQuestion(example);
                void ask(example);
              }}
              className="rounded-full border border-rule px-3 py-1 text-xs text-muted transition-colors hover:border-accent hover:text-accent"
            >
              {example}
            </button>
          ))}
        </div>
      </form>

      {asked && (
        <section className="mt-10 border-t border-rule pt-8">
          <h2 className="text-sm font-medium tracking-wide text-muted uppercase">Answer</h2>

          {result ? (
            <div className="mt-3">
              <Answer result={result} sources={sources ?? []} />
            </div>
          ) : error ? (
            <p className="mt-3 rounded border border-flag/30 bg-flag/5 p-3 text-sm text-flag">
              {error}
            </p>
          ) : (
            // Shown alongside passages that have usually already arrived, so this is a status
            // line rather than a blocking spinner — the page is useful before it disappears.
            <p className="mt-3 flex items-center gap-2 text-sm text-muted">
              <span className="h-3 w-3 animate-spin rounded-full border-2 border-rule border-t-accent" />
              Reading the sources below and writing an answer. This takes about ten seconds.
            </p>
          )}

          <h2 className="mt-10 text-sm font-medium tracking-wide text-muted uppercase">
            Sources
          </h2>
          <p className="mt-1 text-sm text-muted">
            The passages the answer was written from, most relevant first. Each is widened to the
            text either side of the match so nothing reads as a fragment.
          </p>

          {sources ? (
            <Sources sources={sources} cited={result?.cited ?? []} />
          ) : (
            <p className="mt-4 text-sm text-muted">Searching…</p>
          )}
        </section>
      )}
    </>
  );
}
