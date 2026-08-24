import type { Result, Source } from "@/lib/api";

// The model emits a small, measured amount of Markdown. Across the 128 recorded evaluation
// answers, 24 contain any at all and only three constructs ever appear: `*` bullets (52
// occurrences), `**bold**` (36) and `*italic*` (2, both of them case names, which is the legal
// convention for a citation). No headings, tables, code spans or numbered lists.
//
// So this renders those three and nothing else, rather than pulling in a Markdown library. Two
// reasons beyond weight: the output is parsed into React nodes rather than HTML, so model output
// can never become markup and there is no `dangerouslySetInnerHTML` anywhere; and an unsupported
// construct degrades to its literal characters, which is visible and reportable rather than
// silently mangled. If the model starts emitting tables, this is the place that needs to know.

// One alternation, and the order inside it is load-bearing: `**bold**` must be tried before
// `*italic*`, or the italic branch matches the first two asterisks of every bold run.
//
// The `[Sn]` branch only matches a bracket whose entire content is an S-label. That is what keeps
// it off `detail[ed]` and `"[s]ole procedure"` — the legal convention for altering a letter
// inside a quotation, which the model writes because the corpus does. Same rule the Python
// citation check uses.
const INLINE = /\*\*([^*\n]+)\*\*|\*([^*\n]+)\*|\[(S\d+)\]/g;

function inline(text: string, byLabel: Map<string, Source>, keyPrefix: string) {
  const nodes: React.ReactNode[] = [];
  let last = 0;

  for (const m of text.matchAll(INLINE)) {
    const at = m.index!;
    if (at > last) nodes.push(text.slice(last, at));
    const key = `${keyPrefix}-${at}`;

    if (m[1] !== undefined) {
      nodes.push(
        <strong key={key} className="font-semibold">
          {m[1]}
        </strong>,
      );
    } else if (m[2] !== undefined) {
      nodes.push(<em key={key}>{m[2]}</em>);
    } else {
      const label = m[3];
      const source = byLabel.get(label);
      nodes.push(
        source ? (
          <a
            key={key}
            href={`#${source.label}`}
            title={source.citation}
            className="mx-px rounded-sm bg-accent-soft px-1 align-baseline text-[0.78em] font-medium text-accent no-underline hover:bg-accent hover:text-paper"
          >
            {label}
          </a>
        ) : (
          // An unknown label is a fabricated citation, reported as an error below. It stays
          // inert text rather than linking somewhere plausible.
          <span key={key} className="font-medium text-flag">
            [{label}]
          </span>
        ),
      );
    }
    last = at + m[0].length;
  }

  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

/** An answer split into a flat run of paragraphs and bullet lists.
 *
 *  A blank line starts a new block, but a block is **not** all-or-nothing: the model routinely
 *  writes a lead-in sentence and its bullets inside one block, with only single newlines
 *  between them —
 *
 *      There is disagreement among courts regarding ...:
 *      *   The majority of circuits hold that § 1429 ...
 *      *   The Third and Seventh Circuits have held ...
 *
 *  An earlier version required every line in a block to be a bullet before treating it as a
 *  list, so a block shaped like that fell through to a plain paragraph and rendered its
 *  asterisks literally. Lines are therefore grouped into runs, and each run of consecutive
 *  bullets becomes its own list.
 *
 *  Plain lines inside one block are joined with a space rather than preserved: a single newline
 *  in Markdown is a soft wrap, not a break. */
function blocks(answer: string) {
  const out: ({ type: "p"; text: string } | { type: "ul"; items: string[] })[] = [];

  for (const raw of answer.split(/\n{2,}/)) {
    const lines = raw.split("\n").map((l) => l.trim()).filter(Boolean);
    let para: string[] = [];
    let items: string[] = [];

    const flushPara = () => {
      if (para.length) out.push({ type: "p", text: para.join(" ") });
      para = [];
    };
    const flushList = () => {
      if (items.length) out.push({ type: "ul", items });
      items = [];
    };

    for (const line of lines) {
      // `*` is what the model actually emits — 52 occurrences across the recorded answers, always
      // as `*` plus three spaces. `-` is accepted too because it is the other conventional
      // Markdown bullet and costs nothing; the required trailing whitespace keeps it off an
      // em-dash or a negative number opening a line.
      if (/^[*-]\s+/.test(line)) {
        flushPara();
        items.push(line.replace(/^[*-]\s+/, ""));
      } else {
        flushList();
        para.push(line);
      }
    }
    flushList();
    flushPara();
  }

  return out;
}

/** The label stays visible rather than being replaced by the citation, because **a citation
 *  cannot identify a passage**: reporter page numbers were dropped on purpose, so a citation is
 *  document-level, and five of the eight sources on a fee-waiver question all render as
 *  *Northwest Immigrant Rights Project v. USCIS (D.D.C. 2020)*. Swapping the label for that
 *  string would make five distinct passages indistinguishable. The citation goes in the link
 *  title instead, where a browser shows it on hover. */
export function Answer({ result, sources }: { result: Result; sources: Source[] }) {
  const byLabel = new Map(sources.map((s) => [s.label, s]));

  // Degradation is the normal path on a free tier, not an error page: the passages below are
  // still the useful part, so this says why the prose is missing and gets out of the way.
  if (!result.answer) {
    return (
      <div className="rounded border border-rule bg-white/60 p-4 text-sm text-muted">
        <p className="font-medium text-ink">
          {result.degraded_category === "cap_reached"
            ? "No written answer. The demo's daily limit for generated answers is used up."
            : result.degraded_category === "no_key"
              ? "No written answer. This deployment has no model configured."
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
        {blocks(result.answer).map((block, i) =>
          block.type === "ul" ? (
            <ul key={i} className={`list-disc space-y-1.5 pl-5 ${i > 0 ? "mt-4" : ""}`}>
              {block.items.map((item, j) => (
                <li key={j}>{inline(item, byLabel, `${i}-${j}`)}</li>
              ))}
            </ul>
          ) : (
            <p key={i} className={i > 0 ? "mt-4" : undefined}>
              {inline(block.text, byLabel, String(i))}
            </p>
          ),
        )}
      </div>

      {/* The mechanical checks, surfaced rather than logged. A fabricated label is the failure
          this project claims not to have, so it gets the loudest treatment available. */}
      {result.unknown.length > 0 && (
        <p className="mt-4 rounded border border-flag/30 bg-flag/5 p-3 text-sm text-flag">
          Fabricated citation: {result.unknown.join(", ")}. The answer cited a source that was
          never supplied.
        </p>
      )}
      {result.truncated && (
        <p className="mt-4 text-sm text-muted">
          The answer reached its length ceiling, so its last citation may be cut off.
        </p>
      )}

      <p className="mt-5 border-t border-rule pt-3 text-sm text-muted">
        Every sentence above is drawn from the numbered sources below.{" "}
        <strong className="font-medium text-ink">
          {result.cited.length} of {sources.length}
        </strong>{" "}
        of them were used. Follow a number to read the passage it came from.
      </p>
    </div>
  );
}
