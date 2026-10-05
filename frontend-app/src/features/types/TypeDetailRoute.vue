<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import PrksWorkCard from '../../components/PrksWorkCard.vue'
import { useWorkCardCollection } from '../../components/use-work-card-collection'
import { workCardCollectionFingerprint, workCardThumbOptions } from '../../components/work-card'
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

const modeHost = ref<HTMLElement | null>(null)
const rootEl = ref<HTMLElement | null>(null)

useWorkCardCollection(rootEl, {
  initWhen: () => !offlineCached.value,
  source: () =>
    workCardCollectionFingerprint(rows.value, { suppressThumbnail: offlineCached.value }),
})

function paintMode(): void {
  const host = modeHost.value
  if (!host) return
  host.innerHTML = window.prksWorkBrowseModeToggleHtml?.('prks-work-browse-mode-types') ?? ''
  window.prksBindWorkBrowseMode?.(rootEl.value)
  window.prksRefreshIcons?.(rootEl.value)
}

onMounted(() => {
  paintMode()
})

watch(
  () => props.projection.generation,
  () => {
    paintMode()
  },
  { flush: 'post' },
)
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
    <div :class="collectionClass">
      <p v-if="!rows.length" class="tags-page__empty types-page__empty">No files in this type yet.</p>
      <PrksWorkCard
        v-for="work in rows"
        :key="String(work.id ?? '')"
        :work="work"
        :options="workCardThumbOptions(offlineCached, { hideDocTypeBadge: true })"
      />
    </div>
  </div>
</template>
