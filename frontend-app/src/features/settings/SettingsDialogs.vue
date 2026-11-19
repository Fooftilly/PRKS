<script setup lang="ts">
import ModalDialog from '../../modal/ModalDialog.vue'
import RestoreConfirmForm from './RestoreConfirmForm.vue'
</script>

<template>
  <!--
    Settings' own dialogs, rendered inside ModalHost (#1036). Opening one
    hides the classic Settings shell, as openModal does for every dialog.
  -->
  <ModalDialog id="prks-restore-confirm-modal" title="Replace library?">
    <RestoreConfirmForm />
  </ModalDialog>
</template>
