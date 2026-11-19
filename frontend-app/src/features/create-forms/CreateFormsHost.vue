<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import FolderCreateForm from './FolderCreateForm.vue'
import GroupCreateForm from './GroupCreateForm.vue'
import NewFileForm from './NewFileForm.vue'
import PersonCreateForm from './PersonCreateForm.vue'
import PersonTemplateForm from './PersonTemplateForm.vue'
import PlaylistCreateForm from './PlaylistCreateForm.vue'
import RoleLinkForm from './RoleLinkForm.vue'
import SavedViewForm from '../saved-views/SavedViewForm.vue'
import ModalDialog from '../../modal/ModalDialog.vue'
import { snapshotModalFields } from './snapshot'
import { CREATE_FORM_MODAL_IDS, type CreateFormModalId, type PrksCreateFormsApi } from './types'

const OWNED = new Set<string>(CREATE_FORM_MODAL_IDS)
const baselines = new Map<string, string>()

const folderRef = ref<InstanceType<typeof FolderCreateForm> | null>(null)
const personRef = ref<InstanceType<typeof PersonCreateForm> | null>(null)
const groupRef = ref<InstanceType<typeof GroupCreateForm> | null>(null)
const playlistRef = ref<InstanceType<typeof PlaylistCreateForm> | null>(null)
const newFileRef = ref<InstanceType<typeof NewFileForm> | null>(null)
const savedViewRef = ref<InstanceType<typeof SavedViewForm> | null>(null)
const roleLinkRef = ref<InstanceType<typeof RoleLinkForm> | null>(null)
const personTemplateRef = ref<InstanceType<typeof PersonTemplateForm> | null>(null)

/** Saved View's heading follows its mode (new or edit). */
const savedViewTitle = computed(() => savedViewRef.value?.title ?? '')

function modalRoot(modalId: string): HTMLElement | null {
  const el = document.getElementById(modalId)
  return el instanceof HTMLElement ? el : null
}

/** Each owned dialog's form, for focusing its first field on open. */
const focusTargets: Record<CreateFormModalId, () => { focusInitial: () => void } | null> = {
  'folder-modal': () => folderRef.value,
  'person-modal': () => personRef.value,
  'group-modal': () => groupRef.value,
  'playlist-modal': () => playlistRef.value,
  'work-modal': () => newFileRef.value,
  'saved-view-modal': () => savedViewRef.value,
  'role-modal': () => roleLinkRef.value,
  'person-template-modal': () => personTemplateRef.value,
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
  prepareRoleLink(stillOpening) {
    return roleLinkRef.value?.prepare(stillOpening) ?? Promise.resolve()
  },
  prepareNewFile(stillOpening) {
    return newFileRef.value?.prepare(stillOpening) ?? Promise.resolve()
  },
  focusInitial(modalId) {
    focusTargets[modalId as CreateFormModalId]?.()?.focusInitial()
  },
  dismissInnerLayer(modalId) {
    if (modalId === 'folder-modal') return folderRef.value?.closeParentList() ?? false
    if (modalId === 'group-modal') return groupRef.value?.closeParentList() ?? false
    if (modalId === 'work-modal') return newFileRef.value?.closeOpenList() ?? false
    if (modalId === 'role-modal') return roleLinkRef.value?.closeOpenList() ?? false
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
    ModalHost renders this inside its own layer (#1036): each form is the
    body of a ModalDialog shell, and openModal / closeModals reach the shells
    through window.prksModalHost. Settings keeps its classic shell.
  -->
  <ModalDialog
    id="work-modal"
    title="New File"
    dialog-class="modal-wide modal--create-file"
    body-class="modal-body--scroll"
    with-footer
  >
    <NewFileForm ref="newFileRef" />
  </ModalDialog>
  <ModalDialog id="folder-modal" title="Add New Folder">
    <FolderCreateForm ref="folderRef" />
  </ModalDialog>
  <ModalDialog id="playlist-modal" title="New playlist">
    <PlaylistCreateForm ref="playlistRef" />
  </ModalDialog>
  <ModalDialog id="saved-view-modal" :title="savedViewTitle">
    <SavedViewForm ref="savedViewRef" />
  </ModalDialog>
  <ModalDialog id="group-modal" title="New people group">
    <GroupCreateForm ref="groupRef" />
  </ModalDialog>
  <ModalDialog id="person-modal" title="Add New Person">
    <PersonCreateForm ref="personRef" />
  </ModalDialog>
  <ModalDialog id="person-template-modal" title="Edit Profile Using Template">
    <PersonTemplateForm ref="personTemplateRef" />
  </ModalDialog>
  <ModalDialog id="role-modal" title="Link Person to Work">
    <RoleLinkForm ref="roleLinkRef" />
  </ModalDialog>
</template>
