import { describe, expect, it } from 'vitest'
import type { OutputBundle } from 'rolldown'
import { inlineExtractedCss } from './inline-css-plugin'

function bundle(entries: Record<string, { type: string; isEntry?: boolean; code?: string; source?: string }>): OutputBundle {
  return entries as unknown as OutputBundle
}

describe('inlineExtractedCss', () => {
  it('prepends extracted CSS to the JS entry and drops the stylesheet', () => {
    const output = bundle({
      'b.css': { type: 'asset', source: 'b{}' },
      'a.css': { type: 'asset', source: 'a{}' },
      'prks-vue.js': { type: 'chunk', isEntry: true, code: 'entry();' },
    })
    inlineExtractedCss(output)
    expect(output['a.css']).toBeUndefined()
    expect(output['b.css']).toBeUndefined()
    const chunk = output['prks-vue.js']
    expect(chunk.type).toBe('chunk')
    if (chunk.type !== 'chunk') return
    expect(chunk.code.startsWith('(()=>')).toBe(true)
    expect(chunk.code).toContain('data-prks-vue-css')
    expect(chunk.code).toContain(JSON.stringify('a{}\nb{}'))
    expect(chunk.code.endsWith('entry();')).toBe(true)
  })

  it('leaves a bundle without CSS unchanged', () => {
    const output = bundle({
      'prks-vue.js': { type: 'chunk', isEntry: true, code: 'entry();' },
    })
    inlineExtractedCss(output)
    const chunk = output['prks-vue.js']
    expect(chunk.type).toBe('chunk')
    if (chunk.type !== 'chunk') return
    expect(chunk.code).toBe('entry();')
  })

  it('fails the build when CSS has no JS entry to carry it', () => {
    const output = bundle({
      'prks-vue.css': { type: 'asset', source: 'body{}' },
    })
    expect(() => inlineExtractedCss(output)).toThrow(/no JS entry chunk/)
  })
})
