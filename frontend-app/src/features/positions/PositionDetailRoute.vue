<script setup lang="ts">
import { computed, inject, onMounted, ref, watch } from 'vue'
import { positionIntentsKey } from './intents'
import { researchMarkdownHtml } from './markdown'
import type { PositionDetailProjection } from './projection'
import type { PositionArgumentRef } from './types'

const props = defineProps<{
  projection: PositionDetailProjection
}>()

const intents = inject(positionIntentsKey)
const rootEl = ref<HTMLElement | null>(null)
const summaryHost = ref<HTMLElement | null>(null)
const descriptionHeadHost = ref<HTMLElement | null>(null)
const descriptionHost = ref<HTMLElement | null>(null)
const argumentsHeadHost = ref<HTMLElement | null>(null)
const argumentsHost = ref<HTMLElement | null>(null)

const availability = computed(() => props.projection.availability)
const position = computed(() => props.projection.position)
const ready = computed(() => availability.value === 'ready' && !!position.value)
const argumentList = computed(() => position.value?.arguments ?? [])

const descriptionHtml = computed(() => {
  const text = String(position.value?.description || '')
  if (!text.trim()) return '<p class="meta-row">No description yet.</p>'
  return researchMarkdownHtml(text)
})

function escHtml(value: string): string {
  const fn = window.prksEscapeHtml
  if (typeof fn === 'function') return fn(value)
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/** DESIGN.md: Argument/Stance rows via shared `prksResearchIndexRowHtml`. */
function argumentRowsHtml(rows: readonly PositionArgumentRef[]): string {
  const rowHtml = window.prksResearchIndexRowHtml
  if (typeof rowHtml !== 'function') return ''
  return rows
    .map((row) => {
      const kindLabel = row.kind === 'stance' ? 'Stance' : 'Argument'
      const html = rowHtml({
        href: `#/arguments/${encodeURIComponent(row.id)}`,
        title: escHtml(row.name || row.id),
        kind: escHtml(kindLabel),
        meta: [escHtml(row.verdict_label || row.verdict_id || '')],
      })
      return html.replace('<a ', '<a data-prks-role="position-argument-link" ')
    })
    .join('')
}

function paintSectionHead(
  host: HTMLElement | null,
  title: string,
  opts: { headingId?: string; count?: number },
): void {
  if (!host) return
  const fn = window.prksResearchSectionHeadHtml
  host.innerHTML = typeof fn === 'function' ? fn(title, opts) : ''
}

function paintSummary(): void {
  const host = summaryHost.value
  const p = position.value
  if (!host || !p) return
  if (typeof window.prksRelSummaryHtml !== 'function') {
    host.innerHTML = ''
    return
  }
  const n = p.arguments.length
  host.innerHTML = window.prksRelSummaryHtml({
    parts: [
      n
        ? `${n} ${n === 1 ? 'targeting Argument/Stance' : 'targeting Arguments/Stances'}`
        : null,
    ],
  })
}

function paintDescription(): void {
  const host = descriptionHost.value
  if (!host) return
  host.innerHTML = descriptionHtml.value
}

function paintArguments(): void {
  const host = argumentsHost.value
  if (!host) return
  host.innerHTML = argumentList.value.length ? argumentRowsHtml(argumentList.value) : ''
}

function paintSectionHeads(): void {
  paintSectionHead(descriptionHeadHost.value, 'Description', {
    headingId: 'prks-position-desc-h',
  })
  paintSectionHead(argumentsHeadHost.value, 'Arguments & Stances', {
    headingId: 'prks-position-args-h',
    count: argumentList.value.length,
  })
}

function refreshIcons(): void {
  const root = rootEl.value
  if (root && typeof window.prksRefreshIcons === 'function') window.prksRefreshIcons(root)
}

function onViewGraph(): void {
  if (position.value) intents?.viewGraph(position.value)
}

function paintAll(): void {
  paintSummary()
  paintSectionHeads()
  paintDescription()
  paintArguments()
  refreshIcons()
}

onMounted(() => {
  paintAll()
})

watch(
  () =>
    [
      props.projection.generation,
      position.value?.id,
      position.value?.name,
      position.value?.description,
      position.value?.arguments,
    ] as const,
  () => {
    paintAll()
  },
  { flush: 'post' },
)
</script>

<template>
  <div ref="rootEl" data-prks-position-detail-view>
    <template v-if="availability === 'unavailable'">
      <div class="prks-page-header page-header">
        <h2 class="prks-page-title">Position not available offline</h2>
      </div>
      <p class="prks-inline-message" data-prks-role="offline-unavailable">
        This item is not available offline.
      </p>
    </template>
    <template v-else-if="availability === 'not-found' || !position">
      <div class="prks-page-header page-header">
        <h2 class="prks-page-title">Position not found.</h2>
      </div>
      <p class="meta-row">
        <a class="prks-btn prks-btn--secondary" href="#/positions">Back to Positions</a>
      </p>
    </template>
    <template v-else-if="ready && position">
      <div class="prks-page-header page-header">
        <div class="page-header__title-row">
          <div>
            <p class="saved-view-detail__kicker">Position</p>
            <h2 class="prks-page-title">{{ position.name || 'Position' }}</h2>
            <div ref="summaryHost"></div>
          </div>
          <div class="page-header__actions">
            <button
              type="button"
              class="prks-btn prks-btn--secondary"
              id="prks-position-view-graph"
              @click="onViewGraph"
            >
              View in graph
            </button>
          </div>
        </div>
      </div>
      <div class="research-entity">
        <section class="research-entity__section" aria-labelledby="prks-position-desc-h">
          <div ref="descriptionHeadHost"></div>
          <div ref="descriptionHost" class="research-md"></div>
        </section>
        <section class="research-entity__section" aria-labelledby="prks-position-args-h">
          <div ref="argumentsHeadHost"></div>
          <div
            v-if="argumentList.length"
            ref="argumentsHost"
            class="list-view prks-research-index"
          ></div>
          <p v-else class="meta-row">No Arguments or Stances target this Position yet.</p>
        </section>
      </div>
    </template>
  </div>
</template>
