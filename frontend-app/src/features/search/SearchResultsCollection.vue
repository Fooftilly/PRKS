<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { searchResultCardHtml } from './legacy-work-card'
import type { SearchResultsProjection } from './types'

const props = defineProps<{
  projection: SearchResultsProjection
}>()

const collectionEl = ref<HTMLElement | null>(null)

function escapeText(value: string): string {
  const fn = window.prksEscapeHtml
  if (typeof fn === 'function') return fn(value)
  return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

const collectionClass = computed(() => {
  void props.projection.generation
  const fn = window.prksWorkBrowseCollectionClass
  return typeof fn === 'function' ? fn() : 'card-grid'
})

const collectionHtml = computed(() => {
  const rows = props.projection.rows
  if (!rows.length) {
    return `<p class="prks-inline-message">${escapeText(props.projection.emptyMessage)}</p>`
  }
  return rows.map((work) => searchResultCardHtml(work)).join('')
})

function releaseOwnedThumbResources(root: ParentNode | null): void {
  if (!root) return
  // Scoped only. Another pane may own the preview or its own lazy thumbs.
  window.prksReleaseWorkThumbPreview?.(root)
  window.prksReleaseLazyWorkThumbs?.(root)
}

function paintCollection(): void {
  const el = collectionEl.value
  if (!el) return
  // Release while the previous cards are still inside this collection.
  releaseOwnedThumbResources(el)
  el.innerHTML = collectionHtml.value
  window.prksInitLazyWorkThumbs?.(el)
  window.prksRefreshIcons?.(el)
}

onMounted(paintCollection)

onBeforeUnmount(() => {
  // beginRoute dismisses this tree before app.js releases thumbs on contentDiv.
  releaseOwnedThumbResources(collectionEl.value)
})

watch(collectionHtml, paintCollection, { flush: 'post' })
</script>

<template>
  <div ref="collectionEl" :class="collectionClass" data-prks-search-results></div>
</template>
