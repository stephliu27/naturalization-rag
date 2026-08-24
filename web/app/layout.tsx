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
          <nav className="flex gap-4 text-sm">
            <Link href="/evaluation" className="text-muted hover:text-accent">
              How it was tested
            </Link>
            <a
              href="https://github.com/stephliu27/naturalization-rag"
              target="_blank"
              rel="noopener noreferrer"
              className="text-muted hover:text-accent"
            >
              Source
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
            answer is only as current as the document it cites — always check the original before
            relying on it.
          </p>
        </footer>
      </body>
    </html>
  );
}
