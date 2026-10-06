import { onBeforeUnmount, onMounted, toValue, watch, type MaybeRefOrGetter, type Ref } from 'vue'

/**
 * Preview + lazy-thumb lifetime for a Vue Work-card collection.
 * Release/init follow the work/thumb source fingerprint, not every host
 * render. Release runs flush-pre while previous cards are still in the tree.
 * Offline-cached collections may skip lazy-thumb init; they still refresh icons
 * when the source actually changes.
 */
export function useWorkCardCollection(
  root: Ref<HTMLElement | null>,
  options: {
    initWhen?: MaybeRefOrGetter<boolean>
    source: MaybeRefOrGetter<string>
  },
): { release: () => void } {
  function shouldInit(): boolean {
    return options.initWhen == null ? true : toValue(options.initWhen)
  }

  function sourceKey(): string {
    return `${shouldInit() ? '1' : '0'}\n${toValue(options.source)}`
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
  watch(sourceKey, release, { flush: 'pre' })
  watch(sourceKey, init, { flush: 'post' })
  onBeforeUnmount(release)
  return { release }
}
