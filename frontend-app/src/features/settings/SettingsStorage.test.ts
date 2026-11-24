import { VueQueryPlugin } from '@tanstack/vue-query'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { parseStorageStatus } from '../../api/storage-status'
import { createPrksQueryClient } from '../../query/client'
import { activateSettingsCategory, openSettingsToLastCategory, resetSettingsNavigationForTests } from './navigation'
import SettingsStorage from './SettingsStorage.vue'
import { formatStorageBytes, storageRows } from './storage-labels'

const BODY = {
  root: { configured: '/srv/prks-link', resolved: '/mnt/disk/prks', source: 'env', editable_in_app: false },
  identity: {
    storage_root_id: 'SR-0123456789abcdef0123456789abcdef',
    layout_version: 1,
    state: 'active',
    relocation_state: 'none',
    backend_type: 'local',
  },
  filesystem: {
    free_bytes: 5 * 1024 ** 3,
    total_bytes: 100 * 1024 ** 3,
    filesystem_type: 'ext4',
    network: 'local',
    case_sensitive: true,
    normalization: 'preserved',
    directory_fsync: true,
  },
  objects: { referenced: 12, missing: 0, pending_cleanup: 2, orphans: 1, held: 0 },
  database: { engine: 'PostgreSQL', host: 'db', port: '5432', name: 'prks' },
}

const mounted: VueWrapper[] = []

afterEach(() => {
  for (const wrapper of mounted.splice(0)) wrapper.unmount()
  resetSettingsNavigationForTests()
  vi.unstubAllGlobals()
})

function answer(status: number, body: unknown): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  }))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function mountPanel(): VueWrapper {
  const wrapper = mount(SettingsStorage, {
    global: { plugins: [[VueQueryPlugin, { queryClient: createPrksQueryClient() }]] },
  })
  mounted.push(wrapper)
  return wrapper
}

describe('SettingsStorage', () => {
  it('reads nothing until the Storage category opens', async () => {
    const fetchMock = answer(200, BODY)
    mountPanel()
    await flushPromises()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('shows the files and the database, read-only', async () => {
    const fetchMock = answer(200, BODY)
    activateSettingsCategory('storage')
    const wrapper = mountPanel()
    await flushPromises()
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('/api/storage-status')
    const files = wrapper.get('[data-prks-storage="files"]').text()
    expect(files).toContain('/mnt/disk/prks')
    expect(files).toContain('Configured as /srv/prks-link')
    expect(files).toContain('5.0 GB free of 100 GB')
    expect(files).toContain('Set by the environment')
    expect(wrapper.get('[data-prks-storage="database"]').text()).toContain('PostgreSQL · db:5432/prks')
    expect(wrapper.get('[data-prks-storage="pending_cleanup"]').text()).toContain('2')
    expect(wrapper.get('[data-prks-storage="storage_root_id"]').text()).toContain('SR-0123456789abcdef0123456789abcdef')
    expect(wrapper.get('[data-prks-storage="case_sensitive"]').text()).toContain('Different files')
    expect(wrapper.findAll('input, select, textarea')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('Save')
    expect(wrapper.text()).not.toContain('missing from the folder')
  })

  it('warns about missing files and a network filesystem', async () => {
    answer(200, {
      ...BODY,
      objects: { ...BODY.objects, missing: 3 },
      filesystem: { ...BODY.filesystem, network: 'network', case_sensitive: null },
    })
    activateSettingsCategory('storage')
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.text()).toContain('3 files the Library names are missing from the folder.')
    expect(wrapper.text()).toContain('network filesystem')
    expect(wrapper.get('[data-prks-storage="case_sensitive"]').text()).toContain('Not measured')
  })

  it('says when the counts could not be read', async () => {
    answer(200, { ...BODY, objects: null })
    activateSettingsCategory('storage')
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.get('[data-prks-storage="unavailable"]').text()).toContain('Could not be read')
  })

  it('tells a member who is not the owner why nothing shows', async () => {
    answer(403, { error: 'forbidden', code: 'forbidden' })
    activateSettingsCategory('storage')
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.text()).toContain('Only the Library owner can see where the Library is stored.')
  })

  it('refuses a body that claims the browser may change the root', () => {
    expect(() => parseStorageStatus({ ...BODY, root: { ...BODY.root, editable_in_app: true } })).toThrow()
    expect(() => parseStorageStatus({ ...BODY, filesystem: { ...BODY.filesystem, network: 'cloud' } })).toThrow()
    expect(() => parseStorageStatus({ ...BODY, objects: { ...BODY.objects, missing: -1 } })).toThrow()
  })

  it('reads again when Settings reopens on Storage, so free space stays current', async () => {
    let body: unknown = BODY
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)
    activateSettingsCategory('storage')
    const wrapper = mountPanel()
    await flushPromises()
    body = { ...BODY, filesystem: { ...BODY.filesystem, free_bytes: 1024 ** 3 } }
    openSettingsToLastCategory()
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(wrapper.get('[data-prks-storage="files"]').text()).toContain('1.0 GB free')
  })

  it('hides the last answer once a later read fails', async () => {
    let status = 200
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(status === 200 ? BODY : { error: 'Forbidden', code: 'forbidden' }), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)
    activateSettingsCategory('storage')
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.find('#prks-storage-objects').exists()).toBe(true)
    status = 403
    openSettingsToLastCategory()
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(wrapper.find('#prks-storage-locations').exists()).toBe(false)
    expect(wrapper.find('#prks-storage-objects').exists()).toBe(false)
    expect(wrapper.find('#prks-storage-folder').exists()).toBe(false)
  })

  it('keeps a known port when the server has no host name', () => {
    const rows = storageRows(parseStorageStatus({ ...BODY, database: { engine: 'PostgreSQL', host: null, port: '5433', name: 'prks' } }))
    expect(rows.database).toBe('PostgreSQL · port 5433/prks')
  })

  it('formats sizes', () => {
    expect(formatStorageBytes(512)).toBe('512 B')
    expect(formatStorageBytes(1536)).toBe('1.5 KB')
    expect(formatStorageBytes(250 * 1024 ** 3)).toBe('250 GB')
  })
})
