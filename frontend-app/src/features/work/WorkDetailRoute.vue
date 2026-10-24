<script setup lang="ts">
import WorkMainSurface from './WorkMainSurface.vue'
import type { WorkMainSurfaceModel } from './main-surface'

defineProps<{
  availability: 'ready' | 'not-found'
  surface: WorkMainSurfaceModel | null
}>()
</script>

<template>
  <p
    v-if="availability !== 'ready' || !surface"
    class="prks-inline-message prks-inline-message--error"
  >File not found.</p>
  <WorkMainSurface
    v-else
    :show-header="surface.showHeader"
    :title="surface.title"
    :doc-type-html="surface.docTypeHtml"
    :rel-summary-parts="surface.relSummaryParts"
    :work-id="surface.workId"
    :kind="surface.kind"
    :has-file="surface.hasFile"
    :viewer-html="surface.viewerHtml"
    :editor-region-id="surface.editorRegionId"
  />
</template>
