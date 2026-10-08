/**
 * Fake Web Locks and BroadcastChannel for identity tests. One `browser`
 * models one origin: locks and channels are shared by every page created from it.
 */
import type { ChannelLike, LockManagerLike } from '../identity'

export function createFakeBrowser() {
  const held = new Map<string, string>()
  const channels = new Set<ChannelLike & { page: string }>()

  function locksFor(page: string): LockManagerLike & { releaseAll(): void } {
    const mine = new Set<string>()
    return {
      request(name, options, callback) {
        return new Promise((resolve) => {
          setTimeout(() => {
            if (held.has(name)) {
              if (!options.ifAvailable) throw new Error('fake locks only model ifAvailable')
              resolve(callback(null))
              return
            }
            held.set(name, page)
            mine.add(name)
            void Promise.resolve(callback({ name })).then(() => {
              held.delete(name)
              mine.delete(name)
              resolve(undefined)
            })
          }, 0)
        })
      },
      query() {
        return Promise.resolve({ held: [...held.keys()].map((name) => ({ name })) })
      },
      // Models page unload: the browser drops every lock the page held.
      releaseAll() {
        for (const name of mine) held.delete(name)
        mine.clear()
      },
    }
  }

  function channelFor(page: string): (name: string) => ChannelLike {
    return () => {
      const channel: ChannelLike & { page: string } = {
        page,
        onmessage: null,
        postMessage(message) {
          const data = structuredClone(message)
          for (const other of channels) {
            if (other === channel) continue
            setTimeout(() => other.onmessage?.({ data }), 1)
          }
        },
        close() {
          channels.delete(channel)
        },
      }
      channels.add(channel)
      return channel
    }
  }

  function sessionStorageWith(initial: Record<string, string> = {}) {
    const values = new Map(Object.entries(initial))
    return {
      getItem: (key: string) => (values.has(key) ? (values.get(key) as string) : null),
      setItem: (key: string, value: string) => {
        values.set(key, value)
      },
      values,
    }
  }

  return { held, locksFor, channelFor, sessionStorageWith }
}
