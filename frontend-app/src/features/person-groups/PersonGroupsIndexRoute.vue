<script setup lang="ts">
import { computed, inject, ref, watch } from 'vue'
import { personGroupIntentsKey } from './intents'
import { buildGroupTree, collapsibleGroupIds } from './projection'
import type { PersonGroupsIndexProjection } from './projection'
import PersonGroupTreeNode from './PersonGroupTreeNode.vue'

const props = defineProps<{
  projection: PersonGroupsIndexProjection
}>()

const intents = inject(personGroupIntentsKey)
const chrome = intents?.indexChrome() ?? { query: '', expandedIds: [] as string[] }
const query = ref(chrome.query)
const expandedIds = ref(new Set(chrome.expandedIds))

watch([query, expandedIds], () => {
  intents?.writeIndexChrome(query.value, [...expandedIds.value])
}, { flush: 'sync' })

const unavailable = computed(() => props.projection.availability === 'unavailable')
const groups = computed(() => props.projection.groups)
const tree = computed(() => buildGroupTree(groups.value, query.value, expandedIds.value))
const filtering = computed(() => !!query.value.trim())

function icon(name: string): string {
  return typeof window.prksIcon === 'function' ? window.prksIcon(name, { size: 'sm' }) : ''
}

function onCreate(): void {
  intents?.create()
}

function onToggle(groupId: string): void {
  if (filtering.value) return
  const next = new Set(expandedIds.value)
  if (next.has(groupId)) next.delete(groupId)
  else next.add(groupId)
  expandedIds.value = next
}

function onToggleAll(): void {
  if (filtering.value) return
  expandedIds.value = tree.value.allCollapsed
    ? new Set(collapsibleGroupIds(groups.value))
    : new Set()
}
</script>

<template>
  <div data-prks-person-groups-index-view>
    <template v-if="unavailable">
      <div class="prks-page-header page-header">
        <h2 class="prks-page-title">Person Groups not available offline</h2>
      </div>
      <p class="prks-inline-message" data-prks-role="offline-unavailable">This item is not available offline.</p>
    </template>
    <div v-else class="prks-group-library">
      <div class="prks-page-header page-header prks-group-library__header page-header--split">
        <h2 class="prks-page-title">People groups</h2>
        <button
          type="button"
          class="prks-btn prks-btn--secondary"
          data-prks-role="group-mutation-control"
          @click="onCreate"
        >
          <span v-html="icon('plus')"></span>
          New group
        </button>
      </div>
      <p class="meta-row prks-group-library__intro">
        Organize people into hierarchical groups. A person can belong to multiple groups.
      </p>
      <div v-if="groups.length" class="prks-group-library__toolbar">
        <div class="tag-add-shell tag-add-shell--flush prks-group-library__search">
          <div class="tag-add-shell__field">
            <input
              id="prks-group-library-search"
              v-model="query"
              type="text"
              class="tag-add-shell__input"
              placeholder="Search groups…"
              maxlength="300"
              autocomplete="off"
              aria-label="Filter groups"
            />
            <button
              id="prks-group-library-search-clear"
              type="button"
              class="tag-add-shell__clear"
              aria-label="Clear search"
              title="Clear search"
              :hidden="!query.trim()"
              @click="query = ''"
            >
              &times;
            </button>
          </div>
        </div>
        <div v-if="tree.hasCollapsible" class="prks-group-library__toolbar-actions">
          <button
            id="prks-group-library-expand-toggle"
            type="button"
            class="prks-btn prks-btn--secondary prks-group-library__toolbar-btn"
            :class="{ 'is-collapse-all': !tree.allCollapsed }"
            :aria-label="tree.allCollapsed ? 'Expand all' : 'Collapse all'"
            :title="tree.allCollapsed ? 'Expand all' : 'Collapse all'"
            :hidden="filtering"
            :disabled="filtering"
            @click="onToggleAll"
          >
            <span class="ribbon-btn__icon" v-html="icon('chevronDown')"></span>
          </button>
        </div>
      </div>
      <div v-if="!groups.length" class="prks-group-library__empty-state">
        <p class="prks-inline-message prks-group-library__empty">No Person Groups yet.</p>
        <button type="button" class="prks-btn prks-btn--primary" data-prks-role="group-mutation-control" @click="onCreate">
          New Group
        </button>
      </div>
      <div v-else class="prks-group-library__scroll" data-prks-group-tree-host>
        <p v-if="tree.emptySearch" class="prks-inline-message prks-group-tree__empty">No groups match your search.</p>
        <div v-else class="prks-group-tree" role="tree">
          <PersonGroupTreeNode
            v-for="node in tree.nodes"
            :key="node.id"
            :node="node"
            :filtering="filtering"
            @toggle="onToggle"
          />
        </div>
      </div>
    </div>
  </div>
</template>
