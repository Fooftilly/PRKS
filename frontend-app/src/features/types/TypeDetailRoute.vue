<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { typeDetailWorkCardHtml } from './legacy-work-card'
import type { TypeDetailProjection } from './projection'

const props = defineProps<{
  projection: TypeDetailProjection
}>()

const offlineCached = computed(() => props.projection.offlineCached === true)
const rows = computed(() => props.projection.rows)
function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

const badgeHtml = computed(() => {
  const fn = window.prksDocTypeBadgeHtml
  if (typeof fn === 'function') return fn(props.projection.docType)
  return `<span class="status-badge Planned">${escapeHtml(props.projection.label)}</span>`
})
const collectionClass = computed(() => {
  void props.projection.generation
  const fn = window.prksWorkBrowseCollectionClass
  return typeof fn === 'function' ? fn('types-page__detail-grid') : 'card-grid types-page__detail-grid'
})
const collectionHtml = computed(() => {
  if (!rows.value.length) {
    return '<p class="tags-page__empty types-page__empty">No files in this type yet.</p>'
  }
  const cached = offlineCached.value
  return rows.value.map((work) => typeDetailWorkCardHtml(work, cached)).join('')
})

const modeHost = ref<HTMLElement | null>(null)
const collectionEl = ref<HTMLElement | null>(null)
const rootEl = ref<HTMLElement | null>(null)

function paintMode(): void {
  const host = modeHost.value
  if (!host) return
  host.innerHTML = window.prksWorkBrowseModeToggleHtml?.('prks-work-browse-mode-types') ?? ''
  window.prksBindWorkBrowseMode?.(rootEl.value)
  window.prksRefreshIcons?.(rootEl.value)
}

function releaseOwnedThumbResources(root: ParentNode | null): void {
  if (!root) return
  // Scoped only. Another pane may own the preview or its own lazy thumbs.
  window.prksReleaseWorkThumbPreview?.(root)
  window.prksReleaseLazyWorkThumbs?.(root)
}

function paintCollection(): void {
  const root = rootEl.value
  // Release while the previous cards are still inside this type-detail root.
  // beginRoute removes the subtree before app.js can release on contentDiv.
  releaseOwnedThumbResources(root || collectionEl.value)
  const el = collectionEl.value
  if (!el) return
  el.innerHTML = collectionHtml.value
  if (!offlineCached.value && typeof window.prksInitLazyWorkThumbs === 'function') {
    window.prksInitLazyWorkThumbs(el)
  }
  window.prksRefreshIcons?.(root)
}

onMounted(() => {
  paintMode()
  paintCollection()
})

onBeforeUnmount(() => {
  releaseOwnedThumbResources(rootEl.value || collectionEl.value)
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
  <div ref="rootEl" class="types-page types-page--detail" data-prks-types-detail>
    <div class="prks-page-header page-header types-page__detail-header page-header--split">
      <div class="page-header__title-row">
        <h2 class="prks-page-title">Files</h2>
        <div class="types-page__detail-type" v-html="badgeHtml"></div>
        <div ref="modeHost" class="work-html-slot" data-prks-types-mode-host></div>
      </div>
    </div>
    <div ref="collectionEl" :class="collectionClass"></div>
  </div>
</template>
