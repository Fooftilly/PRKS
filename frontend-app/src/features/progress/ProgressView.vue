<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import PrksState from '../../components/PrksState.vue'
import { legacyWorkCardHtml } from './legacy-work-card'
import { progressCardSubtitle, progressFileCountLabel, progressPageTitle, progressVisibleRows } from './rows'
import type { ProgressSnapshot } from './state'

const props = defineProps<{
  snapshot: ProgressSnapshot
}>()

const status = computed(() => props.snapshot.status)
const offlineCached = computed(() => props.snapshot.offlineCached === true)
const visible = computed(() => progressVisibleRows(props.snapshot.rows, status.value))
const title = computed(() => progressPageTitle(status.value))
const countLabel = computed(() => progressFileCountLabel(visible.value.length))
const collectionClass = computed(() => {
  // Re-read the shared preference when this owner's snapshot changes. A mode
  // click updates the DOM in place and must not be reset until the next snapshot.
  void props.snapshot.generation
  const fn = window.prksWorkBrowseCollectionClass
  return typeof fn === 'function' ? fn() : 'card-grid'
})
const collectionHtml = computed(() => {
  if (!visible.value.length) return ''
  const cached = offlineCached.value
  return visible.value
    .map((work) => legacyWorkCardHtml(work, cached, progressCardSubtitle(work)))
    .join('')
})

const modeHost = ref<HTMLElement | null>(null)
const collectionEl = ref<HTMLElement | null>(null)

function paintMode(): void {
  const host = modeHost.value
  if (!host) return
  const html = window.prksWorkBrowseModeToggleHtml?.('prks-work-browse-mode-progress') ?? ''
  host.innerHTML = html
  window.prksBindWorkBrowseMode?.(host.parentElement)
}

function paintCollection(): void {
  const el = collectionEl.value
  if (!el) return
  el.innerHTML = collectionHtml.value
  window.prksRefreshIcons?.(el)
  window.prksInitLazyWorkThumbs?.(el)
}

onMounted(() => {
  paintMode()
  paintCollection()
})

watch(collectionHtml, () => {
  paintCollection()
}, { flush: 'post' })
</script>

<template>
  <div class="prks-page-header page-header page-header--split" data-prks-progress-view>
    <div class="page-header__title-row">
      <h2 class="prks-page-title">{{ title }}</h2>
      <!-- display:contents keeps the shared browse-mode control a title-row child. -->
      <div ref="modeHost" data-prks-progress-mode-host style="display: contents"></div>
    </div>
    <p class="prks-scope-line" role="status">{{ countLabel }}</p>
  </div>
  <div v-if="visible.length" ref="collectionEl" :class="collectionClass"></div>
  <PrksState v-else kind="empty" heading="No files with this progress status yet." />
</template>
