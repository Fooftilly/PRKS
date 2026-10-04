import { onBeforeUnmount, onBeforeUpdate, onMounted, onUpdated, toValue, type MaybeRefOrGetter, type Ref } from 'vue'

/**
 * Preview + lazy-thumb lifetime for a Vue Work-card collection.
 * Release runs in onBeforeUpdate while the previous cards are still in the tree.
 * Init (and icon refresh) runs after mount/update. Offline-cached collections
 * may skip lazy-thumb init; they still refresh icons.
 */
export function useWorkCardCollection(
  root: Ref<HTMLElement | null>,
  options: { initWhen?: MaybeRefOrGetter<boolean> } = {},
): { release: () => void } {
  function shouldInit(): boolean {
    return options.initWhen == null ? true : toValue(options.initWhen)
  }

  function release(): void {
    const el = root.value
    if (!el) return
    window.prksReleaseWorkThumbPreview?.(el)
    window.prksReleaseLazyWorkThumbs?.(el)
  }

  function init(): void {
    const el = root.value
    if (!el) return
    window.prksRefreshIcons?.(el)
    if (shouldInit()) window.prksInitLazyWorkThumbs?.(el)
  }

  onMounted(init)
  onBeforeUpdate(release)
  onUpdated(init)
  onBeforeUnmount(release)
  return { release }
}
