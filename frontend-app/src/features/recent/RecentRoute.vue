<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { recentWorkCardHtml } from './legacy-work-card'
import { recentOpenedSubtitle, type RecentProjection } from './projection'

const props = defineProps<{
  projection: RecentProjection
}>()

const headerIcon = computed(() => window.prksPageHeaderIconHtml?.('clock') ?? '')
const offlineCached = computed(() => props.projection.offlineCached === true)
const rows = computed(() => props.projection.rows)
const collectionClass = computed(() => {
  void props.projection.generation
  const fn = window.prksWorkBrowseCollectionClass
  return typeof fn === 'function' ? fn() : 'card-grid'
})
const collectionHtml = computed(() => {
  if (!rows.value.length) {
    return '<p class="prks-inline-message">No recently opened documents found.</p>'
  }
  const cached = offlineCached.value
  return rows.value
    .map((work) => recentWorkCardHtml(work, cached, recentOpenedSubtitle(work.last_opened_at)))
    .join('')
})

const modeHost = ref<HTMLElement | null>(null)
const collectionEl = ref<HTMLElement | null>(null)
const rootEl = ref<HTMLElement | null>(null)

function paintMode(): void {
  const host = modeHost.value
  if (!host) return
  host.innerHTML = window.prksWorkBrowseModeToggleHtml?.('prks-work-browse-mode-recent') ?? ''
  window.prksBindWorkBrowseMode?.(rootEl.value)
}

function paintCollection(): void {
  const el = collectionEl.value
  if (!el) return
  el.innerHTML = collectionHtml.value
  window.prksRefreshIcons?.(rootEl.value)
}

onMounted(() => {
  paintMode()
  paintCollection()
})

watch(
  () => props.projection.generation,
  () => {
    paintMode()
  },
  { flush: 'post' },
)

watch(collectionHtml, () => {
  paintCollection()
}, { flush: 'post' })
</script>

<template>
  <div ref="rootEl" data-prks-recent-view>
    <div class="prks-page-header page-header page-header--split">
      <div class="page-header__title-row">
        <h2 class="prks-page-title">
          <span style="display: contents" v-html="headerIcon"></span>
          Recently Opened
        </h2>
        <div ref="modeHost" data-prks-recent-mode-host style="display: contents"></div>
      </div>
    </div>
    <div ref="collectionEl" :class="collectionClass"></div>
  </div>
</template>
