import Link from "next/link";

import { Ask } from "@/components/Ask";
import { corpus, generation } from "@/lib/evals";

// A server component, so the corpus counts are read off disk at build time and shipped as
// static HTML. The interactive half is one client island below it.
export default function Home() {
  const { documents, uscis, caselaw } = corpus();
  const { fabricated, citations, answers } = generation();

  return (
    <>
      <h1 className="mt-10 font-serif text-3xl leading-tight font-semibold sm:text-4xl">
        Straight answers about becoming a US citizen, with the source attached.
      </h1>
      <p className="mt-4 text-lg text-muted">
        Ask a question about the US naturalization process. The answer is written only from{" "}
        {documents} official documents: {uscis} chapters of the USCIS Policy Manual and{" "}
        {caselaw} federal court opinions. Every claim links to the exact passage behind it, so
        you can read it yourself.
      </p>

      {/* The trust claim in a register a non-lawyer can read. It is the same fact the evaluation
          page states with numbers; leading with the numbers here would speak to the wrong
          reader, and omitting the claim entirely would waste the strongest thing the tool can
          honestly say about itself. */}
      <p className="mt-4 rounded border border-rule bg-white/60 p-3 text-sm text-muted">
        <strong className="font-medium text-ink">
          All claims are backed by direct textual evidence.
        </strong>{" "}
        Across {answers} answers written during testing, all {citations} citations pointed at a
        real passage and{" "}
        {fabricated === 0 ? "none were invented" : `${fabricated} were invented`}.{" "}
        <Link href="/evaluation" className="text-accent underline underline-offset-2">
          See how that was measured
        </Link>
        .
      </p>

      <p className="mt-3 rounded border border-flag/25 bg-flag/5 p-3 text-sm text-ink">
        <strong className="font-medium">Please don&rsquo;t enter personal information.</strong>{" "}
        Your question is sent to a third-party model (Google Gemini) to write the answer. Leave
        out names, case numbers, A-numbers, and details about your own situation.
      </p>

      <Ask />
    </>
  );
}
