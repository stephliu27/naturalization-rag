// The client half of the two-endpoint split. Types here mirror what `api/main.py` returns and
// are the one place a field name is written down twice — unavoidable across a language boundary,
// and the reason `/config` exists so that at least the *values* are never duplicated.

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") ?? "http://localhost:8000";

export type Source = {
  label: string;
  citation: string;
  section: string;
  text: string;
  id: string;
  source_id: string;
  source_type: "uscis" | "caselaw";
  url: string;
  score: number;
};

export type Result = {
  answer: string | null;
  degraded: string | null;
  degraded_category: string | null;
  cited: string[];
  unknown: string[];
  uncited: string[];
  malformed: string[];
  citations: number;
  truncated: boolean;
  usage: Record<string, number>;
};

export type SearchResponse = { question: string; sources: Source[]; k: number };
export type AnswerResponse = {
  question: string;
  sources: Source[];
  result: Result;
  generation_remaining: number;
};

// The backend is bounded at 200 seconds by the ingress; the browser is given a little more so
// that a server-side deadline surfaces as a *degraded answer with passages* rather than as a
// client-side abort with nothing. Without any timeout at all a hung fetch spins forever, since
// `fetch` has no default one.
const ANSWER_TIMEOUT_MS = 230_000;
const SEARCH_TIMEOUT_MS = 20_000;

async function post<T>(path: string, question: string, timeoutMs: number): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
      signal: controller.signal,
    });
    if (!response.ok) {
      // 503 is the index failing to load, which is the one server fault that is not a
      // degradation; 422 is the length or emptiness check in the request model.
      const detail = await response.json().catch(() => null);
      throw new Error(detail?.detail ?? `request failed (${response.status})`);
    }
    return (await response.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

export const search = (question: string) =>
  post<SearchResponse>("/search", question, SEARCH_TIMEOUT_MS);

export const answer = (question: string) =>
  post<AnswerResponse>("/answer", question, ANSWER_TIMEOUT_MS);

/** Fire-and-forget warm-up. Costs nothing and fires from the visitor's own browser at the
 *  moment warmth is about to matter — the layer that hides a cold start if the replica count
 *  is ever set back to zero. Failure is ignored on purpose: it is a hint, not a dependency. */
export function warm(): void {
  fetch(`${API_BASE}/health`).catch(() => {});
}
