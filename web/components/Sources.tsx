"use client";

import type { Source } from "@/lib/api";

const KIND: Record<string, string> = {
  uscis: "USCIS Policy Manual",
  caselaw: "Federal court opinion",
};

/** Corpus text made readable without being altered.
 *
 *  The processed corpus keeps its `##` section markers, which are structure the chunker relies
 *  on and noise to a reader — shown literally they look like a rendering bug. The marker is
 *  dropped and the line it was on is set in bold, which is what it always meant. Nothing else
 *  is touched: no reflowing, no smart quotes, no ellipsis for length. A statute tidied up is a
 *  statute misquoted. */
function lines(text: string) {
  return text.split("\n").map((line, i) => {
    const heading = line.match(/^(#{2,4})\s+(.*)$/);
    if (heading) {
      return (
        <span key={i} className="mt-3 block font-semibold text-ink first:mt-0">
          {heading[2]}
        </span>
      );
    }
    return <span key={i}>{line + "\n"}</span>;
  });
}

export function Sources({ sources, cited }: { sources: Source[]; cited: string[] }) {
  const used = new Set(cited);
  return (
    <ol className="mt-4 space-y-3">
      {sources.map((source) => {
        const wasCited = used.has(source.label);
        return (
          <li key={source.id} id={source.label}>
            {/* A native <details> rather than a state-driven accordion: it is keyboard
                accessible and findable by the browser's own in-page search without any work,
                and `open` on the cited ones needs no JavaScript at all. */}
            <details
              open={wasCited}
              className={`group rounded border p-3 transition-colors ${
                wasCited ? "border-accent/35 bg-accent-soft/40" : "border-rule bg-white/50"
              }`}
            >
              <summary className="cursor-pointer list-none">
                <span className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                  <span
                    className={`rounded px-1.5 py-0.5 text-xs font-semibold ${
                      wasCited ? "bg-accent text-paper" : "bg-rule text-muted"
                    }`}
                  >
                    {source.label}
                  </span>
                  <span className="font-medium text-ink">{source.citation}</span>
                  {wasCited && (
                    <span className="text-xs font-medium text-accent">cited above</span>
                  )}
                </span>
                <span className="mt-1 block text-xs text-muted">
                  {[KIND[source.source_type] ?? source.source_type, source.section]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </summary>

              <div className="passage mt-3 border-t border-rule pt-3 font-serif text-[0.95rem] leading-relaxed text-ink/90">
                {lines(source.text)}
              </div>

              {source.url && (
                <a
                  href={source.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-3 inline-block text-sm text-accent underline underline-offset-2"
                >
                  Read the original document →
                </a>
              )}
            </details>
          </li>
        );
      })}
    </ol>
  );
}
