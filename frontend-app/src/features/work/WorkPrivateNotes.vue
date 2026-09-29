<script setup lang="ts">
import { onMounted, ref } from 'vue'
import type { WorkPrivateNoteOwner } from './private-note-session'

const props = defineProps<{
  workId: string
  initialText: string
  owner: WorkPrivateNoteOwner
}>()

const field = ref<HTMLTextAreaElement | null>(null)

onMounted(() => {
  if (field.value) field.value.value = props.initialText
  window.prksBindPrivateNotesField?.('work', props.workId, props.owner)
})
</script>

<template>
  <div class="doc-meta-card prks-private-notes-card">
    <h3 class="prks-private-notes-card__head">
      <span class="prks-private-notes-card__head-text">Reminders</span>
      <button
        type="button"
        class="prks-hint-btn prks-private-notes-card__hint-btn"
        data-prks-hint-type="notes-private-file"
        aria-label="About reminders"
        aria-expanded="false"
        aria-controls="prks-hint-popover"
      >?</button>
    </h3>
    <textarea
      :id="`prks-private-notes-work-${workId}`"
      ref="field"
      class="prks-private-notes-input"
      rows="4"
      maxlength="8000"
      spellcheck="true"
      data-prks-notes-entity="work"
      :data-prks-notes-id="workId"
      placeholder="e.g. Need this for…"
    ></textarea>
    <p
      :id="`prks-private-notes-status-work-${workId}`"
      class="meta-row prks-private-notes-status"
      aria-live="polite"
    ></p>
  </div>
</template>
