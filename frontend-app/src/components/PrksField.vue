<script setup lang="ts">
/**
 * Label + native control slot + optional help/error.
 * The control stays a visible input, select, or textarea in the slot.
 * Slot props `labelledBy` / `describedBy` must be bound on that control
 * so help and error ids compose (help then error) instead of replacing.
 */
import { computed, onMounted, onUpdated, ref } from 'vue'

const props = defineProps<{
  label: string
  forId: string
  /** Present (including '') reserves the live error node. Omit when the field has no error region. */
  error?: string | null
  help?: string
}>()

const controlHost = ref<HTMLElement | null>(null)

const labelledBy = computed(() => `${props.forId}-label`)
const helpId = computed(() => (props.help ? `${props.forId}-help` : ''))
const errorId = computed(() => (props.error != null ? `${props.forId}-error` : ''))
const describedBy = computed(() => {
  const ids = [helpId.value, errorId.value].filter(Boolean)
  return ids.length ? ids.join(' ') : undefined
})

function applyControlNames(): void {
  const host = controlHost.value
  if (!host) return
  const control = host.querySelector<HTMLElement>('input, select, textarea')
  if (!control) return
  control.setAttribute('aria-labelledby', labelledBy.value)
  if (describedBy.value) control.setAttribute('aria-describedby', describedBy.value)
  else control.removeAttribute('aria-describedby')
}

onMounted(applyControlNames)
onUpdated(applyControlNames)
</script>

<template>
  <div class="prks-field" :class="{ 'prks-field--error': !!error }">
    <label :id="labelledBy" class="prks-field__label" :for="forId">
      {{ label }}
    </label>
    <div ref="controlHost" class="prks-field__control">
      <slot :labelled-by="labelledBy" :described-by="describedBy" />
    </div>
    <p v-if="help" :id="helpId" class="prks-field__help">{{ help }}</p>
    <p
      v-if="error != null"
      :id="errorId"
      class="prks-field__error field-error"
      aria-live="polite"
    >{{ error }}</p>
  </div>
</template>
