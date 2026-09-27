<script setup lang="ts">
import { computed, inject, onMounted, ref, watch } from 'vue'
import { conceptIntentsKey } from './intents'
import { researchMarkdownHtml } from './markdown'
import type { ConceptDetailProjection } from './projection'

const MUTATION_ROLE = 'concept-mutation-control'

const props = defineProps<{
  projection: ConceptDetailProjection
}>()

const intents = inject(conceptIntentsKey)
const rootEl = ref<HTMLElement | null>(null)
const summaryHost = ref<HTMLElement | null>(null)
const definitionHost = ref<HTMLElement | null>(null)

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

function paintSummary(): void {
  const host = summaryHost.value
  const c = concept.value
  if (!host || !c) return
  if (typeof window.prksRelSummaryHtml !== 'function') {
    host.innerHTML = ''
    return
  }
  const parentCount = c.parents.length
  host.innerHTML = window.prksRelSummaryHtml({
    parts: [
      parentCount ? `${parentCount} ${parentCount === 1 ? 'parent' : 'parents'}` : null,
      Number(c.mention_count) > 0
        ? `${Number(c.mention_count)} ${
            Number(c.mention_count) === 1 ? 'note mention' : 'note mentions'
          }`
        : null,
    ],
  })
}

function paintDefinition(): void {
  const host = definitionHost.value
  if (!host) return
  host.innerHTML = definitionHtml.value
}

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
function onEditDef(): void {
  if (concept.value) void intents?.editDefinition(concept.value)
}
function onEditAliases(): void {
  if (concept.value) void intents?.editAliases(concept.value)
}
function onEditParents(): void {
  if (concept.value) void intents?.editParents(concept.value)
}

onMounted(() => {
  paintSummary()
  paintDefinition()
  refreshIcons()
})

watch(
  () => [props.projection.generation, concept.value?.id, concept.value?.name, concept.value?.description] as const,
  () => {
    paintSummary()
    paintDefinition()
    refreshIcons()
  },
)
</script>

<template>
  <div ref="rootEl" data-prks-concept-detail-view>
    <template v-if="availability === 'unavailable'">
      <div class="prks-page-header page-header">
        <h2 class="prks-page-title">Concept not available offline</h2>
      </div>
      <p class="prks-inline-message" data-prks-role="offline-unavailable">
        This item is not available offline.
      </p>
    </template>
    <template v-else-if="availability === 'not-found' || !concept">
      <div class="prks-page-header page-header">
        <h2 class="prks-page-title">Concept not found.</h2>
      </div>
      <p class="meta-row">
        <a class="prks-btn prks-btn--secondary" href="#/concepts">Back to Concepts</a>
      </p>
    </template>
    <template v-else-if="ready && concept">
      <div class="prks-page-header page-header">
        <div class="page-header__title-row">
          <div>
            <p class="saved-view-detail__kicker">Concept</p>
            <h2 class="prks-page-title">{{ concept.name || 'Concept' }}</h2>
            <div ref="summaryHost"></div>
          </div>
          <div class="page-header__actions">
            <button
              type="button"
              class="prks-btn prks-btn--secondary"
              id="prks-concept-view-graph"
              @click="onViewGraph"
            >
              View in graph
            </button>
            <button
              type="button"
              class="prks-btn prks-btn--secondary"
              id="prks-concept-rename"
              :data-prks-role="MUTATION_ROLE"
              @click="onRename"
            >
              Rename
            </button>
            <button
              type="button"
              class="prks-btn prks-btn--quiet-danger prks-page-action--destructive"
              id="prks-concept-delete"
              :data-prks-role="MUTATION_ROLE"
              @click="onDelete"
            >
              Delete
            </button>
          </div>
        </div>
      </div>
      <div class="research-entity">
        <section class="research-entity__section" aria-labelledby="prks-concept-def-h">
          <div class="research-entity__section-head">
            <h3 id="prks-concept-def-h">Definition</h3>
            <div class="research-entity__section-head-actions">
              <button
                type="button"
                class="prks-btn prks-btn--secondary prks-btn--sm"
                id="prks-concept-edit-def"
                :data-prks-role="MUTATION_ROLE"
                @click="onEditDef"
              >
                Edit
              </button>
            </div>
          </div>
          <div ref="definitionHost" class="research-md"></div>
        </section>

        <section class="research-entity__section" aria-labelledby="prks-concept-aliases-h">
          <div class="research-entity__section-head">
            <h3 id="prks-concept-aliases-h">Search keys / aliases</h3>
            <div class="research-entity__section-head-actions">
              <button
                type="button"
                class="prks-btn prks-btn--secondary prks-btn--sm"
                id="prks-concept-edit-aliases"
                :data-prks-role="MUTATION_ROLE"
                @click="onEditAliases"
              >
                Edit
              </button>
            </div>
          </div>
          <p v-if="aliasSub" class="research-entity__section-sub meta-row">{{ aliasSub }}</p>
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
          <div class="research-entity__section-head">
            <h3 id="prks-concept-parents-h">Parent concepts</h3>
            <div class="research-entity__section-head-actions">
              <button
                type="button"
                class="prks-btn prks-btn--secondary prks-btn--sm"
                id="prks-concept-edit-parents"
                :data-prks-role="MUTATION_ROLE"
                @click="onEditParents"
              >
                Edit
              </button>
            </div>
          </div>
          <p v-if="parentSub" class="research-entity__section-sub meta-row">{{ parentSub }}</p>
          <div v-if="concept.parents.length" class="list-view prks-research-index">
            <a
              v-for="parent in concept.parents"
              :key="parent.id"
              class="prks-list-row prks-research-row"
              :href="`#/concepts/${encodeURIComponent(parent.id)}`"
            >
              <span class="prks-research-row__body">
                <span class="prks-research-row__title-line">
                  <span class="prks-research-row__title">{{ parent.name || parent.id }}</span>
                  <span class="prks-research-row__kind">Concept</span>
                </span>
              </span>
            </a>
          </div>
          <p v-else class="meta-row">Top-level concept.</p>
        </section>

        <section class="research-entity__section" aria-labelledby="prks-concept-children-h">
          <div class="research-entity__section-head">
            <h3 id="prks-concept-children-h">Subconcepts</h3>
          </div>
          <div v-if="concept.children.length" class="list-view prks-research-index">
            <a
              v-for="child in concept.children"
              :key="child.id"
              class="prks-list-row prks-research-row"
              :href="`#/concepts/${encodeURIComponent(child.id)}`"
            >
              <span class="prks-research-row__body">
                <span class="prks-research-row__title-line">
                  <span class="prks-research-row__title">{{ child.name || child.id }}</span>
                  <span class="prks-research-row__kind">Concept</span>
                </span>
              </span>
            </a>
          </div>
          <p v-else class="meta-row">No subconcepts.</p>
        </section>

        <section class="research-entity__section" aria-labelledby="prks-concept-mentions-h">
          <div class="research-entity__section-head">
            <h3 id="prks-concept-mentions-h">Mentioned in research notes</h3>
            <div class="research-entity__section-head-actions">
              <span class="research-entity__section-count">{{ mentionCount }}</span>
            </div>
          </div>
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
