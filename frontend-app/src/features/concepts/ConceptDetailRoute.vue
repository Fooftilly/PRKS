<script setup lang="ts">
import { computed, inject, onMounted, ref, watch } from 'vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
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
const definitionHeadHost = ref<HTMLElement | null>(null)
const definitionHost = ref<HTMLElement | null>(null)
const aliasesHeadHost = ref<HTMLElement | null>(null)
const parentsHeadHost = ref<HTMLElement | null>(null)
const childrenHeadHost = ref<HTMLElement | null>(null)
const parentsHost = ref<HTMLElement | null>(null)
const childrenHost = ref<HTMLElement | null>(null)
const mentionsHeadHost = ref<HTMLElement | null>(null)

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

function escHtml(value: string): string {
  const fn = window.prksEscapeHtml
  if (typeof fn === 'function') return fn(value)
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/** DESIGN.md: relationship rows via shared `prksResearchIndexRowHtml`. */
function researchRelationRowsHtml(
  rows: readonly { id: string; name: string }[],
): string {
  const rowHtml = window.prksResearchIndexRowHtml
  if (typeof rowHtml !== 'function') return ''
  return rows
    .map((row) =>
      rowHtml({
        href: `#/concepts/${encodeURIComponent(row.id)}`,
        title: escHtml(row.name || row.id),
        kind: 'Concept',
      }),
    )
    .join('')
}

/** DESIGN.md: section heads via shared `prksResearchSectionHeadHtml`. */
function paintSectionHead(
  host: HTMLElement | null,
  title: string,
  opts: {
    headingId?: string
    actionId?: string
    actionLabel?: string
    actionRole?: string
    count?: number
    sub?: string
  },
  onAction?: () => void,
): void {
  if (!host) return
  const fn = window.prksResearchSectionHeadHtml
  if (typeof fn !== 'function') {
    host.innerHTML = ''
    return
  }
  host.innerHTML = fn(title, opts)
  if (opts.actionId && onAction) {
    // Action ids are static PRKS tokens (hyphenated); avoid CSS.escape for jsdom.
    const btn = host.querySelector(`[id="${opts.actionId}"]`)
    if (btn instanceof HTMLElement) {
      btn.addEventListener('click', onAction)
    }
  }
}

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

function paintRelations(): void {
  const c = concept.value
  const parents = parentsHost.value
  const children = childrenHost.value
  if (parents) {
    parents.innerHTML = c && c.parents.length ? researchRelationRowsHtml(c.parents) : ''
  }
  if (children) {
    children.innerHTML = c && c.children.length ? researchRelationRowsHtml(c.children) : ''
  }
}

function paintSectionHeads(): void {
  paintSectionHead(
    definitionHeadHost.value,
    'Definition',
    {
      headingId: 'prks-concept-def-h',
      actionId: 'prks-concept-edit-def',
      actionLabel: 'Edit',
      actionRole: MUTATION_ROLE,
    },
    () => {
      if (concept.value) void intents?.editDefinition(concept.value)
    },
  )
  paintSectionHead(
    aliasesHeadHost.value,
    'Search keys / aliases',
    {
      headingId: 'prks-concept-aliases-h',
      actionId: 'prks-concept-edit-aliases',
      actionLabel: 'Edit',
      actionRole: MUTATION_ROLE,
      sub: aliasSub.value || undefined,
    },
    () => {
      if (concept.value) void intents?.editAliases(concept.value)
    },
  )
  paintSectionHead(
    parentsHeadHost.value,
    'Parent concepts',
    {
      headingId: 'prks-concept-parents-h',
      actionId: 'prks-concept-edit-parents',
      actionLabel: 'Edit',
      actionRole: MUTATION_ROLE,
      sub: parentSub.value || undefined,
    },
    () => {
      if (concept.value) void intents?.editParents(concept.value)
    },
  )
  paintSectionHead(childrenHeadHost.value, 'Subconcepts', {
    headingId: 'prks-concept-children-h',
  })
  paintSectionHead(mentionsHeadHost.value, 'Mentioned in research notes', {
    headingId: 'prks-concept-mentions-h',
    count: mentionCount.value,
  })
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

function paintAll(): void {
  paintSummary()
  paintSectionHeads()
  paintDefinition()
  paintRelations()
  refreshIcons()
}

onMounted(() => {
  paintAll()
})

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
    paintAll()
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
          <div ref="definitionHeadHost"></div>
          <div ref="definitionHost" class="research-md"></div>
        </section>

        <section class="research-entity__section" aria-labelledby="prks-concept-aliases-h">
          <div ref="aliasesHeadHost"></div>
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
          <div ref="parentsHeadHost"></div>
          <div
            v-if="concept.parents.length"
            ref="parentsHost"
            class="list-view prks-research-index"
          ></div>
          <p v-else class="meta-row">Top-level concept.</p>
        </section>

        <section class="research-entity__section" aria-labelledby="prks-concept-children-h">
          <div ref="childrenHeadHost"></div>
          <div
            v-if="concept.children.length"
            ref="childrenHost"
            class="list-view prks-research-index"
          ></div>
          <p v-else class="meta-row">No subconcepts.</p>
        </section>

        <section class="research-entity__section" aria-labelledby="prks-concept-mentions-h">
          <div ref="mentionsHeadHost"></div>
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
