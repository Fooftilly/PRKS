<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { typeFileCountLabel, typeDetailHref, type TypesIndexProjection, type TypesIndexRow } from './projection'

const props = defineProps<{
  projection: TypesIndexProjection
}>()

const rows = computed(() => props.projection.rows)
const rootEl = ref<HTMLElement | null>(null)
const chevron = computed(() => window.prksIcon?.('chevronRight', { size: 'sm' }) ?? '→')

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function badgeMarkup(row: TypesIndexRow): string {
  const fn = window.prksDocTypeBadgeHtml
  if (typeof fn === 'function') return fn(row.value)
  return `<span class="status-badge Planned">${escapeHtml(row.label)}</span>`
}

function paintBadges(): void {
  const root = rootEl.value
  if (!root) return
  root.querySelectorAll<HTMLElement>('[data-prks-type-badge]').forEach((el) => {
    const value = el.getAttribute('data-prks-type-badge') || ''
    const row = rows.value.find((item) => item.value === value)
    el.innerHTML = row ? badgeMarkup(row) : ''
  })
  window.prksRefreshIcons?.(root)
}

onMounted(paintBadges)

watch(
  () => props.projection.generation,
  () => {
    paintBadges()
  },
  { flush: 'post' },
)

watch(rows, () => {
  paintBadges()
}, { flush: 'post' })
</script>

<template>
  <div ref="rootEl" class="types-page" data-prks-types-index>
    <div class="prks-page-header page-header tags-page__header">
      <h2 class="prks-page-title">File types</h2>
      <p class="tags-page__sub types-page__sub">Browse files by BibTeX document type. Click row to open matching files.</p>
    </div>
    <div class="list-view types-page__list">
      <p v-if="!rows.length" class="tags-page__empty types-page__empty">No files in library yet. Add file to start grouping by BibTeX type.</p>
      <div
        v-for="row in rows"
        :key="row.value"
        class="project-card types-page__list-item"
        :data-prks-route="typeDetailHref(row.value)"
        data-prks-middleclick-nav="1"
      >
        <div class="types-page__list-main">
          <span class="types-page__badge-host" style="display: contents" :data-prks-type-badge="row.value"></span>
          <p class="meta-row types-page__list-stats">{{ typeFileCountLabel(row.count) }}</p>
        </div>
        <span class="types-page__list-arrow" aria-hidden="true" v-html="chevron"></span>
      </div>
    </div>
  </div>
</template>
