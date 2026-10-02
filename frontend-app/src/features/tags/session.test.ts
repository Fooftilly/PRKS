import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { readRouteSurface } from '../../route-surface/lifecycle'
import { dismissTags, presentTags, registerTagsBridge, resetTagsSessionForTests } from './session'

afterEach(() => {
  resetTagsSessionForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  delete window.prksVuePresentTags
  delete window.prksVueDismissTags
  delete window.prksVueCloseTagsAliasModal
  delete window.prksVueCloseTagsMergeModal
  delete window.prksIcon
  delete window.prksRefreshIcons
  delete window.fetchTags
})

function host(): HTMLElement {
  const el = document.createElement('div')
  document.body.appendChild(el)
  return el
}

function owner() {
  return {
    isCurrent: () => true,
    lastResolvedRoute: { name: 'tags' },
  }
}

const ALPHA = {
  id: 't1',
  name: 'Alpha',
  color: '#abc',
  aliases: ['Latin'],
  work_count: 1,
  folder_count: 0,
}
const BETA = {
  id: 't2',
  name: 'Beta',
  color: 'red;background:url(https://evil)',
  aliases: [],
  work_count: 4,
  folder_count: 0,
}

describe('Tags route bridge', () => {
  it('paints each owner from the coordinator list and does not fetch', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    window.fetchTags = vi.fn()
    window.prksIcon = () => '<i data-lucide="arrow-right"></i>'
    registerTagsBridge(window)
    const main = owner()
    const secondary = owner()
    const mainHost = host()
    const secondaryHost = host()
    presentTags({ owner: main, host: mainHost, tags: [ALPHA, BETA], generation: 3, shell: true })
    presentTags({
      owner: secondary,
      host: secondaryHost,
      tags: [{ id: 'side', name: 'Side', work_count: 1, folder_count: 1 }],
      generation: 1,
      shell: false,
    })
    expect(mainHost.querySelector('.prks-page-title')?.textContent).toBe('All tags')
    expect(mainHost.querySelector('[data-tag-alias-edit="t1"]')).not.toBeNull()
    expect(mainHost.querySelector('[data-prks-route="#/search?tag=Alpha"]')).not.toBeNull()
    expect(mainHost.querySelector('[data-tag-alias-edit="side"]')).toBeNull()
    expect(secondaryHost.querySelector('[data-tag-alias-edit="side"]')).not.toBeNull()
    expect(secondaryHost.querySelector('[data-tag-alias-edit="t1"]')).toBeNull()
    const beta = mainHost.querySelector<HTMLElement>('[data-tag-merge="t2"]')?.parentElement
    expect(beta?.getAttribute('style') || '').not.toContain('url(')
    expect(beta?.getAttribute('style') || '').toContain('#6d6cf7')
    expect(readRouteSurface(main)).toMatchObject({
      name: 'tags',
      canonicalHash: '#/tags',
      ownsMainShell: true,
    })
    expect(readRouteSurface(secondary)?.ownsMainShell).toBe(false)
    expect(fetchMock).not.toHaveBeenCalled()
    expect(window.fetchTags).not.toHaveBeenCalled()

    mainHost.querySelector<HTMLButtonElement>('[data-tag-alias-edit="t1"]')?.click()
    await nextTick()
    expect(mainHost.querySelector('#tags-page-alias-canonical')?.textContent).toBe('Alpha')
    expect(mainHost.querySelector('[data-alias-remove="Latin"]')).not.toBeNull()
    expect(secondaryHost.querySelector('#tags-page-alias-modal')).toBeNull()
    window.prksVueCloseTagsAliasModal?.()
    await nextTick()
    expect(mainHost.querySelector('#tags-page-alias-modal')).toBeNull()
  })

  it('reopens only the resumed alias dialog and ignores a stale generation', async () => {
    const pane = owner()
    const el = host()
    presentTags({ owner: pane, host: el, tags: [], generation: 2 })
    expect(el.querySelector('.tags-page__empty')?.textContent).toContain('No tags in use yet')
    dismissTags(pane)
    presentTags({
      owner: pane,
      host: el,
      tags: [ALPHA],
      generation: 2,
      resume: { aliasTagId: 't1' },
    })
    await nextTick()
    expect(el.querySelector('#tags-page-alias-delete-btn')).toBeNull()
    presentTags({
      owner: pane,
      host: el,
      tags: [ALPHA, BETA],
      generation: 3,
      resume: { aliasTagId: 't1' },
    })
    expect(el.querySelector('#tags-page-alias-canonical')?.textContent).toBe('Alpha')
    expect(el.querySelector('#tags-page-alias-delete-btn')).not.toBeNull()
    const chips = [...el.querySelectorAll('[data-tag-alias-edit]')].map((node) => node.getAttribute('data-tag-alias-edit'))
    expect(chips).toEqual(['t1', 't2'])
  })

  it('registers the bridge and paints the host that stored the request', () => {
    const el = host()
    const decoy = host()
    el.setAttribute('data-prks-vue-route-host', 'true')
    decoy.setAttribute('data-prks-vue-route-host', 'true')
    const pane = owner()
    ;(el as HTMLElement & { __prksVueRouteRequest?: object }).__prksVueRouteRequest = {
      feature: 'tags',
      owner: pane,
      host: decoy,
      tags: [ALPHA],
      generation: 1,
      shell: true,
    }
    registerTagsBridge(window)
    expect(window.prksVuePresentTags).toBeTypeOf('function')
    expect(window.prksVueDismissTags).toBeTypeOf('function')
    expect((el as HTMLElement & { __prksVueRouteRequest?: unknown }).__prksVueRouteRequest).toBeUndefined()
    expect(el.querySelector('[data-tag-alias-edit="t1"]')).not.toBeNull()
    expect(decoy.querySelector('[data-prks-tags-page]')).toBeNull()
  })

  it('dismisses one owner and leaves the other mounted', () => {
    registerTagsBridge(window)
    const main = owner()
    const secondary = owner()
    const mainHost = host()
    const secondaryHost = host()
    window.prksVuePresentTags?.({
      owner: main,
      host: mainHost,
      tags: [ALPHA],
      generation: 2,
      shell: true,
    })
    window.prksVuePresentTags?.({
      owner: secondary,
      host: secondaryHost,
      tags: [BETA],
      generation: 1,
      shell: false,
      resume: { aliasTagId: 't2' },
    })
    expect(secondaryHost.querySelector('#tags-page-alias-canonical')?.textContent).toBe('Beta')
    window.prksVueDismissTags?.(main)
    expect(mainHost.querySelector('[data-prks-tags-page]')).toBeNull()
    expect(secondaryHost.querySelector('[data-tag-merge="t2"]')).not.toBeNull()
    expect(secondaryHost.querySelector('#tags-page-alias-canonical')?.textContent).toBe('Beta')
  })
})
