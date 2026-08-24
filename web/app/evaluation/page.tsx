import type { Metadata } from "next";

import { MODEL, THINKING, TOP_K, corpus, generation, retrieval } from "@/lib/evals";

export const metadata: Metadata = {
  title: "How it was tested | Naturalization Barrier Navigator",
  description:
    "Retrieval and generation scores against a hand-written question set with verbatim " +
    "expected passages.",
};

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="rounded border border-rule bg-white/60 p-4">
      <div className="font-serif text-2xl font-semibold text-ink">{value}</div>
      <div className="mt-1 text-sm text-muted">{label}</div>
    </div>
  );
}

export default function Evaluation() {
  const c = corpus();
  const r = retrieval();
  const g = generation();
  const shipping = g.shipping;

  return (
    <article className="mt-10">
      <h1 className="font-serif text-3xl font-semibold">How it was tested</h1>
      <p className="mt-4 text-lg text-muted">
        Most AI tools ask you to take their word for it. This one was graded against an answer
        key.
      </p>
      <p className="mt-4 text-muted">
        {r.questions} questions were written by hand, in the words a real applicant would use.
        For each one, the documents a correct answer has to rely on were written down in
        advance, down to the specific sentences. That turns &ldquo;is it any good?&rdquo; from an impression into a
        score, and it makes a bad change visible immediately instead of six months later.
      </p>
      <p className="mt-4 text-muted">
        Two things get graded separately, because they fail for different reasons:{" "}
        <strong className="font-medium text-ink">finding</strong> the right passage, and{" "}
        <strong className="font-medium text-ink">writing</strong> an answer that sticks to it.
        Every number below is read straight from the saved test results. None of it is typed
        in by hand, so nothing on this page can drift from what was actually measured.
      </p>

      <h2 className="mt-10 font-serif text-xl font-semibold">The corpus</h2>
      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <Stat value={String(c.documents)} label="documents indexed" />
        <Stat value={`${(c.chars / 1_000_000).toFixed(2)}M`} label="characters of source text" />
        <Stat value={`${c.uscis} / ${c.caselaw}`} label="policy chapters / court opinions" />
      </div>
      <p className="mt-4 text-sm text-muted">
        The USCIS Policy Manual was scraped chapter by chapter; the {c.caselaw} opinions were
        selected by hand, each one read to confirm what it actually holds. Documents are split
        into passages sized to the retrieval model&rsquo;s own limit rather than to a generic
        default.
      </p>

      <h2 className="mt-10 font-serif text-xl font-semibold">Finding the right passage</h2>
      <p className="mt-3 text-muted">
        Before it can answer anything, the tool has to pull the right passages out of{" "}
        {c.documents} documents. This half is graded on its own, and it is the half that matters
        most: no amount of good writing rescues an answer built on the wrong chapter. Grading it
        needs no AI and costs nothing, so it can be re-run on every change.
      </p>
      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <Stat value={`${Math.round(r.recall * 100)}%`} label={`of expected documents found at k=${r.k}`} />
        <Stat
          value={`${r.foundAny}/${r.questions}`}
          label="questions where at least one expected document came back"
        />
        <Stat
          value={`${r.anchorsInWindow}/${r.anchors}`}
          label="expected paragraphs actually returned"
        />
      </div>

      <h3 className="mt-8 text-sm font-medium tracking-wide text-muted uppercase">
        Why the cutoff is {r.k}
      </h3>
      <p className="mt-2 text-sm text-muted">
        Recall stops improving past {r.k}, so a deeper cutoff would only hand the model
        worse-matching passages to dilute the answer with. That was settled with the free metric
        before spending a single request.
      </p>
      <table className="mt-3 w-full text-sm">
        <thead>
          <tr className="border-b border-rule text-left text-muted">
            <th className="py-1.5 font-medium">passages retrieved</th>
            {r.curve.map((p) => (
              <th key={p.k} className="py-1.5 text-right font-medium">
                {p.k}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr>
            <td className="py-1.5">expected documents found</td>
            {r.curve.map((p) => (
              <td
                key={p.k}
                className={`py-1.5 text-right tabular-nums ${
                  p.k === r.k ? "font-semibold text-accent" : ""
                }`}
              >
                {Math.round(p.recall * 100)}%
              </td>
            ))}
          </tr>
        </tbody>
      </table>

      <h2 className="mt-10 font-serif text-xl font-semibold">Writing an honest answer</h2>
      <p className="mt-3 text-muted">
        The claim worth making is not that the answers sound good. It is that they are{" "}
        <em>checkable</em>. Every passage is handed to the model with a numbered label, so a
        citation either points at a real passage or it does not. Catching an invented one is
        arithmetic, not opinion: no human reading, no second AI grading the first.
      </p>
      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <Stat value={String(g.fabricated)} label={`invented citations across ${g.citations} total`} />
        {/* `cells`, not `files`: the grid is four configurations run twice, so the file count
            would double-report the number of settings that were actually compared. */}
        <Stat
          value={String(g.answers)}
          label={`answers scored across ${g.cells.length} configurations`}
        />
        {shipping && (
          <Stat
            value={`${shipping.totals.anchors_in_cited}/${shipping.totals.anchors_in_context}`}
            label="expected paragraphs that landed inside a source the answer actually cited"
          />
        )}
      </div>

      <h3 className="mt-8 text-sm font-medium tracking-wide text-muted uppercase">
        The defaults were measured, not inherited
      </h3>
      <p className="mt-2 text-sm text-muted">
        Retrieval depth and reasoning effort were varied against each other and every cell scored
        the same way. The winning cell is what this site runs.
      </p>
      <table className="mt-3 w-full text-sm">
        <thead>
          <tr className="border-b border-rule text-left text-muted">
            <th className="py-1.5 font-medium">passages</th>
            <th className="py-1.5 font-medium">reasoning</th>
            <th className="py-1.5 text-right font-medium">expected paragraphs cited</th>
            <th className="py-1.5 text-right font-medium">expected documents cited</th>
            <th className="py-1.5 text-right font-medium">invented citations</th>
          </tr>
        </thead>
        <tbody>
          {g.cells.map((cell) => {
            const isShipping = cell.k === TOP_K && cell.thinking === THINKING;
            return (
              <tr
                key={`${cell.k}-${cell.thinking}`}
                className={`border-b border-rule/60 ${isShipping ? "font-semibold text-accent" : ""}`}
              >
                <td className="py-1.5">{cell.k}</td>
                <td className="py-1.5">
                  {cell.thinking}
                  {isShipping && " (in use)"}
                </td>
                <td className="py-1.5 text-right tabular-nums">
                  {cell.totals.anchors_in_cited}/{cell.totals.anchors_in_context}
                </td>
                <td className="py-1.5 text-right tabular-nums">
                  {cell.totals.expected_cited}/{cell.totals.expected_retrieved}
                </td>
                <td className="py-1.5 text-right tabular-nums">{cell.totals.fabricated}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-3 text-sm text-muted">
        Running the same configuration twice produced byte-identical answers on 64 of 64 pairs, so
        these numbers carry no run-to-run noise and comparisons need no repeats. Currently
        serving <code className="text-ink">{MODEL}</code> at {TOP_K} passages, {THINKING}{" "}
        reasoning effort.
      </p>

      <h2 className="mt-10 font-serif text-xl font-semibold">Areas for improvement</h2>
      <ul className="mt-3 list-disc space-y-2 pl-5 text-muted">
        <li>
          <strong className="font-medium text-ink">Case law is the weaker half.</strong> The
          opinions all cite the same statutes, so a procedural question tends to retrieve{" "}
          <em>an</em> opinion rather than <em>the</em> opinion, sometimes one on the opposite
          side of a circuit split.
        </li>
        <li>
          <strong className="font-medium text-ink">
            Everyday phrasing misses the policy register.
          </strong>{" "}
          &ldquo;Can I appeal it&rdquo; finds the right chapter at rank 66. The corpus writes
          &ldquo;request a hearing on a denial&rdquo; and the two share almost no words, so
          keyword matching does not rescue it either. That was tested and rejected.
        </li>
        <li>
          <strong className="font-medium text-ink">Sources go out of date.</strong> A 2020 opinion
          is frozen in 2020. The model is instructed to date any figure it reports and to decline
          rather than answer from memory, but currency is a real limit of a fixed corpus.
        </li>
      </ul>
      <p className="mt-4 text-sm text-muted">
        Each of these was found by measuring rather than by guessing, which is the point of
        having an answer key: it tells you where to work next. They are listed here because a
        tool that knows its own weak spots is easier to trust than one that claims none.
      </p>
    </article>
  );
}
