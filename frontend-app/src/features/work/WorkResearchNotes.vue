<script setup lang="ts">
import { onMounted, ref } from 'vue'

const props = defineProps<{
  initialText: string
  editorRegionId: string
}>()

const field = ref<HTMLTextAreaElement | null>(null)

onMounted(() => {
  if (field.value) field.value.value = props.initialText
})
</script>

<template>
  <div class="work-notes-pane">
    <div class="work-notes-pane-header">
      <h3 class="work-notes-title">Research Notes</h3>
      <div class="work-notes-pane-header-actions">
        <button
          type="button"
          class="work-notes-toggle-btn"
          data-prks-role="work-notes-collapse-btn"
          aria-expanded="true"
          :aria-controls="editorRegionId"
          aria-label="Collapse research notes editor"
          title="Collapse notes"
        >
          <span class="work-notes-toggle-btn__icon" aria-hidden="true">
            <svg class="work-notes-toggle-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2.65" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="6.5 13 12 19 17.5 13" />
              <polyline points="6.5 6 12 12 17.5 6" />
            </svg>
          </span>
        </button>
        <div data-prks-role="annotation-sync-status" class="work-annotation-sync-status work-annotation-sync-status--hidden" aria-live="polite"></div>
        <div data-prks-role="editor-status" class="work-editor-status"></div>
      </div>
    </div>
    <div class="work-notes-editor-wrap" data-prks-role="work-notes-editor-region" :id="editorRegionId">
      <textarea ref="field" data-prks-role="research-notes-editor" :value="initialText"></textarea>
    </div>
  </div>
</template>
