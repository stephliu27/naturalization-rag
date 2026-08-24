import path from "node:path";

import type { NextConfig } from "next";

const config: NextConfig = {
  // The evaluation page reads `../data/eval/` at build time. Telling Next the workspace really
  // starts a level up stops it inferring a root from the nearest lockfile, which here would be
  // `web/` and would exclude the very files the build needs. Must be absolute — Next 16 warns
  // and rewrites a relative value rather than honoring it.
  outputFileTracingRoot: path.join(import.meta.dirname, ".."),
};

export default config;
