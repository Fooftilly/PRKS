import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import appSource from '../../../frontend/js/app.js?raw'

// The route host is a persistent tab root that outlives every route paint
// (#459). Each page-enter run's listeners must end with that run.

type PlayWindow = Window & { __prksPlayPageEnterAnimation?: (host: HTMLElement) => void }

const playWindow = window as PlayWindow

beforeAll(() => {
  // Everything app.js declares between the route-loading painter and the next helper.
  const loading = appSource.indexOf('function prksRenderRouteLoading(')
  const start = appSource.indexOf('\n}\n', loading) + 3
  const end = appSource.indexOf('function prksResolveWorkspaceMainTab()', start)
  expect(loading).toBeGreaterThan(-1)
  expect(appSource.slice(start, end)).toContain('function prksPlayPageEnterAnimation(')
  window.eval(
    appSource.slice(start, end) + '\nwindow.__prksPlayPageEnterAnimation = prksPlayPageEnterAnimation;\n',
  )
})

afterEach(() => {
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

function play(host: HTMLElement): void {
  playWindow.__prksPlayPageEnterAnimation?.(host)
}

/** Live listener registrations on one target, honouring AbortSignal release. */
function trackListeners(target: HTMLElement): () => string[] {
  const live = new Map<string, number>()
  const key = (type: string, fn: unknown, options?: boolean | AddEventListenerOptions) => {
    const capture = typeof options === 'boolean' ? options : !!options?.capture
    return `${type}|${capture}|${ids.get(fn as object) ?? 'x'}`
  }
  const ids = new WeakMap<object, number>()
  let seq = 0
  const add = target.addEventListener.bind(target)
  const remove = target.removeEventListener.bind(target)
  target.addEventListener = ((type: string, fn: EventListenerOrEventListenerObject, options?: boolean | AddEventListenerOptions) => {
    if (!ids.has(fn)) ids.set(fn, (seq += 1))
    const k = key(type, fn, options)
    const signal = typeof options === 'object' ? options.signal : undefined
    if (!signal?.aborted && !live.has(k)) {
      live.set(k, 1)
      signal?.addEventListener('abort', () => live.delete(k), { once: true })
    }
    add(type, fn, options)
  }) as typeof target.addEventListener
  target.removeEventListener = ((type: string, fn: EventListenerOrEventListenerObject, options?: boolean | EventListenerOptions) => {
    live.delete(key(type, fn, options))
    remove(type, fn, options)
  }) as typeof target.removeEventListener
  return () => Array.from(live.keys()).map((k) => k.split('|')[0]).sort()
}

function tabRoot(): HTMLElement {
  const host = document.createElement('div')
  host.className = 'prks-tab-root'
  host.innerHTML = '<div class="route-body"><span class="card"></span></div>'
  document.body.appendChild(host)
  return host
}

function withEnterAnimation(host: HTMLElement): void {
  const real = window.getComputedStyle.bind(window)
  vi.spyOn(window, 'getComputedStyle').mockImplementation((el: Element, pseudo?: string | null) => {
    const style = real(el, pseudo)
    if (el !== host || !host.classList.contains('prks-page-enter')) return style
    return new Proxy(style, {
      get: (t, p) => (p === 'animationName' ? 'prksPageEnter' : Reflect.get(t, p)),
    })
  })
}

function animationEvent(type: string, name = 'prksPageEnter'): Event {
  const event = new Event(type, { bubbles: true })
  Object.defineProperty(event, 'animationName', { value: name })
  return event
}

describe('page-enter animation listener lifetime (#459)', () => {
  it('leaves no listener on a host that has no enter animation, however often routes paint', () => {
    const host = tabRoot()
    const live = trackListeners(host)
    for (let i = 0; i < 10; i += 1) play(host)
    expect(live()).toEqual([])
    expect(host.classList.contains('prks-page-enter')).toBe(false)
  })

  it('keeps one run per host while animating and releases it on its own animationend', () => {
    const host = tabRoot()
    withEnterAnimation(host)
    const live = trackListeners(host)
    for (let i = 0; i < 10; i += 1) play(host)
    expect(live()).toEqual(['animationcancel', 'animationend'])
    expect(host.classList.contains('prks-page-enter')).toBe(true)

    host.querySelector('.card')?.dispatchEvent(animationEvent('animationend', 'cardPulse'))
    expect(live()).toEqual(['animationcancel', 'animationend'])

    host.dispatchEvent(animationEvent('animationend'))
    expect(live()).toEqual([])
    expect(host.classList.contains('prks-page-enter')).toBe(false)
  })

  it('releases a cancelled run but ignores the cancel left over from its own restart', () => {
    const host = tabRoot()
    withEnterAnimation(host)
    const live = trackListeners(host)
    const running = [{ animationName: 'prksPageEnter' }] as unknown as Animation[]
    const getAnimations = vi.fn<() => Animation[]>(() => running)
    ;(host as HTMLElement & { getAnimations: () => Animation[] }).getAnimations = getAnimations

    play(host)
    play(host)
    host.dispatchEvent(animationEvent('animationcancel'))
    expect(live()).toEqual(['animationcancel', 'animationend'])
    expect(host.classList.contains('prks-page-enter')).toBe(true)

    getAnimations.mockReturnValue([])
    host.dispatchEvent(animationEvent('animationcancel'))
    expect(live()).toEqual([])
    expect(host.classList.contains('prks-page-enter')).toBe(false)
  })
})
