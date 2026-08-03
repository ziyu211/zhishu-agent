import { build } from 'vite'
await build({
  configFile: 'vite.config.ts',
  logLevel: 'error',
  ssr: { noExternal: ['naive-ui', 'vueuc', 'seemly', '@css-render/vue3-ssr', 'css-render', 'treemate', 'vdirs', 'vooks', 'evtd', '@juggle/resize-observer', 'date-fns', 'lodash-es'] },
  build: {
    ssr: 'scripts/render-check.ts',
    outDir: 'scripts/.render-out',
    emptyOutDir: true,
    minify: false,
    rollupOptions: { output: { entryFileNames: 'render-check.mjs' } },
  },
})
console.log('BUILD_DONE')
