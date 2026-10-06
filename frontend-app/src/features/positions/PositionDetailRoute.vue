<script setup lang="ts">
import { computed, inject, ref, watch } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import PrksLinkButton from '../../components/PrksLinkButton.vue'
import PrksRelSummary from '../../components/PrksRelSummary.vue'
import PrksResearchRow from '../../components/PrksResearchRow.vue'
import PrksResearchSectionHead from '../../components/PrksResearchSectionHead.vue'
import { positionIntentsKey } from './intents'
import { researchMarkdownHtml } from './markdown'
import type { PositionDetailProjection } from './projection'

const props = defineProps<{
  projection: PositionDetailProjection
}>()

const intents = inject(positionIntentsKey)
const rootEl = ref<HTMLElement | null>(null)

const availability = computed(() => props.projection.availability)
const position = computed(() => props.projection.position)
const ready = computed(() => availability.value === 'ready' && !!position.value)
const argumentList = computed(() => position.value?.arguments ?? [])
const descriptionHtml = computed(() => {
  const text = String(position.value?.description || '')
  if (!text.trim()) return '<p class="meta-row">No description yet.</p>'
  return researchMarkdownHtml(text)
})
const summaryParts = computed(() => {
  const n = argumentList.value.length
  return [
    n ? `${n} ${n === 1 ? 'targeting Argument/Stance' : 'targeting Arguments/Stances'}` : null,
  ]
})

function refreshIcons(): void {
  const root = rootEl.value
  if (root && typeof window.prksRefreshIcons === 'function') window.prksRefreshIcons(root)
}

function onViewGraph(): void {
  if (position.value) intents?.viewGraph(position.value)
}

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
    refreshIcons()
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
      <PrksInlineMessage data-prks-role="offline-unavailable">
        This item is not available offline.
      </PrksInlineMessage>
    </template>
    <template v-else-if="availability === 'not-found' || !position">
      <div class="prks-page-header page-header">
        <h2 class="prks-page-title">Position not found.</h2>
      </div>
      <p class="meta-row">
        <PrksLinkButton href="#/positions">Back to Positions</PrksLinkButton>
      </p>
    </template>
    <template v-else-if="ready && position">
      <div class="prks-page-header page-header">
        <div class="page-header__title-row">
          <div>
            <p class="saved-view-detail__kicker">Position</p>
            <h2 class="prks-page-title">{{ position.name || 'Position' }}</h2>
            <PrksRelSummary :parts="summaryParts" />
          </div>
          <div class="page-header__actions">
            <PrksButton id="prks-position-view-graph" @click="onViewGraph">
              View in graph
            </PrksButton>
          </div>
        </div>
      </div>
      <div class="research-entity">
        <section class="research-entity__section" aria-labelledby="prks-position-desc-h">
          <PrksResearchSectionHead title="Description" heading-id="prks-position-desc-h" />
          <div class="research-md" v-html="descriptionHtml"></div>
        </section>
        <section class="research-entity__section" aria-labelledby="prks-position-args-h">
          <PrksResearchSectionHead
            title="Arguments &amp; Stances"
            heading-id="prks-position-args-h"
            :count="argumentList.length"
          />
          <div v-if="argumentList.length" class="list-view prks-research-index">
            <PrksResearchRow
              v-for="row in argumentList"
              :key="row.id"
              :href="`#/arguments/${encodeURIComponent(row.id)}`"
              :title="row.name || row.id"
              :kind="row.kind === 'stance' ? 'Stance' : 'Argument'"
              :meta="[row.verdict_label || row.verdict_id || ''].filter(Boolean)"
              data-prks-role="position-argument-link"
            />
          </div>
          <p v-else class="meta-row">No Arguments or Stances target this Position yet.</p>
        </section>
      </div>
    </template>
  </div>
</template>
