// GWASHub web. In dev, /api is proxied to the local API (uvicorn on :8000) so the session cookie is same-origin.
export default defineNuxtConfig({
  compatibilityDate: '2026-10-01',
  ssr: false,
  devtools: { enabled: false },
  app: {
    head: {
      title: 'GWASHub',
      meta: [{ name: 'description', content: 'Upload, quality-control and meta-analyse GWAS summary statistics.' }],
      link: [
        { rel: 'preconnect', href: 'https://fonts.googleapis.com' },
        { rel: 'stylesheet', href: 'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap' },
      ],
    },
  },
  css: ['primeicons/primeicons.css', '~/assets/main.css'],
  modules: ['@primevue/nuxt-module'],
  primevue: {
    importTheme: { from: '~/theme.ts' },
  },
  nitro: {
    devProxy: {
      '/api': { target: `${process.env.GWASHUB_API ?? 'http://127.0.0.1:8000'}/api`, changeOrigin: true },
    },
  },
})
