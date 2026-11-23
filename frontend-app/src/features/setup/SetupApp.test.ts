import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SetupApp from './SetupApp.vue'
import { readSetupState, submitSetup, waitForApplication } from './setup-service'

const TOKEN = 'a'.repeat(43)

type Answer = { status: number; body: unknown; headers?: Record<string, string> }
let state: string
let answer: Answer
let posts: Array<Record<string, unknown>>

function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

beforeEach(() => {
  state = 'unconfigured'
  answer = { status: 200, body: { state: 'ready', created: true, signed_in: true } }
  posts = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_path: string, init?: RequestInit) => {
      if (init?.method === 'POST') {
        posts.push(JSON.parse(String(init.body)) as Record<string, unknown>)
        return json(answer.status, answer.body, answer.headers)
      }
      return json(200, { state })
    }),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

async function mounted(
  wait: () => Promise<boolean> = async () => true,
): Promise<{ wrapper: VueWrapper; opened: { count: number } }> {
  const opened = { count: 0 }
  const wrapper = mount(SetupApp, {
    props: { wait, openApplication: () => (opened.count += 1) },
    attachTo: document.body,
  })
  await flushPromises()
  return { wrapper, opened }
}

async function fillToReview(wrapper: VueWrapper): Promise<void> {
  await wrapper.get('#prks-setup-token').setValue(`  ${TOKEN} `)
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  await wrapper.get('#prks-setup-login').setValue('alice')
  await wrapper.get('#prks-setup-password').setValue('correct horse')
  await wrapper.get('#prks-setup-password-again').setValue('correct horse')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
}

describe('SetupApp', () => {
  it('walks token, owner and Library, review, then opens the application', async () => {
    const { wrapper, opened } = await mounted()
    expect(wrapper.text()).toContain('Step 1 of 3')
    expect(wrapper.text()).toContain('python prks_app.py admin bootstrap issue-token')
    await fillToReview(wrapper)
    expect(wrapper.text()).toContain('Step 3 of 3')
    // Defaults shown on the review: display name is the login, Library is "Library".
    expect(wrapper.get('.prks-setup__review').text()).toContain('alice')
    expect(wrapper.get('.prks-setup__review').text()).toContain('Library')
    expect(posts).toEqual([])
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(posts).toEqual([
      { token: TOKEN, login: 'alice', display_name: '', password: 'correct horse', library_name: 'Library' },
    ])
    expect(opened.count).toBe(1)
    wrapper.unmount()
  })

  it('refuses an empty or malformed token before leaving step 1', async () => {
    const { wrapper } = await mounted()
    await wrapper.get('form').trigger('submit')
    expect(wrapper.get('#prks-setup-token-error').text()).toBe('Enter the setup token.')
    await wrapper.get('#prks-setup-token').setValue('short')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.get('#prks-setup-token-error').text()).toContain('not a setup token')
    expect(wrapper.text()).toContain('Step 1 of 3')
    wrapper.unmount()
  })

  it('checks the account fields and that the passwords match', async () => {
    const { wrapper } = await mounted()
    await wrapper.get('#prks-setup-token').setValue(TOKEN)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    await wrapper.get('form').trigger('submit')
    expect(wrapper.get('#prks-setup-login-error').text()).toBe('Enter a login.')
    expect(wrapper.get('#prks-setup-password-error').text()).toBe('Enter a password.')
    await wrapper.get('#prks-setup-login').setValue('alice')
    await wrapper.get('#prks-setup-password').setValue('one')
    await wrapper.get('#prks-setup-password-again').setValue('two')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.get('#prks-setup-password-again-error').text()).toBe('The two passwords differ.')
    expect(wrapper.text()).toContain('Step 2 of 3')
    wrapper.unmount()
  })

  it('returns to the token step when the server refuses the token', async () => {
    answer = { status: 403, body: { error: 'bootstrap_token_invalid' } }
    const { wrapper, opened } = await mounted()
    await fillToReview(wrapper)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('Step 1 of 3')
    expect(wrapper.get('#prks-setup-token-error').text()).toContain('not valid')
    expect(opened.count).toBe(0)
    wrapper.unmount()
  })

  it('keeps the review open with the reason for other refusals', async () => {
    answer = { status: 429, body: { error: 'too_many_attempts' }, headers: { 'Retry-After': '30' } }
    const { wrapper } = await mounted()
    await fillToReview(wrapper)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('Try again in 30 seconds.')
    expect(wrapper.text()).toContain('Step 3 of 3')
    // The button is usable again for a retry.
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('names a field the server refused and returns to it', async () => {
    answer = { status: 400, body: { error: 'invalid_request', field: 'library_name', reason: 'too_long' } }
    const { wrapper } = await mounted()
    await fillToReview(wrapper)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('Step 2 of 3')
    expect(wrapper.get('#prks-setup-library-error').text()).toBe('This Library name is too long.')
    wrapper.unmount()
  })

  it('says a refused name cannot be used when it is not too long', async () => {
    answer = { status: 400, body: { error: 'invalid_request', field: 'display_name', reason: 'invalid' } }
    const { wrapper } = await mounted()
    await fillToReview(wrapper)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('#prks-setup-display-name-error').text()).toBe('This display name cannot be used.')
    expect(wrapper.get('#prks-setup-display-name').attributes('aria-describedby')).toContain(
      'prks-setup-display-name-error',
    )
    wrapper.unmount()
  })

  it('opens the application when setup is already done', async () => {
    state = 'ready'
    const { wrapper, opened } = await mounted()
    expect(opened.count).toBe(1)
    expect(posts).toEqual([])
    wrapper.unmount()
  })

  it('says an earlier setup did not finish', async () => {
    state = 'bootstrapping'
    const { wrapper } = await mounted()
    expect(wrapper.text()).toContain('An earlier setup did not finish')
    wrapper.unmount()
  })

  it('offers a retry when PRKS does not answer', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('network') }))
    const { wrapper } = await mounted()
    expect(wrapper.text()).toContain('PRKS did not answer')
    wrapper.unmount()
  })

  it('says when the application did not open after setup', async () => {
    const { wrapper, opened } = await mounted(async () => false)
    await fillToReview(wrapper)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('has not opened yet')
    expect(opened.count).toBe(0)
    wrapper.unmount()
  })
})

describe('setup service', () => {
  it('reads only known states', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(200, { state: 'other' })))
    expect(await readSetupState()).toBeNull()
    vi.stubGlobal('fetch', vi.fn(async () => json(503, { error: 'unavailable' })))
    expect(await readSetupState()).toBeNull()
    vi.stubGlobal('fetch', vi.fn(async () => json(200, { state: 'bootstrapping' })))
    expect(await readSetupState()).toBe('bootstrapping')
  })

  it('maps an unreachable server and a body without a code', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('network') }))
    const input = { token: TOKEN, login: 'a', displayName: '', password: 'p', libraryName: '' }
    expect(await submitSetup(input)).toEqual({ ok: false, error: 'unreachable' })
    vi.stubGlobal('fetch', vi.fn(async () => new Response('nope', { status: 502 })))
    expect(await submitSetup(input)).toMatchObject({ ok: false, error: 'unavailable' })
  })

  it('waits for the application to answer its session read', async () => {
    const statuses = [503, 503, 401]
    const fetchImpl = vi.fn(async () => {
      const status = statuses.shift()
      if (status === undefined) throw new TypeError('gone')
      return json(status, {})
    }) as unknown as typeof fetch
    const sleep = vi.fn(async () => undefined)
    expect(await waitForApplication({ fetchImpl, sleep })).toBe(true)
    expect(sleep).toHaveBeenCalledTimes(2)
    const down = vi.fn(async () => { throw new TypeError('down') }) as unknown as typeof fetch
    expect(await waitForApplication({ fetchImpl: down, sleep, maxAttempts: 3 })).toBe(false)
  })
})
