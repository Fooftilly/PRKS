<script setup lang="ts">
import { ref } from 'vue'
import PrksField from '../../components/PrksField.vue'
import { fieldValue } from './dom'
import { submitPersonCreate } from './submit'

const personBusy = ref(false)

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

async function onSave(): Promise<void> {
  if (personBusy.value) return
  const w = runtime()
  const parse = w.parsePersonBirthDeathField
  const help = w.PERSON_DATE_HELP || ''
  if (typeof parse === 'function') {
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
    const lastName = fieldValue('person-lname')
    if (!lastName) {
      await w.prksAlertMessage?.('Last name is required.', 'Validation')
      return
    }
    personBusy.value = true
    try {
      await submitPersonCreate({
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
      })
    } finally {
      personBusy.value = false
    }
    return
  }
  await w.prksAlertMessage?.('Could not save this person locally. Please retry.', 'Could not save')
}

function afterReset(): void {
  personBusy.value = false
}

defineExpose({ afterReset })
</script>

<template>
  <div class="form-pane">
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
    <button
      id="save-person-btn"
      type="button"
      class="prks-btn prks-btn--primary"
      :disabled="personBusy"
      :aria-busy="personBusy ? 'true' : undefined"
      @click="onSave"
    >{{ personBusy ? 'Saving…' : 'Save Person' }}</button>
  </div>
</template>
