import type { Metadata } from "next";
import Link from "next/link";

import { corpus, generation } from "@/lib/evals";

export const metadata: Metadata = {
  title: "About | Naturalization Barrier Navigator",
  description:
    "Why this exists: research on naturalization law and its barriers, an asylum law seminar, " +
    "and the finding that missing information is itself a barrier.",
};

const LINKS = [
  {
    href: "https://www.linkedin.com/in/liu-stephanie/",
    label: "LinkedIn",
    // Brand marks are filled paths; the envelope below is a stroked outline. Both are
    // `aria-hidden` because the visible text beside each one already names the destination.
    path: "M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 1 1 0-4.125 2.062 2.062 0 0 1 0 4.125zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z",
  },
  {
    href: "https://github.com/stephliu27/naturalization-rag",
    label: "GitHub",
    path: "M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12",
  },
];

export default function About() {
  const c = corpus();
  const { fabricated, citations, answers } = generation();

  return (
    <article className="mt-10">
      <h1 className="font-serif text-3xl font-semibold">About this project</h1>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
        <span className="text-muted">by Stephanie Liu</span>
        {LINKS.map((link) => (
          <a
            key={link.label}
            href={link.href}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 text-accent underline decoration-accent/40 underline-offset-4 hover:decoration-accent"
          >
            <svg aria-hidden="true" viewBox="0 0 24 24" fill="currentColor" className="h-4 w-4 shrink-0">
              <path d={link.path} />
            </svg>
            {link.label}
          </a>
        ))}
        <a
          href="mailto:stepliu@stanford.edu"
          className="inline-flex items-center gap-1.5 text-accent underline decoration-accent/40 underline-offset-4 hover:decoration-accent"
        >
          <svg
            aria-hidden="true"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="h-4 w-4 shrink-0"
          >
            <rect x="2" y="4" width="20" height="16" rx="2" />
            <path d="m2 7 10 6 10-6" />
          </svg>
          Email
        </a>
      </div>

      <p className="mt-6 text-lg text-muted">
        For far too many prospective citizens, the hardest part of the US naturalization process
        isn&rsquo;t navigating the process itself, but decoding the requirements behind it.
      </p>

      <h2 className="mt-10 font-serif text-xl font-semibold">How I got here</h2>

      <p className="mt-4">
        I first worked on naturalization law through the UC Santa Barbara Research Mentorship
        Program, where I co-authored a paper with Janna Haider on how the courts acted as a venue
        for the production of race in the 1922 landmark Supreme Court case{" "}
        <em>Takao Ozawa v. United States</em>. Ozawa had lived in the United States for
        twenty years, graduated from the University of California, raised his children speaking
        only English, and attended an English-language church. The Supreme Court denied him
        citizenship anyway, ruling that &ldquo;free white persons&rdquo; in the Naturalization Act
        of 1790 meant Caucasian and that he did not qualify. The government conceded that he was
        assimilated and of good moral character, and it made no difference to the outcome. What
        struck me from reading the briefs was that Ozawa had to make his entire argument
        inside a framework built to exclude him, because that was the only argument the courts
        were willing to hear.
      </p>

      <p className="mt-4">
        I wanted to learn more about how naturalization operates in the status quo, so I completed
        my senior capstone project on how to bring equity to the US naturalization process. I
        looked at why the US naturalization rate has stayed between 40 and 50 percent for
        decades while Canada&rsquo;s has hovered near 80, using microdata from the 2023 American
        Community Survey along with a comparison of the two systems. Most of the literature points
        to three prohibitive barriers: the filing fee, the English proficiency test, and the
        civics exam. But a fourth one kept coming up, and it was the one nobody had really
        designed around. Far too many eligible applicants did not know what the process actually
        involved. A randomized study from Stanford&rsquo;s Immigration Policy Lab (Hainmueller et
        al., <em>PNAS</em>, 2018) found that simply informing low-income permanent residents that
        fee waivers existed raised their naturalization rate by 35 percent, which suggests the
        barrier in those cases was not the cost so much as the lack of information about it.
      </p>

      <p className="mt-4">
        I later took Professor Lisa Weissman-Ward&rsquo;s asylum law seminar,{" "}
        <a
          href="https://exploreintrosems.stanford.edu/opportunities/anatomy-asylum-law-who-gets-it-and-why"
          target="_blank"
          rel="noopener noreferrer"
          className="text-accent underline underline-offset-2"
        >
          LAWGEN 117Q: The Anatomy of Asylum Law
        </a>
        . I had the opportunity to speak with people who had been through the asylum process and
        to sit in on hearings at the San Francisco immigration court. Asylum barriers are not
        completely identical to naturalization, but the most significant one remained consistent:
        people didn&rsquo;t know a process existed, or whether they were eligible for it, or that a
        fee could be waived or a requirement excepted. None of that is really a legal problem. It
        is an information problem, and it is the kind of problem AI is genuinely well suited to. I
        built this to close that gap, so that understanding the process does not depend on already
        knowing someone who understands it.
      </p>

      <h2 className="mt-10 font-serif text-xl font-semibold">Why accuracy came first</h2>

      <p className="mt-4">
        The obvious risk with using AI for anything legal is that it will make things up. A
        language model will answer an immigration question confidently whether or not it actually
        knows, and here a confident wrong answer can do real damage: someone misses a filing
        window, gives up on an application they would have qualified for, or decides they are
        ineligible when they are not. Even a lack of information is better than misinformation, so
        I designed the Naturalization Barrier Navigator to prioritize accuracy above all else.
      </p>

      <p className="mt-4">
        This tool reads only from primary law that I scraped and cleaned myself: {c.uscis} chapters
        of the USCIS Policy Manual and {c.caselaw} federal court opinions, {c.documents} documents
        altogether. The model receives those passages and nothing else, and it is instructed to
        decline rather than fill a gap from memory. Every claim in an answer links to the passage
        it came from, so you can read the source yourself instead of taking the answer&rsquo;s word
        for it. And because a citation that merely looks plausible is worth nothing, I check them
        mechanically rather than by eye:{" "}
        <strong className="font-medium">
          across {answers} answers written during testing, all {citations} citations pointed at a
          real passage and{" "}
          {fabricated === 0 ? "none were invented" : `${fabricated} were invented`}
        </strong>
        .{" "}
        <Link href="/evaluation" className="text-accent underline underline-offset-2">
          See how that was measured
        </Link>
        .
      </p>

      <p className="mt-4">
        None of this replaces a lawyer, and it is not meant to. It answers the question that comes
        before a lawyer, which for a lot of people is the one that stops them from going any
        further: what does the rule actually say, and where can I read it myself?
      </p>
    </article>
  );
}
