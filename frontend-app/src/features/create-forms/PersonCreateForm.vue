<script setup lang="ts">
import { ref } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksField from '../../components/PrksField.vue'
import { useDialogLifecycle } from '../../modal/dialogs'
import { fieldValue, setFieldValue } from './dom'
import { createPersonSaveGate } from './person-save'
import { createPersonDurably, finishPersonCreate, type PersonPayload } from './submit'
import { createSubmitKeyHandler, focusField } from './keyboard'

const personBusy = ref(false)
const gate = createPersonSaveGate()

interface PersonWindow {
  parsePersonBirthDeathField?: (text: string) => string | null
  PERSON_DATE_HELP?: string
  prksAlertMessage?: (message: string, title?: string) => Promise<void> | void
  syncPersonAliasesFromNames?: () => void
}

function runtime(): PersonWindow {
  return window as unknown as PersonWindow
}

function onNameInput(): void {
  runtime().syncPersonAliasesFromNames?.()
}

function onAliasesInput(): void {
  window._personAliasesManual = true
}

function readPayload(): PersonPayload | null {
  const w = runtime()
  const parse = w.parsePersonBirthDeathField
  if (typeof parse !== 'function') return null
  const birth = parse(fieldValue('person-birth-date'))
  if (birth === null) return null
  const death = parse(fieldValue('person-death-date'))
  if (death === null) return null
  const lastName = fieldValue('person-lname')
  if (!lastName) return null
  return {
    first_name: fieldValue('person-fname'),
    last_name: lastName,
    aliases: fieldValue('person-aliases'),
    about: fieldValue('person-about'),
    image_url: fieldValue('person-image-url'),
    link_wikipedia: fieldValue('person-link-wikipedia'),
    link_stanford_encyclopedia: fieldValue('person-link-stanford'),
    link_iep: fieldValue('person-link-iep'),
    links_other: fieldValue('person-links-other'),
    birth_date: birth,
    death_date: death,
  }
}

async function onSave(): Promise<void> {
  const w = runtime()
  const parse = w.parsePersonBirthDeathField
  const help = w.PERSON_DATE_HELP || ''
  if (typeof parse !== 'function') {
    await w.prksAlertMessage?.('Could not save this person locally. Please retry.', 'Could not save')
    return
  }
  const birth = parse(fieldValue('person-birth-date'))
  if (birth === null) {
    await w.prksAlertMessage?.(`Birth:\n${help}`, 'Validation')
    return
  }
  const death = parse(fieldValue('person-death-date'))
  if (death === null) {
    await w.prksAlertMessage?.(`Date of death:\n${help}`, 'Validation')
    return
  }
  if (!fieldValue('person-lname')) {
    await w.prksAlertMessage?.('Last name is required.', 'Validation')
    return
  }
  await gate.save({
    modal: () => {
      const el = document.getElementById('person-modal')
      return el instanceof HTMLElement ? el : null
    },
    isBusy: () => personBusy.value,
    setBusy: (busy) => {
      personBusy.value = busy
    },
    readPayload,
    createPerson: createPersonDurably,
    alert: async (message, title) => {
      await w.prksAlertMessage?.(message, title)
    },
    afterCreate: finishPersonCreate,
  })
}

const FIELD_IDS = [
  'person-fname',
  'person-lname',
  'person-aliases',
  'person-about',
  'person-birth-date',
  'person-death-date',
  'person-image-url',
  'person-link-wikipedia',
  'person-link-stanford',
  'person-link-iep',
  'person-links-other',
] as const

/**
 * Every opening starts blank, with aliases following the names again. The
 * profile editor in the right panel is a separate form and is not touched.
 */
function resetForOpening(): void {
  window._personAliasesManual = false
  for (const id of FIELD_IDS) {
    setFieldValue(id, '')
    document.getElementById(id)?.removeAttribute('aria-invalid')
  }
  // New opening: allow a fresh Save. An older in-flight create keeps its own
  // opening token and must not clear this busy flag when it settles.
  personBusy.value = false
}

const onKeydown = createSubmitKeyHandler(['person-fname', 'person-lname'], () => void onSave())

function focusInitial(): void {
  focusField('person-fname')
}

useDialogLifecycle({ opened: resetForOpening, focusInitial })
</script>

<template>
  <div class="form-pane" @keydown="onKeydown">
    <PrksField v-slot="{ labelledBy, describedBy }" label="First name" for-id="person-fname">
      <input
        id="person-fname"
        type="text"
        placeholder="First name"
        :aria-labelledby="labelledBy"
        :aria-describedby="describedBy"
        @input="onNameInput"
      >
    </PrksField>
    <PrksField v-slot="{ labelledBy, describedBy }" label="Last name" for-id="person-lname">
      <input
        id="person-lname"
        type="text"
        placeholder="Required"
        :aria-labelledby="labelledBy"
        :aria-describedby="describedBy"
        @input="onNameInput"
      >
    </PrksField>
    <PrksField v-slot="{ labelledBy, describedBy }" label="Aliases" for-id="person-aliases">
      <input
        id="person-aliases"
        type="text"
        placeholder="Filled from names as you type; edit anytime"
        :aria-labelledby="labelledBy"
        :aria-describedby="describedBy"
        @input="onAliasesInput"
      >
    </PrksField>
    <PrksField v-slot="{ labelledBy, describedBy }" label="About / expertise" for-id="person-about">
      <textarea
        id="person-about"
        placeholder="Optional…"
        :aria-labelledby="labelledBy"
        :aria-describedby="describedBy"
      ></textarea>
    </PrksField>
    <div class="form-grid-2">
      <PrksField v-slot="{ labelledBy, describedBy }" label="Birth date" for-id="person-birth-date">
        <input
          id="person-birth-date"
          type="text"
          placeholder="dd/mm/yyyy, yyyy, or -428"
          autocomplete="off"
          :aria-labelledby="labelledBy"
          :aria-describedby="describedBy"
        >
      </PrksField>
      <PrksField v-slot="{ labelledBy, describedBy }" label="Date of death" for-id="person-death-date">
        <input
          id="person-death-date"
          type="text"
          placeholder="dd/mm/yyyy, yyyy, or -428"
          autocomplete="off"
          :aria-labelledby="labelledBy"
          :aria-describedby="describedBy"
        >
      </PrksField>
    </div>
    <PrksField v-slot="{ labelledBy, describedBy }" label="Portrait image URL" for-id="person-image-url">
      <input
        id="person-image-url"
        type="url"
        placeholder="https://…"
        :aria-labelledby="labelledBy"
        :aria-describedby="describedBy"
      >
    </PrksField>
    <PrksField v-slot="{ labelledBy, describedBy }" label="Wikipedia" for-id="person-link-wikipedia">
      <input
        id="person-link-wikipedia"
        type="url"
        placeholder="https://en.wikipedia.org/wiki/…"
        :aria-labelledby="labelledBy"
        :aria-describedby="describedBy"
      >
    </PrksField>
    <PrksField
      v-slot="{ labelledBy, describedBy }"
      label="Stanford Encyclopedia of Philosophy"
      for-id="person-link-stanford"
    >
      <input
        id="person-link-stanford"
        type="url"
        placeholder="https://plato.stanford.edu/entries/…"
        :aria-labelledby="labelledBy"
        :aria-describedby="describedBy"
      >
    </PrksField>
    <PrksField v-slot="{ labelledBy, describedBy }" label="Internet Encyclopedia of Philosophy" for-id="person-link-iep">
      <input
        id="person-link-iep"
        type="url"
        placeholder="https://iep.utm.edu/…"
        :aria-labelledby="labelledBy"
        :aria-describedby="describedBy"
      >
    </PrksField>
    <PrksField v-slot="{ labelledBy, describedBy }" label="Other links" for-id="person-links-other">
      <textarea
        id="person-links-other"
        class="textarea-sm"
        placeholder="One URL per line, or [Title](https://...)"
        :aria-labelledby="labelledBy"
        :aria-describedby="describedBy"
      ></textarea>
    </PrksField>
    <PrksButton
      id="save-person-btn"
      variant="primary"
      :busy="personBusy"
      busy-label="Saving…"
      @click="onSave"
    >Save Person</PrksButton>
  </div>
</template>
