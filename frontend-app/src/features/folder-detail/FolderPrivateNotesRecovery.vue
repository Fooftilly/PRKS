<script setup lang="ts">
/**
 * The recovery notice of the Folder Reminders card (#534). The card itself is
 * classic HTML from `ui.js`; only this notice is Vue, on the shared presenter.
 */
import EditorRecoveryNotice from '../editor-recovery/EditorRecoveryNotice.vue'
import type { RecoveryOwner } from '../editor-recovery/recovery-presenter'
import { folderPrivateNotesRecoveryView, openFolderPrivateNotesRecoveryReview } from './private-notes-recovery'

const props = defineProps<{ owner: RecoveryOwner }>()

const recoveryView = folderPrivateNotesRecoveryView(props.owner)
</script>

<template>
  <EditorRecoveryNotice
    :view="recoveryView"
    subject="Reminders"
    @review="(opener) => openFolderPrivateNotesRecoveryReview(owner, opener)"
  />
</template>
