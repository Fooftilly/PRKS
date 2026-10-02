<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { ResearchGraphChromeIds } from './session'

const props = defineProps<{
  owner: object
  includePeople?: boolean
  chrome?: ResearchGraphChromeIds
}>()

const rootEl = ref<HTMLElement | null>(null)
const headerIcon = computed(() => window.prksPageHeaderIconHtml?.('share-2') ?? '')

function legendIcon(name: string): string {
  return window.prksIcon?.(name, { size: 'sm' }) ?? ''
}

onMounted(() => {
  window.prksRefreshIcons?.(rootEl.value)
})

onBeforeUnmount(() => {
  window.prksReleaseResearchGraph?.(props.owner)
})
</script>

<template>
  <div ref="rootEl" class="research-graph" data-prks-research-graph data-prks-research-graph-page>
    <div class="prks-page-header page-header">
      <div class="page-header__title-row">
        <h2 class="prks-page-title">
          <span v-if="headerIcon" class="work-html-slot" v-html="headerIcon"></span>
          Research Graph
        </h2>
      </div>
    </div>
    <div class="prks-toolbar research-graph__toolbar">
      <label
        class="research-graph__find-label"
        :for="chrome?.findId || undefined"
      >Find node</label>
      <input
        :id="chrome?.findId || undefined"
        class="prks-input"
        type="search"
        autocomplete="off"
        placeholder="Find node…"
        data-prks-role="graph-find"
        :aria-controls="chrome?.resultsId || undefined"
      >
      <button type="button" class="prks-btn prks-btn--secondary" data-prks-role="graph-fit">Fit</button>
      <button type="button" class="prks-btn prks-btn--secondary" data-prks-role="graph-reset">Reset layout</button>
      <button
        type="button"
        class="prks-btn prks-btn--secondary"
        data-prks-role="graph-filters-toggle"
        aria-expanded="false"
        :aria-controls="chrome?.filtersPanelId || undefined"
      >Filters</button>
      <button
        type="button"
        class="prks-btn prks-btn--secondary"
        data-prks-role="graph-legend-toggle"
        aria-expanded="false"
        :aria-controls="chrome?.legendPanelId || undefined"
      >Legend</button>
    </div>
    <div
      :id="chrome?.resultsId || undefined"
      class="research-graph__find-results"
      role="listbox"
      hidden
      data-prks-role="graph-find-results"
    ></div>
    <div
      class="research-graph__status prks-inline-message"
      role="status"
      data-prks-role="graph-status"
      hidden
    ></div>
    <div
      :id="chrome?.filtersPanelId || undefined"
      class="research-graph__aux-panel research-graph__filters-panel"
      data-prks-role="graph-filters-panel"
      hidden
    >
      <div class="research-graph__filters">
        <span class="research-graph__filter-group">Nodes</span>
        <label class="prks-filter-toggle"><input type="checkbox" data-graph-filter="concepts" checked> <span>Concepts</span></label>
        <label class="prks-filter-toggle"><input type="checkbox" data-graph-filter="positions" checked> <span>Positions</span></label>
        <label class="prks-filter-toggle"><input type="checkbox" data-graph-filter="arguments" checked> <span>Arguments</span></label>
        <label class="prks-filter-toggle"><input type="checkbox" data-graph-filter="works" checked> <span>Works</span></label>
        <label class="prks-filter-toggle"><input type="checkbox" data-graph-filter="people" :checked="includePeople"> <span>People</span></label>
      </div>
      <div class="research-graph__filters">
        <span class="research-graph__filter-group">Relations</span>
        <label class="prks-filter-toggle"><input type="checkbox" data-graph-filter="hierarchy" checked> <span>Hierarchy</span></label>
        <label class="prks-filter-toggle"><input type="checkbox" data-graph-filter="responds" checked> <span>Responses</span></label>
        <label class="prks-filter-toggle"><input type="checkbox" data-graph-filter="sources" checked> <span>Sources</span></label>
        <label class="prks-filter-toggle"><input type="checkbox" data-graph-filter="mentions" checked> <span>Note mentions</span></label>
      </div>
    </div>
    <div
      :id="chrome?.legendPanelId || undefined"
      class="research-graph__aux-panel research-graph__legend"
      data-prks-role="graph-legend-panel"
      aria-label="Graph legend"
      hidden
    >
      <div class="research-graph__legend-group">
        <span class="research-graph__filter-group">Nodes</span>
        <ul>
          <li><span class="research-graph__legend-icon research-graph__legend-icon--concept" aria-hidden="true"><span class="work-html-slot" v-html="legendIcon('network')"></span></span> Concept</li>
          <li><span class="research-graph__legend-icon research-graph__legend-icon--position" aria-hidden="true"><span class="work-html-slot" v-html="legendIcon('flag')"></span></span> Position</li>
          <li><span class="research-graph__legend-icon research-graph__legend-icon--argument" aria-hidden="true"><span class="work-html-slot" v-html="legendIcon('messages-square')"></span></span> Argument</li>
          <li><span class="research-graph__legend-icon research-graph__legend-icon--stance" aria-hidden="true"><span class="work-html-slot" v-html="legendIcon('messages-square')"></span></span> Stance</li>
          <li><span class="research-graph__legend-icon research-graph__legend-icon--work" aria-hidden="true"><span class="work-html-slot" v-html="legendIcon('file-text')"></span></span> Work</li>
          <li><span class="research-graph__legend-icon research-graph__legend-icon--person" aria-hidden="true"><span class="work-html-slot" v-html="legendIcon('user')"></span></span> Person</li>
        </ul>
      </div>
      <div class="research-graph__legend-group">
        <span class="research-graph__filter-group">Relations</span>
        <ul>
          <li><span class="research-graph__line research-graph__line--hierarchy"></span> Hierarchy</li>
          <li><span class="research-graph__line research-graph__line--responds"></span> Response</li>
          <li><span class="research-graph__line research-graph__line--source"></span> Source</li>
          <li><span class="research-graph__line research-graph__line--mentions"></span> Note mention</li>
          <li><span class="research-graph__line research-graph__line--author"></span> Author</li>
        </ul>
      </div>
    </div>
    <div data-prks-role="graph-body">
      <div class="research-graph__stage" data-prks-role="graph-stage">
        <div class="prks-panel research-graph__canvas-wrap">
          <div
            class="research-graph__canvas"
            data-prks-role="graph-canvas"
            role="img"
            aria-label="Research relationship graph"
          ></div>
        </div>
      </div>
      <p class="meta-row" role="status" data-prks-role="graph-derived-off" hidden>
        Note-mention edges unavailable. Canonical relationships still shown.
      </p>
    </div>
  </div>
</template>
