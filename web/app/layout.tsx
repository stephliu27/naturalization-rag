import type { Metadata } from "next";
import Link from "next/link";

import "./globals.css";

export const metadata: Metadata = {
  title: "Naturalization Barrier Navigator",
  description:
    "Ask about the US naturalization process and get an answer written only from official " +
    "USCIS policy and federal court opinions, with every claim linked to its source.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="mx-auto flex min-h-dvh max-w-3xl flex-col px-5 py-8 sm:px-8">
        <header className="flex items-baseline justify-between gap-4">
          <Link href="/" className="font-serif text-lg font-semibold no-underline">
            Naturalization Barrier Navigator
          </Link>
          {/* Underlined and in the accent color rather than muted grey. Header links that are
              styled like plain text get read as labels and never clicked — and this is the only
              route to the page carrying the evaluation numbers. */}
          <nav className="flex items-center gap-3 text-sm">
            <Link
              href="/about"
              className="text-accent underline decoration-accent/40 underline-offset-4 hover:decoration-accent"
            >
              About
            </Link>
            <span aria-hidden="true" className="text-rule select-none">
              |
            </span>
            <Link
              href="/evaluation"
              className="text-accent underline decoration-accent/40 underline-offset-4 hover:decoration-accent"
            >
              How it was tested
            </Link>
            <span aria-hidden="true" className="text-rule select-none">
              |
            </span>
            <a
              href="https://github.com/stephliu27/naturalization-rag"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-accent underline decoration-accent/40 underline-offset-4 hover:decoration-accent"
            >
              {/* The GitHub mark rather than a generic external-link arrow. On this site the
                  word "Source" was ambiguous — every cited passage is a source — and a brand
                  logo says "you are leaving for GitHub" more directly than an arrow does.
                  `aria-hidden` because the visible text already names the destination. */}
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                fill="currentColor"
                className="h-4 w-4 shrink-0"
              >
                <path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12" />
              </svg>
              GitHub
            </a>
          </nav>
        </header>

        <main className="flex-1">{children}</main>

        <footer className="mt-16 border-t border-rule pt-5 text-xs leading-relaxed text-muted">
          <p>
            <strong className="font-semibold text-ink">
              Legal information, not legal advice.
            </strong>{" "}
            This tool surfaces and cites public policy text. It does not interpret anyone&rsquo;s
            circumstances, and it is not a substitute for an immigration attorney or an accredited
            representative.
          </p>
          <p className="mt-2">
            Sources are dated. Some state rules that were later changed or struck down, so an
            answer is only as current as the document it cites. Always check the original before
            relying on it.
          </p>
        </footer>
      </body>
    </html>
  );
}
