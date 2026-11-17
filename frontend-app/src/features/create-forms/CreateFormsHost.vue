<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import FolderCreateForm from './FolderCreateForm.vue'
import GroupCreateForm from './GroupCreateForm.vue'
import NewFileForm from './NewFileForm.vue'
import PersonCreateForm from './PersonCreateForm.vue'
import PlaylistCreateForm from './PlaylistCreateForm.vue'
import { snapshotModalFields } from './snapshot'
import { CREATE_FORM_MODAL_IDS, type PrksCreateFormsApi } from './types'

const OWNED = new Set<string>(CREATE_FORM_MODAL_IDS)
const baselines = new Map<string, string>()

const folderRef = ref<InstanceType<typeof FolderCreateForm> | null>(null)
const personRef = ref<InstanceType<typeof PersonCreateForm> | null>(null)
const groupRef = ref<InstanceType<typeof GroupCreateForm> | null>(null)
const playlistRef = ref<InstanceType<typeof PlaylistCreateForm> | null>(null)
const newFileRef = ref<InstanceType<typeof NewFileForm> | null>(null)

function modalRoot(modalId: string): HTMLElement | null {
  const el = document.getElementById(modalId)
  return el instanceof HTMLElement ? el : null
}

function refreshFolderValidation(): Promise<void> {
  return folderRef.value?.refreshValidation() ?? Promise.resolve()
}

const api: PrksCreateFormsApi = {
  owns(modalId) {
    return OWNED.has(modalId)
  },
  isDirty(modalId) {
    if (!OWNED.has(modalId) || !baselines.has(modalId)) return false
    const root = modalRoot(modalId)
    if (!root) return false
    return snapshotModalFields(root) !== baselines.get(modalId)
  },
  captureBaseline(modalId) {
    if (!OWNED.has(modalId)) return
    const root = modalRoot(modalId)
    if (!root) return
    baselines.set(modalId, snapshotModalFields(root))
  },
  resetBaselines() {
    baselines.clear()
  },
  releaseFolderSave() {
    folderRef.value?.releaseSave()
  },
  refreshFolderValidation,
  prepareGroup() {
    return groupRef.value?.prepare() ?? Promise.resolve()
  },
  resetPlaylistFields() {
    playlistRef.value?.resetFields()
  },
  afterPersonReset() {
    personRef.value?.afterReset()
  },
  prepareNewFile(stillOpening) {
    return newFileRef.value?.prepare(stillOpening) ?? Promise.resolve()
  },
  focusInitial(modalId) {
    if (modalId === 'folder-modal') folderRef.value?.focusInitial()
    else if (modalId === 'person-modal') personRef.value?.focusInitial()
    else if (modalId === 'group-modal') groupRef.value?.focusInitial()
    else if (modalId === 'playlist-modal') playlistRef.value?.focusInitial()
    else if (modalId === 'work-modal') newFileRef.value?.focusInitial()
  },
  dismissInnerLayer(modalId) {
    if (modalId === 'folder-modal') return folderRef.value?.closeParentList() ?? false
    if (modalId === 'group-modal') return groupRef.value?.closeParentList() ?? false
    if (modalId === 'work-modal') return newFileRef.value?.closeOpenList() ?? false
    return false
  },
  resetClosed() {
    // Person and Playlist already start blank on every opening.
    if (modalRoot('folder-modal')?.classList.contains('hidden')) folderRef.value?.resetFields()
    if (modalRoot('group-modal')?.classList.contains('hidden')) groupRef.value?.resetFields()
  },
}

onMounted(() => {
  window.prksCreateForms = api
  window.prksRefreshFolderModalValidation = refreshFolderValidation
})

onBeforeUnmount(() => {
  if (window.prksCreateForms === api) delete window.prksCreateForms
  if (window.prksRefreshFolderModalValidation === refreshFolderValidation) {
    delete window.prksRefreshFolderModalValidation
  }
})
</script>

<template>
  <!--
    Temporary adapter (#1036). ModalHost does not mount feature forms yet,
    so these bodies teleport into the classic shells. Retire the teleport
    when the host grows a feature-form slot. New File also teleports its
    footer into the shell. Saved View, role link, the person template, and
    Settings stay on their shells.
  -->
  <Teleport to="#folder-modal-vue">
    <FolderCreateForm ref="folderRef" />
  </Teleport>
  <Teleport to="#person-modal-vue">
    <PersonCreateForm ref="personRef" />
  </Teleport>
  <Teleport to="#group-modal-vue">
    <GroupCreateForm ref="groupRef" />
  </Teleport>
  <Teleport to="#playlist-modal-vue">
    <PlaylistCreateForm ref="playlistRef" />
  </Teleport>
  <Teleport to="#work-modal-vue">
    <NewFileForm ref="newFileRef" />
  </Teleport>
</template>
