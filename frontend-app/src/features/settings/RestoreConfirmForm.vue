<script setup lang="ts">
import { computed } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import { useDialogLifecycle } from '../../modal/dialogs'
import { focusField } from '../create-forms/keyboard'
import { confirmReady, requestRestore, resetRestoreConfirm, restoreSession } from './restore-session'

/**
 * Body of the "Replace library?" dialog (#1036). Typing RESTORE enables the
 * restore; the Backup panel runs it and reports here and in its own status.
 */
const ready = computed(() => confirmReady())

function onSubmit(): void {
  void requestRestore()
}

useDialogLifecycle({
  opened: resetRestoreConfirm,
  focusInitial: () => focusField('prks-restore-confirm-input'),
})
</script>

<template>
  <p class="prks-settings-hint">Restoring will replace the current PRKS library with the contents of this backup.</p>
  <p class="prks-settings-hint">Before restoring, download a backup of the current library if you may want to return to it.</p>
  <label for="prks-restore-confirm-input" class="prks-settings-section__title">Type RESTORE to confirm</label>
  <input
    id="prks-restore-confirm-input"
    v-model="restoreSession.confirmText"
    type="text"
    class="prks-settings-text-input"
    autocomplete="off"
    spellcheck="false"
    placeholder="RESTORE"
    :disabled="restoreSession.busy"
  >
  <p id="prks-restore-confirm-status" class="prks-settings-hint" aria-live="polite">{{ restoreSession.confirmStatus }}</p>
  <PrksButton id="prks-restore-confirm-submit" :disabled="!ready" @click="onSubmit">Restore this backup</PrksButton>
</template>
