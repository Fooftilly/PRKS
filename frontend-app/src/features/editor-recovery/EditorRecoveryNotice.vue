<script setup lang="ts">
/**
 * One compact in-flow notice inside an editor pane: recovery drafts are
 * waiting for review, and/or this pane's newest text has no recovery copy.
 * Hiding it only hides it until the pane mounts again; nothing is discarded.
 * The unprotected warning cannot be hidden while it is true.
 */
import { computed, ref, watch } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import { noticeText, unprotectedText } from './labels'
import type { RecoveryNoticeView } from './types'

const props = defineProps<{
  view: RecoveryNoticeView | null
  /** What the drafts are, e.g. "Research Notes". */
  subject: string
}>()

const emit = defineEmits<{
  review: [opener: HTMLElement | null]
}>()

const hidden = ref(false)
const draftsText = computed(() => (props.view && props.view.drafts ? noticeText(props.view, props.subject) : ''))
const warningText = computed(() => (props.view && props.view.unprotected ? unprotectedText(props.view.unprotected) : ''))

// A different Work in this pane is a different notice.
watch(
  () => (props.view ? props.view.workId : ''),
  () => {
    hidden.value = false
  },
)

function onReview(event: MouseEvent): void {
  emit('review', event.currentTarget instanceof HTMLElement ? event.currentTarget : null)
}
</script>

<template>
  <div v-if="(draftsText && !hidden) || warningText" class="editor-recovery-notice" data-prks-role="editor-recovery-notice">
    <PrksInlineMessage
      v-if="warningText"
      tone="warning"
      status
      class="editor-recovery-notice__message"
      data-prks-role="editor-recovery-unprotected"
    >
      {{ warningText }}
    </PrksInlineMessage>
    <div v-if="draftsText && !hidden" class="editor-recovery-notice__row" data-prks-role="editor-recovery-drafts">
      <PrksInlineMessage status class="editor-recovery-notice__message">{{ draftsText }}</PrksInlineMessage>
      <PrksButton size="sm" variant="secondary" data-prks-role="editor-recovery-open-review" @click="onReview">Review</PrksButton>
      <PrksButton size="sm" variant="ghost" data-prks-role="editor-recovery-hide" @click="hidden = true">Hide</PrksButton>
    </div>
  </div>
</template>
