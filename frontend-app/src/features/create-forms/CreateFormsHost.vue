<script setup lang="ts">
import { computed, ref } from 'vue'
import FolderCreateForm from './FolderCreateForm.vue'
import GroupCreateForm from './GroupCreateForm.vue'
import NewFileForm from './NewFileForm.vue'
import PersonCreateForm from './PersonCreateForm.vue'
import PersonTemplateForm from './PersonTemplateForm.vue'
import PlaylistCreateForm from './PlaylistCreateForm.vue'
import RoleLinkForm from './RoleLinkForm.vue'
import SavedViewForm from '../saved-views/SavedViewForm.vue'
import ModalDialog from '../../modal/ModalDialog.vue'

const savedViewRef = ref<InstanceType<typeof SavedViewForm> | null>(null)

/** Saved View's heading follows its mode (new or edit). */
const savedViewTitle = computed(() => savedViewRef.value?.title ?? '')
</script>

<template>
  <!--
    ModalHost renders this inside its own layer (#1036): each form is the
    body of a ModalDialog shell and registers its own opening, close and
    Escape steps with it. openModal / closeModals reach the shells through
    window.prksModalHost. Settings keeps its classic shell.
  -->
  <ModalDialog
    id="work-modal"
    title="New File"
    dialog-class="modal-wide modal--create-file"
    body-class="modal-body--scroll"
    with-footer
  >
    <NewFileForm />
  </ModalDialog>
  <ModalDialog id="folder-modal" title="Add New Folder">
    <FolderCreateForm />
  </ModalDialog>
  <ModalDialog id="playlist-modal" title="New playlist">
    <PlaylistCreateForm />
  </ModalDialog>
  <ModalDialog id="saved-view-modal" :title="savedViewTitle">
    <SavedViewForm ref="savedViewRef" />
  </ModalDialog>
  <ModalDialog id="group-modal" title="New people group">
    <GroupCreateForm />
  </ModalDialog>
  <ModalDialog id="person-modal" title="Add New Person">
    <PersonCreateForm />
  </ModalDialog>
  <ModalDialog id="person-template-modal" title="Edit Profile Using Template">
    <PersonTemplateForm />
  </ModalDialog>
  <ModalDialog id="role-modal" title="Link Person to Work">
    <RoleLinkForm />
  </ModalDialog>
</template>
