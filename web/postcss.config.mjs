// Tailwind v4 is a PostCSS plugin and nothing else — no `tailwind.config.js`, no `content`
// globs to keep in sync. Theme values live in `app/globals.css` under `@theme`.
export default {
  plugins: { "@tailwindcss/postcss": {} },
};
