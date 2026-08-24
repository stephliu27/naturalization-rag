// Every number the site states, derived at build time from the same committed files the CLI
// wrote. Nothing here is typed in by hand, which is the rule `app.py`'s sidebar already follows:
// a number pasted into a UI drifts from the file it came from the first time the eval re-runs.
//
// Build time rather than request time, because these are committed artifacts. Re-running the
// eval and pushing re-deploys the numbers; nothing else can change them, so there is no reason
// to pay a network round trip for a value that is fixed at the commit.
//
// **Vercel needs one setting for this to work.** The evaluation data lives at `../data/eval`,
// outside the `web/` root directory, and Vercel uploads only the root directory unless
// "Include source files outside of the Root Directory" is enabled. Every read below therefore
// throws rather than defaulting, so that a misconfigured project fails the build loudly instead
// of deploying a page of zeroes that looks measured and is not.

import fs from "node:fs";
import path from "node:path";

const REPO = path.join(process.cwd(), "..");
const EVAL_DIR = path.join(REPO, "data", "eval");

function readJson<T>(file: string): T {
  if (!fs.existsSync(file)) {
    throw new Error(
      `Cannot read ${file}. If this is a Vercel build, enable "Include source files outside ` +
        `of the Root Directory in the Build Step" in Project Settings > Build.`,
    );
  }
  return JSON.parse(fs.readFileSync(file, "utf8")) as T;
}

/** `TOP_K` and `THINKING_LEVEL` read out of the Python that defines them.
 *
 *  Restating them here as TypeScript constants would be the exact drift this file exists to
 *  prevent: the grid was scored at four configurations, and naming the wrong one would make the
 *  page report a score for a setting the API is not running. Reading them from source is six
 *  lines and cannot go stale, and because it happens at build time a rename fails the build. */
function pythonConstant(file: string, name: string): string {
  const source = fs.readFileSync(path.join(REPO, "scripts", file), "utf8");
  const match = source.match(new RegExp(`^${name}\\s*=\\s*"?([^"\\n#]+)"?`, "m"));
  if (!match) throw new Error(`${name} not found in scripts/${file}`);
  return match[1].trim();
}

export const TOP_K = Number(pythonConstant("query.py", "TOP_K"));
export const THINKING = pythonConstant("generate.py", "THINKING_LEVEL");
export const MODEL = pythonConstant("generate.py", "GEMINI_MODEL");

type RetrievalRow = {
  recall: Record<string, number>;
  hit: boolean;
  anchors: number;
  anchors_in_window: number;
};

export function retrieval() {
  const rows = readJson<RetrievalRow[]>(path.join(EVAL_DIR, "results.json"));
  // JSON object keys are strings, so the integer cutoffs the eval wrote come back as "8".
  // Indexing with the number itself yields undefined and quietly sums to NaN.
  const k = String(TOP_K);
  const n = rows.length;
  return {
    questions: n,
    k: TOP_K,
    recall: rows.reduce((a, r) => a + r.recall[k], 0) / n,
    foundAny: rows.filter((r) => r.hit).length,
    foundAll: rows.filter((r) => r.recall[k] === 1).length,
    anchors: rows.reduce((a, r) => a + r.anchors, 0),
    anchorsInWindow: rows.reduce((a, r) => a + r.anchors_in_window, 0),
    // The full recall curve, which is the one place the retrieval story is more interesting
    // than a single number: it is flat past k=8, which is why 8 is the default and 10 is not.
    curve: Object.keys(rows[0].recall)
      .map(Number)
      .sort((a, b) => a - b)
      .map((cut) => ({
        k: cut,
        recall: rows.reduce((a, r) => a + r.recall[String(cut)], 0) / n,
      })),
  };
}

type GridFile = {
  config: { k: number; thinking: string; model: string; run: number };
  questions: { answer?: string | null; citations?: number; unknown?: string[]; malformed?: string[] }[];
  totals: Record<string, number>;
};

export function generation() {
  const files = fs
    .readdirSync(path.join(EVAL_DIR, "generation"))
    .filter((f) => f.endsWith(".json"))
    .sort();
  if (files.length === 0) throw new Error(`no grid files in ${EVAL_DIR}/generation`);

  let answers = 0;
  let citations = 0;
  let fabricated = 0;
  let malformed = 0;
  let shipping: GridFile | null = null;
  const cells: { k: number; thinking: string; totals: Record<string, number> }[] = [];

  for (const file of files) {
    const grid = readJson<GridFile>(path.join(EVAL_DIR, "generation", file));
    // Every answer in the grid, probes included. The fabrication count is the claim that covers
    // the whole measurement, so it must not quietly exclude a question.
    for (const q of grid.questions) {
      if (q.answer) answers += 1;
      citations += q.citations ?? 0;
      fabricated += q.unknown?.length ?? 0;
      malformed += q.malformed?.length ?? 0;
    }
    if (grid.config.run === 1) {
      cells.push({ k: grid.config.k, thinking: grid.config.thinking, totals: grid.totals });
    }
    // Selected by config, never by filename, so changing a default moves the reported numbers
    // instead of quietly reporting the old ones.
    if (!shipping && grid.config.k === TOP_K && grid.config.thinking === THINKING) {
      shipping = grid;
    }
  }

  return { answers, citations, fabricated, malformed, cells, shipping, files: files.length };
}

export function corpus() {
  const counts = { uscis: 0, caselaw: 0, chars: 0 };
  for (const half of ["uscis", "caselaw"] as const) {
    const dir = path.join(REPO, "data", "processed", half);
    for (const file of fs.readdirSync(dir)) {
      // The `_metadata.json` sidecar beside every document is not corpus text.
      if (!file.endsWith(".txt")) continue;
      counts[half] += 1;
      // Decoded length, not `statSync().size`. The corpus keeps curly quotes, em-dashes and
      // section symbols, all of which are multi-byte in UTF-8, so byte size overstates the
      // headline character count — and that count is the one the resume line quotes.
      counts.chars += fs.readFileSync(path.join(dir, file), "utf8").length;
    }
  }
  return { ...counts, documents: counts.uscis + counts.caselaw };
}
