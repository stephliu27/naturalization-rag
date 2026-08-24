import type { Result, Source } from "@/lib/api";

// Only a bracket whose entire content is an S-label. That is what keeps this off `detail[ed]`
// and `"[s]ole procedure"` — the legal convention for altering a letter inside a quotation,
// which the model writes because the corpus does. The same rule the Python citation check uses.
const LABEL = /\[(S\d+)\]/g;

/** The answer with every `[S1]` turned into a link to the source it names.
 *
 *  The label stays visible rather than being replaced by the citation, and the reason is that
 *  **a citation cannot identify a passage**: reporter page numbers were dropped on purpose, so
 *  a citation is document-level, and five of the eight sources on a fee-waiver question all
 *  render as *Northwest Immigrant Rights Project v. USCIS (D.D.C. 2020)*. Swapping the label
 *  for that string would make five distinct passages indistinguishable and print the same name
 *  twice in one sentence. The label is the only chunk-level handle the answer has.
 *
 *  Built by splitting the string and emitting React nodes rather than by producing HTML, so
 *  there is no `dangerouslySetInnerHTML` anywhere and model output can never be markup. */
function withCitations(text: string, byLabel: Map<string, Source>) {
  const nodes: React.ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(LABEL)) {
    const at = match.index!;
    if (at > last) nodes.push(text.slice(last, at));
    const source = byLabel.get(match[1]);
    nodes.push(
      source ? (
        <a
          key={`${at}-${match[1]}`}
          href={`#${source.label}`}
          title={source.citation}
          className="mx-px rounded-sm bg-accent-soft px-1 align-baseline text-[0.78em] font-medium text-accent no-underline hover:bg-accent hover:text-paper"
        >
          {match[1]}
        </a>
      ) : (
        // An unknown label is a fabricated citation, reported as an error below. It stays inert
        // text rather than linking somewhere plausible.
        <span key={`${at}-${match[1]}`} className="font-medium text-flag">
          {match[0]}
        </span>
      ),
    );
    last = at + match[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

export function Answer({ result, sources }: { result: Result; sources: Source[] }) {
  const byLabel = new Map(sources.map((s) => [s.label, s]));

  // Degradation is the normal path on a free tier, not an error page: the passages below are
  // still the useful part, so this says why the prose is missing and gets out of the way.
  if (!result.answer) {
    return (
      <div className="rounded border border-rule bg-white/60 p-4 text-sm text-muted">
        <p className="font-medium text-ink">
          {result.degraded_category === "cap_reached"
            ? "No written answer — the demo's daily limit for generated answers is used up."
            : result.degraded_category === "no_key"
              ? "No written answer — this deployment has no model configured."
              : "No written answer right now."}
        </p>
        <p className="mt-1">
          The sources below are the same passages an answer would have been written from, and
          they are what actually holds the information. {result.degraded}
        </p>
      </div>
    );
  }

  return (
    <div>
      <div className="font-serif text-[1.0625rem] leading-[1.75] text-ink">
        {result.answer.split(/\n{2,}/).map((paragraph, i) => (
          <p key={i} className={i > 0 ? "mt-4" : undefined}>
            {withCitations(paragraph, byLabel)}
          </p>
        ))}
      </div>

      {/* The mechanical checks, surfaced rather than logged. A fabricated label is the failure
          this project claims not to have, so it gets the loudest treatment available. */}
      {result.unknown.length > 0 && (
        <p className="mt-4 rounded border border-flag/30 bg-flag/5 p-3 text-sm text-flag">
          Fabricated citation: {result.unknown.join(", ")} — the answer cited a source that was
          never supplied.
        </p>
      )}
      {result.truncated && (
        <p className="mt-4 text-sm text-muted">
          The answer reached its length ceiling, so its last citation may be cut off.
        </p>
      )}

      <p className="mt-5 border-t border-rule pt-3 text-sm text-muted">
        Every sentence above is drawn from the numbered sources below —{" "}
        <strong className="font-medium text-ink">
          {result.cited.length} of {sources.length}
        </strong>{" "}
        of them were used. Follow a number to read the passage it came from.
      </p>
    </div>
  );
}
