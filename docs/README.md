# DuckLess docs

Source of https://tosun-si.github.io/duckless/, built with [Astro Starlight](https://starlight.astro.build/).

```bash
cd docs
npm ci
npm run dev       # http://localhost:4321/duckless/
npm run build     # static site in dist/
```

Pages live in `src/content/docs/`; the sidebar is defined in `astro.config.mjs`.
`.github/workflows/docs.yml` builds the site on pull requests that touch `docs/` and
deploys it to GitHub Pages from `main`.
