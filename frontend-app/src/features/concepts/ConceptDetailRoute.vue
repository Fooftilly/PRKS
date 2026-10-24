<script setup lang="ts">
import { computed, inject, ref, watch } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import PrksLinkButton from '../../components/PrksLinkButton.vue'
import PrksRelSummary from '../../components/PrksRelSummary.vue'
import PrksResearchRow from '../../components/PrksResearchRow.vue'
import PrksResearchSectionHead from '../../components/PrksResearchSectionHead.vue'
import { conceptIntentsKey } from './intents'
import { researchMarkdownHtml } from './markdown'
import type { ConceptDetailProjection } from './projection'

const MUTATION_ROLE = 'concept-mutation-control'

const props = defineProps<{
  projection: ConceptDetailProjection
}>()

const intents = inject(conceptIntentsKey)
const rootEl = ref<HTMLElement | null>(null)

const availability = computed(() => props.projection.availability)
const concept = computed(() => props.projection.concept)
const ready = computed(() => availability.value === 'ready' && !!concept.value)

const aliasSub = computed(() => {
  const n = concept.value?.aliases.length || 0
  if (!n) return ''
  return `${n} ${n === 1 ? 'alias' : 'aliases'}`
})

const parentSub = computed(() => {
  const n = concept.value?.parents.length || 0
  if (!n) return ''
  return `${n} ${n === 1 ? 'parent' : 'parents'}`
})

const mentionCount = computed(() => Number(concept.value?.mention_count) || 0)
const definitionHtml = computed(() => researchMarkdownHtml(concept.value?.description))
const summaryParts = computed(() => {
  const c = concept.value
  if (!c) return []
  const parentCount = c.parents.length
  return [
    parentCount ? `${parentCount} ${parentCount === 1 ? 'parent' : 'parents'}` : null,
    mentionCount.value > 0
      ? `${mentionCount.value} ${mentionCount.value === 1 ? 'note mention' : 'note mentions'}`
      : null,
  ]
})

function refreshIcons(): void {
  const root = rootEl.value
  if (root && typeof window.prksRefreshIcons === 'function') window.prksRefreshIcons(root)
}

function onViewGraph(): void {
  if (concept.value) intents?.viewGraph(concept.value)
}
function onRename(): void {
  if (concept.value) void intents?.rename(concept.value)
}
function onDelete(): void {
  if (concept.value) void intents?.remove(concept.value)
}

watch(
  () =>
    [
      props.projection.generation,
      concept.value?.id,
      concept.value?.name,
      concept.value?.description,
      concept.value?.aliases,
      concept.value?.parents,
      concept.value?.children,
      concept.value?.mention_count,
    ] as const,
  () => {
    refreshIcons()
  },
  { flush: 'post' },
)
</script>

<template>
  <div ref="rootEl" data-prks-concept-detail-view>
    <template v-if="availability === 'unavailable'">
      <div class="prks-page-header page-header">
        <h2 class="prks-page-title">Concept not available offline</h2>
      </div>
      <PrksInlineMessage data-prks-role="offline-unavailable">
        This item is not available offline.
      </PrksInlineMessage>
    </template>
    <template v-else-if="availability === 'not-found' || !concept">
      <div class="prks-page-header page-header">
        <h2 class="prks-page-title">Concept not found.</h2>
      </div>
      <p class="meta-row">
        <PrksLinkButton href="#/concepts">Back to Concepts</PrksLinkButton>
      </p>
    </template>
    <template v-else-if="ready && concept">
      <div class="prks-page-header page-header">
        <div class="page-header__title-row">
          <div>
            <p class="saved-view-detail__kicker">Concept</p>
            <h2 class="prks-page-title">{{ concept.name || 'Concept' }}</h2>
            <PrksRelSummary :parts="summaryParts" />
          </div>
          <div class="page-header__actions">
            <PrksButton id="prks-concept-view-graph" @click="onViewGraph">
              View in graph
            </PrksButton>
            <PrksButton
              id="prks-concept-rename"
              :data-prks-role="MUTATION_ROLE"
              @click="onRename"
            >
              Rename
            </PrksButton>
            <PrksButton
              id="prks-concept-delete"
              variant="quiet-danger"
              class="prks-page-action--destructive"
              :data-prks-role="MUTATION_ROLE"
              @click="onDelete"
            >
              Delete
            </PrksButton>
          </div>
        </div>
      </div>
      <div class="research-entity">
        <section class="research-entity__section" aria-labelledby="prks-concept-def-h">
          <PrksResearchSectionHead
            title="Definition"
            heading-id="prks-concept-def-h"
            action-id="prks-concept-edit-def"
            action-label="Edit"
            :action-role="MUTATION_ROLE"
            @action="concept && intents?.editDefinition(concept)"
          />
          <div class="research-md" v-html="definitionHtml"></div>
        </section>

        <section class="research-entity__section" aria-labelledby="prks-concept-aliases-h">
          <PrksResearchSectionHead
            title="Search keys / aliases"
            heading-id="prks-concept-aliases-h"
            action-id="prks-concept-edit-aliases"
            action-label="Edit"
            :action-role="MUTATION_ROLE"
            :sub="aliasSub || undefined"
            @action="concept && intents?.editAliases(concept)"
          />
          <div v-if="concept.aliases.length" class="research-entity__chips">
            <span
              v-for="alias in concept.aliases"
              :key="alias"
              class="tag research-entity__alias-chip"
            >{{ alias }}</span>
          </div>
          <p v-else class="meta-row">No aliases.</p>
        </section>

        <section class="research-entity__section" aria-labelledby="prks-concept-parents-h">
          <PrksResearchSectionHead
            title="Parent concepts"
            heading-id="prks-concept-parents-h"
            action-id="prks-concept-edit-parents"
            action-label="Edit"
            :action-role="MUTATION_ROLE"
            :sub="parentSub || undefined"
            @action="concept && intents?.editParents(concept)"
          />
          <div v-if="concept.parents.length" class="list-view prks-research-index">
            <PrksResearchRow
              v-for="row in concept.parents"
              :key="row.id"
              :href="`#/concepts/${encodeURIComponent(row.id)}`"
              :title="row.name || row.id"
              kind="Concept"
            />
          </div>
          <p v-else class="meta-row">Top-level concept.</p>
        </section>

        <section class="research-entity__section" aria-labelledby="prks-concept-children-h">
          <PrksResearchSectionHead title="Subconcepts" heading-id="prks-concept-children-h" />
          <div v-if="concept.children.length" class="list-view prks-research-index">
            <PrksResearchRow
              v-for="row in concept.children"
              :key="row.id"
              :href="`#/concepts/${encodeURIComponent(row.id)}`"
              :title="row.name || row.id"
              kind="Concept"
            />
          </div>
          <p v-else class="meta-row">No subconcepts.</p>
        </section>

        <section class="research-entity__section" aria-labelledby="prks-concept-mentions-h">
          <PrksResearchSectionHead
            title="Mentioned in research notes"
            heading-id="prks-concept-mentions-h"
            :count="mentionCount"
          />
          <div v-if="concept.mentions.length" class="research-entity__mentions">
            <div
              v-for="mention in concept.mentions"
              :key="mention.work_id"
              class="research-entity__mention"
            >
              <a
                class="research-entity__mention-title"
                :href="`#/works/${encodeURIComponent(mention.work_id)}`"
              >{{ mention.title || mention.work_id }}</a>
              <p
                v-for="(occ, idx) in mention.occurrences || []"
                :key="idx"
                class="research-entity__mention-snippet meta-row"
              >…{{ occ.snippet || '' }}…</p>
            </div>
          </div>
          <p v-else class="meta-row">No research-note references.</p>
        </section>
      </div>
    </template>
  </div>
</template>
