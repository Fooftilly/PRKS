import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { dismissRouteSurface, readRouteSurface } from '../../route-surface/lifecycle'
import {
  presentTags,
  registerTagsBridge,
  reportTagsRefreshFailure,
  resetTagsSessionForTests,
} from './session'

afterEach(() => {
  resetTagsSessionForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  delete window.prksVuePresentRoute
  delete window.prksVueDismissRoute
  delete window.prksVueCloseTagsAliasModal
  delete window.prksVueCloseTagsMergeModal
  delete window.prksIcon
  delete window.prksRefreshIcons
  delete window.fetchTags
  delete window.prksAlertMessage
  delete window.prksTagsAddAlias
  delete window.prksTagsRemoveAlias
  delete window.prksTagsDelete
  delete window.prksTagsMerge
  delete window.prksReloadTagsVocabulary
  delete window.prksConfirmDestructive
})

async function flush(): Promise<void> {
  await nextTick()
  await new Promise((resolve) => setTimeout(resolve, 0))
  await nextTick()
}

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
    dismissRouteSurface(pane)
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
    expect(window.prksVuePresentRoute).toBeTypeOf('function')
    expect(window.prksVueDismissRoute).toBeTypeOf('function')
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
    window.prksVuePresentRoute?.({
      feature: 'tags',
      owner: main,
      host: mainHost,
      tags: [ALPHA],
      generation: 2,
      shell: true,
    })
    window.prksVuePresentRoute?.({
      feature: 'tags',
      owner: secondary,
      host: secondaryHost,
      tags: [BETA],
      generation: 1,
      shell: false,
      resume: { aliasTagId: 't2' },
    })
    expect(secondaryHost.querySelector('#tags-page-alias-canonical')?.textContent).toBe('Beta')
    window.prksVueDismissRoute?.(main)
    expect(mainHost.querySelector('[data-prks-tags-page]')).toBeNull()
    expect(secondaryHost.querySelector('[data-tag-merge="t2"]')).not.toBeNull()
    expect(secondaryHost.querySelector('#tags-page-alias-canonical')?.textContent).toBe('Beta')
  })

  it('closes only the alias dialog Escape dismissed and can still close every pane', async () => {
    registerTagsBridge(window)
    const main = owner()
    const secondary = owner()
    const mainHost = host()
    const secondaryHost = host()
    presentTags({ owner: main, host: mainHost, tags: [ALPHA], generation: 2, shell: true })
    presentTags({
      owner: secondary,
      host: secondaryHost,
      tags: [{ id: 'side', name: 'Side', work_count: 1, folder_count: 0 }],
      generation: 1,
      shell: false,
    })
    mainHost.querySelector<HTMLButtonElement>('[data-tag-alias-edit="t1"]')?.click()
    secondaryHost.querySelector<HTMLButtonElement>('[data-tag-alias-edit="side"]')?.click()
    await nextTick()
    const secondaryModal = secondaryHost.querySelector('#tags-page-alias-modal')
    expect(secondaryModal).not.toBeNull()
    window.prksVueCloseTagsAliasModal?.(secondaryModal)
    await nextTick()
    expect(secondaryHost.querySelector('#tags-page-alias-modal')).toBeNull()
    expect(mainHost.querySelector('#tags-page-alias-canonical')?.textContent).toBe('Alpha')
    window.prksVueCloseTagsAliasModal?.()
    await nextTick()
    expect(mainHost.querySelector('#tags-page-alias-modal')).toBeNull()
  })

  it('returns focus to the alias button after a resumed dialog closes', async () => {
    const el = host()
    presentTags({
      owner: owner(),
      host: el,
      tags: [ALPHA],
      generation: 3,
      resume: { aliasTagId: 't1' },
    })
    await nextTick()
    el.querySelector<HTMLButtonElement>('#tags-page-alias-modal-close')?.click()
    await nextTick()
    expect(document.activeElement).toBe(el.querySelector('[data-tag-alias-edit="t1"]'))
  })

  it('focuses a resumed merge dialog and returns to the row button after a delayed merge', async () => {
    let releaseMerge: (value: { ok: boolean }) => void = () => {}
    window.prksTagsMerge = () => new Promise((resolve) => {
      releaseMerge = resolve
    })
    const pane = owner()
    let el = host()
    window.prksReloadTagsVocabulary = async (ownerArg, generation, nextResume) => {
      const next = host()
      el.replaceWith(next)
      el = next
      presentTags({
        owner: ownerArg as ReturnType<typeof owner>,
        host: el,
        tags: [ALPHA, BETA],
        generation,
        resume: nextResume,
      })
      return true
    }
    presentTags({
      owner: pane,
      host: el,
      tags: [ALPHA, BETA],
      generation: 13,
      resume: { mergeSourceId: 't1' },
    })
    await nextTick()
    expect(document.activeElement).toBe(el.querySelector('#tags-page-merge-filter'))

    el.querySelector<HTMLButtonElement>('[data-tag-merge-pick="t2"]')?.click()
    await nextTick()
    el.querySelector<HTMLButtonElement>('#tags-page-merge-confirm-btn')?.click()
    await flush()
    releaseMerge({ ok: true })
    await flush()

    expect(el.querySelector('#tags-page-merge-confirm')).not.toBeNull()
    expect(document.activeElement).toBe(el.querySelector('#tags-page-merge-confirm-btn'))
    el.querySelector<HTMLButtonElement>('#tags-page-merge-modal-close')?.click()
    await nextTick()
    expect(el.querySelector('#tags-page-merge-modal')).toBeNull()
    expect(document.activeElement).toBe(el.querySelector('[data-tag-merge="t1"]'))
  })

  it('keeps the tag list when a refresh fails and shows that failure', async () => {
    const pane = owner()
    const el = host()
    presentTags({ owner: pane, host: el, tags: [ALPHA, BETA], generation: 4 })
    reportTagsRefreshFailure(pane, 'Could not refresh tags.')
    await nextTick()
    expect(el.querySelector('[data-tag-alias-edit="t1"]')).not.toBeNull()
    expect(el.querySelector('.tags-page__empty')).toBeNull()
    expect(el.querySelector('[data-tags-refresh-error]')?.textContent).toContain('Could not refresh tags.')
  })

  it('shows alias and merge failures in the open dialog and stays quiet for a no-op', async () => {
    const alert = vi.fn()
    window.prksAlertMessage = alert
    window.prksTagsAddAlias = async () => {
      throw new Error('Duplicate alias')
    }
    window.prksTagsRemoveAlias = async () => {
      throw new Error('Alias is in use')
    }
    window.prksConfirmDestructive = async () => true
    window.prksTagsDelete = async () => {
      throw new Error('Could not delete tag.')
    }
    window.prksTagsMerge = async () => {
      throw new Error('Could not merge tags.')
    }
    const el = host()
    presentTags({ owner: owner(), host: el, tags: [ALPHA, BETA], generation: 5 })
    el.querySelector<HTMLButtonElement>('[data-tag-alias-edit="t1"]')?.click()
    await nextTick()
    const input = el.querySelector<HTMLInputElement>('#tags-page-alias-input')
    expect(input).not.toBeNull()
    input!.value = 'Latin'
    input!.dispatchEvent(new Event('input'))
    el.querySelector<HTMLButtonElement>('#tags-page-alias-add-btn')?.click()
    await flush()
    expect(el.querySelector('[data-tags-alias-add-error]')?.textContent).toContain('Duplicate alias')
    expect(alert).not.toHaveBeenCalled()

    el.querySelector<HTMLButtonElement>('[data-alias-remove="Latin"]')?.click()
    await flush()
    expect(el.querySelector('[data-tags-alias-remove-error]')?.textContent).toContain('Alias is in use')

    const deleteBtn = el.querySelector<HTMLButtonElement>('#tags-page-alias-delete-btn')
    expect(deleteBtn?.classList.contains('prks-btn--danger')).toBe(true)
    deleteBtn?.click()
    await flush()
    expect(el.querySelector('[data-tags-alias-delete-error]')?.textContent).toContain('Could not delete tag.')
    expect(el.querySelector('#tags-page-alias-modal')).not.toBeNull()
    expect(alert).not.toHaveBeenCalled()

    el.querySelector<HTMLButtonElement>('#tags-page-alias-modal-close')?.click()
    await nextTick()
    expect(el.querySelector('[data-tags-alias-add-error]')).toBeNull()

    el.querySelector<HTMLButtonElement>('[data-tag-merge="t1"]')?.click()
    await nextTick()
    el.querySelector<HTMLButtonElement>('[data-tag-merge-pick="t2"]')?.click()
    await nextTick()
    const mergeBtn = el.querySelector<HTMLButtonElement>('#tags-page-merge-confirm-btn')
    expect(mergeBtn?.classList.contains('prks-btn--primary')).toBe(true)
    mergeBtn?.click()
    await flush()
    expect(el.querySelector('[data-tags-merge-error]')?.textContent).toContain('Could not merge tags.')
    expect(el.querySelector('#tags-page-merge-confirm')).not.toBeNull()
    expect(alert).not.toHaveBeenCalled()

    window.prksTagsAddAlias = async () => ({ ok: false, reason: 'offline' })
    el.querySelector<HTMLButtonElement>('#tags-page-merge-modal-close')?.click()
    await nextTick()
    el.querySelector<HTMLButtonElement>('[data-tag-alias-edit="t1"]')?.click()
    await nextTick()
    const again = el.querySelector<HTMLInputElement>('#tags-page-alias-input')
    again!.value = 'Quiet'
    again!.dispatchEvent(new Event('input'))
    el.querySelector<HTMLButtonElement>('#tags-page-alias-add-btn')?.click()
    await flush()
    expect(el.querySelector('[data-tags-alias-add-error]')).toBeNull()
    expect(alert).not.toHaveBeenCalled()
  })

  it('marks delete and merge busy while the write is in flight', async () => {
    let releaseDelete: () => void = () => {}
    let releaseMerge: () => void = () => {}
    window.prksConfirmDestructive = async () => true
    window.prksTagsDelete = () => new Promise((resolve) => {
      releaseDelete = () => resolve({ ok: true })
    })
    window.prksTagsMerge = () => new Promise((resolve) => {
      releaseMerge = () => resolve({ ok: true })
    })
    window.prksReloadTagsVocabulary = async () => false
    const el = host()
    presentTags({ owner: owner(), host: el, tags: [ALPHA, BETA], generation: 6 })
    el.querySelector<HTMLButtonElement>('[data-tag-alias-edit="t1"]')?.click()
    await nextTick()
    el.querySelector<HTMLButtonElement>('#tags-page-alias-delete-btn')?.click()
    await flush()
    const deleteBtn = el.querySelector<HTMLButtonElement>('#tags-page-alias-delete-btn')
    expect(deleteBtn?.getAttribute('aria-busy')).toBe('true')
    expect(deleteBtn?.disabled).toBe(true)
    expect(deleteBtn?.textContent).toContain('Deleting…')
    releaseDelete()
    await flush()
    expect(deleteBtn?.getAttribute('aria-busy')).toBeNull()

    el.querySelector<HTMLButtonElement>('#tags-page-alias-modal-close')?.click()
    await nextTick()
    el.querySelector<HTMLButtonElement>('[data-tag-merge="t1"]')?.click()
    await nextTick()
    el.querySelector<HTMLButtonElement>('[data-tag-merge-pick="t2"]')?.click()
    await nextTick()
    el.querySelector<HTMLButtonElement>('#tags-page-merge-confirm-btn')?.click()
    await flush()
    const mergeBtn = el.querySelector<HTMLButtonElement>('#tags-page-merge-confirm-btn')
    expect(mergeBtn?.getAttribute('aria-busy')).toBe('true')
    expect(mergeBtn?.disabled).toBe(true)
    expect(mergeBtn?.textContent).toContain('Merging…')
    releaseMerge()
    await flush()
  })

  it('does not reopen a closed alias dialog when the write finishes', async () => {
    let releaseAdd: (value: { ok: boolean }) => void = () => {}
    let resume: { aliasTagId?: string | null } | null | undefined = { aliasTagId: 'pending' }
    window.prksTagsAddAlias = () => new Promise((resolve) => {
      releaseAdd = resolve
    })
    const pane = owner()
    const el = host()
    window.prksReloadTagsVocabulary = async (ownerArg, generation, nextResume) => {
      resume = nextResume
      presentTags({
        owner: ownerArg as ReturnType<typeof owner>,
        host: el,
        tags: [ALPHA, BETA],
        generation,
        resume: nextResume,
      })
      return true
    }
    presentTags({ owner: pane, host: el, tags: [ALPHA, BETA], generation: 8 })
    el.querySelector<HTMLButtonElement>('[data-tag-alias-edit="t1"]')?.click()
    await nextTick()
    const input = el.querySelector<HTMLInputElement>('#tags-page-alias-input')
    input!.value = 'Latin'
    input!.dispatchEvent(new Event('input'))
    el.querySelector<HTMLButtonElement>('#tags-page-alias-add-btn')?.click()
    await flush()
    el.querySelector<HTMLButtonElement>('#tags-page-alias-modal-close')?.click()
    await nextTick()
    releaseAdd({ ok: true })
    await flush()
    expect(resume).toBeNull()
    expect(el.querySelector('#tags-page-alias-modal')).toBeNull()
    expect(el.querySelector('[data-tag-alias-edit="t1"]')).not.toBeNull()
  })

  it('keeps a newer merge dialog when an alias write finishes', async () => {
    let releaseAdd: (value: { ok: boolean }) => void = () => {}
    window.prksTagsAddAlias = () => new Promise((resolve) => {
      releaseAdd = resolve
    })
    const pane = owner()
    const el = host()
    window.prksReloadTagsVocabulary = async (ownerArg, generation, nextResume) => {
      presentTags({
        owner: ownerArg as ReturnType<typeof owner>,
        host: el,
        tags: [ALPHA, BETA],
        generation,
        resume: nextResume,
      })
      return true
    }
    presentTags({ owner: pane, host: el, tags: [ALPHA, BETA], generation: 10 })
    el.querySelector<HTMLButtonElement>('[data-tag-alias-edit="t1"]')?.click()
    await nextTick()
    const input = el.querySelector<HTMLInputElement>('#tags-page-alias-input')
    input!.value = 'Latin'
    input!.dispatchEvent(new Event('input'))
    el.querySelector<HTMLButtonElement>('#tags-page-alias-add-btn')?.click()
    await flush()
    el.querySelector<HTMLButtonElement>('#tags-page-alias-modal-close')?.click()
    await nextTick()
    el.querySelector<HTMLButtonElement>('[data-tag-merge="t2"]')?.click()
    await nextTick()
    expect(el.querySelector('#tags-page-merge-source-label')?.textContent).toContain('Beta')
    releaseAdd({ ok: true })
    await flush()
    expect(el.querySelector('#tags-page-merge-source-label')?.textContent).toContain('Beta')
    expect(el.querySelector('#tags-page-alias-modal')).toBeNull()
  })

  it('keeps a newer alias dialog when a merge finishes', async () => {
    let releaseMerge: (value: { ok: boolean }) => void = () => {}
    window.prksTagsMerge = () => new Promise((resolve) => {
      releaseMerge = resolve
    })
    const pane = owner()
    const el = host()
    window.prksReloadTagsVocabulary = async (ownerArg, generation, nextResume) => {
      presentTags({
        owner: ownerArg as ReturnType<typeof owner>,
        host: el,
        tags: [ALPHA, BETA],
        generation,
        resume: nextResume,
      })
      return true
    }
    presentTags({ owner: pane, host: el, tags: [ALPHA, BETA], generation: 11 })
    el.querySelector<HTMLButtonElement>('[data-tag-merge="t1"]')?.click()
    await nextTick()
    el.querySelector<HTMLButtonElement>('[data-tag-merge-pick="t2"]')?.click()
    await nextTick()
    el.querySelector<HTMLButtonElement>('#tags-page-merge-confirm-btn')?.click()
    await flush()
    el.querySelector<HTMLButtonElement>('#tags-page-merge-modal-close')?.click()
    await nextTick()
    el.querySelector<HTMLButtonElement>('[data-tag-alias-edit="t2"]')?.click()
    await nextTick()
    expect(el.querySelector('#tags-page-alias-canonical')?.textContent).toBe('Beta')
    releaseMerge({ ok: true })
    await flush()
    expect(el.querySelector('#tags-page-alias-canonical')?.textContent).toBe('Beta')
    expect(el.querySelector('#tags-page-merge-modal')).toBeNull()
  })

  it('shows Removing… while an alias remove is in flight and restores the icon', async () => {
    window.prksIcon = (name) => `<i data-lucide="${name}"></i>`
    let releaseRemove: (value: { ok: boolean }) => void = () => {}
    window.prksTagsRemoveAlias = () => new Promise((resolve) => {
      releaseRemove = resolve
    })
    window.prksReloadTagsVocabulary = async () => false
    const el = host()
    presentTags({ owner: owner(), host: el, tags: [ALPHA], generation: 12 })
    el.querySelector<HTMLButtonElement>('[data-tag-alias-edit="t1"]')?.click()
    await nextTick()
    const button = el.querySelector<HTMLButtonElement>('[data-alias-remove="Latin"]')
    expect(button).not.toBeNull()
    expect(button?.textContent).not.toContain('Removing')
    expect(button?.querySelector('[data-lucide="x"]')).not.toBeNull()
    button?.click()
    await flush()
    expect(button?.getAttribute('aria-busy')).toBe('true')
    expect(button?.disabled).toBe(true)
    expect(button?.textContent).toContain('Removing…')
    expect(button?.querySelector('[data-lucide]')).toBeNull()
    releaseRemove({ ok: true })
    await flush()
    const restored = el.querySelector<HTMLButtonElement>('[data-alias-remove="Latin"]')
    expect(restored?.getAttribute('aria-busy')).toBeNull()
    expect(restored?.disabled).toBe(false)
    expect(restored?.textContent).not.toContain('Removing')
    expect(restored?.getAttribute('aria-label')).toBe('Remove alias')
    expect(restored?.querySelector('[data-lucide="x"]')).not.toBeNull()
  })
})
