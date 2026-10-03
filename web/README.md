# GWASHub web

Nuxt 4 single-page app (PrimeVue 4, HERMES blue theme in `app/theme.ts`). In development, `/api` is
proxied to the local API on port 8000 (see `nuxt.config.ts`), so the session cookie is same-origin.

```bash
npm install
npm run dev        # http://localhost:3000 (start the API first, or use ../devtools/dev.sh)
npm run generate   # static site in .output/public (for S3 + CloudFront)
```

PrimeVue is pinned to v4 on purpose: v5+ is commercially licensed and needs a key.

Pages: `/` · `/about` · `/login` · `/projects` · `/projects/:pid` (overview) · `/projects/:pid/datasets`
(viewer; a row opens the dataset panel with QC and Details tabs) · `/projects/:pid/upload` ·
`/projects/:pid/meta` and `/meta/:id` · `/projects/:pid/pipeline` (pipeline settings and upload options).
