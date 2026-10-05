<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  showHeader: boolean
  title: string
  docTypeHtml: string
  relSummaryHtml: string
  workId: string
  kind: 'pdf' | 'video' | 'empty'
  hasFile: boolean
  viewerHtml: string
  editorRegionId: string
}>()

const notesEditorId = computed(() => `${props.editorRegionId}-field`)
</script>

<template>
  <div class="work-detail">
    <div v-if="showHeader" class="prks-page-header page-header page-header--work">
      <div class="card-heading-row card-heading-row--wrap">
        <h2 class="page-header--work-title">{{ title }}</h2>
        <span data-prks-role="work-header-doc-type-slot" v-html="docTypeHtml"></span>
      </div>
      <div v-if="relSummaryHtml" class="work-html-slot" v-html="relSummaryHtml"></div>
    </div>
    <div class="document-view document-view--work">
      <div class="work-main-column">
        <div class="work-workspace" :data-work-id="workId">
          <div v-if="kind === 'pdf' && hasFile" class="work-pdf-pane">
            <div data-prks-role="pdf-viewer"></div>
            <div data-prks-role="pdf-annotation-popup-host"></div>
          </div>
          <div v-else-if="kind === 'pdf'" class="work-pdf-pane work-pdf-pane--empty">
            <p class="work-pdf-empty">No PDF file attached.</p>
          </div>
          <div v-else-if="kind === 'video'" class="work-viewer-host" v-html="viewerHtml"></div>
          <div v-else class="work-pdf-pane work-pdf-pane--empty">
            <p class="work-pdf-empty">No file attached.</p>
          </div>
          <div
            class="work-split-handle"
            role="separator"
            aria-label="Resize between document and research notes"
            tabindex="0"
          >
            <span class="work-split-handle-grip" aria-hidden="true"></span>
          </div>
          <div data-prks-role="work-research-notes-anchor">
            <div class="work-notes-pane">
              <div class="work-notes-pane-header">
                <h3 class="work-notes-title">
                  <label :for="notesEditorId">Research Notes</label>
                </h3>
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
                <textarea
                  :id="notesEditorId"
                  data-prks-role="research-notes-editor"
                  aria-label="Research Notes"
                ></textarea>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
