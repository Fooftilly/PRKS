import type { OutputAsset, OutputBundle } from 'rolldown'
import type { Plugin } from 'vite'

function cssText(asset: OutputAsset): string {
  const source = asset.source
  return typeof source === 'string' ? source : new TextDecoder().decode(source)
}

export function inlineExtractedCss(bundle: OutputBundle): void {
  const cssNames = Object.keys(bundle)
    .filter((name) => {
      const item = bundle[name]
      return item.type === 'asset' && name.endsWith('.css')
    })
    .sort()
  if (cssNames.length === 0) return

  const css = cssNames.map((name) => cssText(bundle[name] as OutputAsset)).join('\n')
  for (const name of cssNames) {
    delete bundle[name]
  }
  if (!css.trim()) return

  const injector =
    `(()=>{if(document.querySelector("style[data-prks-vue-css]"))return;` +
    `const s=document.createElement("style");` +
    `s.setAttribute("data-prks-vue-css","");` +
    `s.textContent=${JSON.stringify(css)};` +
    `(document.head||document.documentElement).appendChild(s);})();\n`

  let injected = false
  for (const item of Object.values(bundle)) {
    if (item.type === 'chunk' && item.isEntry) {
      item.code = injector + item.code
      injected = true
    }
  }
  if (!injected) {
    throw new Error('prks-inline-css: extracted CSS but frontend/vue has no JS entry chunk')
  }
}

export function inlineCssIntoJs(): Plugin {
  return {
    name: 'prks-inline-css',
    apply: 'build',
    enforce: 'post',
    generateBundle(_options, bundle) {
      inlineExtractedCss(bundle)
    },
  }
}
