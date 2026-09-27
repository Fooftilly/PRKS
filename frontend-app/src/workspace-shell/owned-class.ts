import type { Directive } from 'vue'

export type OwnedFlags = Record<string, boolean>

const tracked = new WeakMap<HTMLElement, string[]>()

function apply(el: HTMLElement, flags: OwnedFlags | null | undefined): void {
  const next = flags ?? {}
  const prev = tracked.get(el) ?? []
  for (const name of prev) {
    if (!Object.prototype.hasOwnProperty.call(next, name)) el.classList.remove(name)
  }
  const names = Object.keys(next)
  for (const name of names) el.classList.toggle(name, !!next[name])
  tracked.set(el, names)
}

/**
 * Toggles only the classes this shell owns. Classes another module sets on the
 * same node (for example `is-drag-source`) stay in place. Vue's class patch
 * would replace the whole attribute.
 */
export const vOwnedClass: Directive<HTMLElement, OwnedFlags> = {
  mounted(el, binding) {
    apply(el, binding.value)
  },
  updated(el, binding) {
    apply(el, binding.value)
  },
}
