<script setup lang="ts">
/**
 * Label + native control slot + optional help/error.
 * The control stays a visible input, select, or textarea in the slot.
 */
defineProps<{
  label: string
  forId: string
  /** Present (including '') reserves the live error node. Omit when the field has no error region. */
  error?: string | null
  help?: string
  required?: boolean
}>()
</script>

<template>
  <div class="prks-field" :class="{ 'prks-field--error': !!error }">
    <label class="prks-field__label" :for="forId">
      {{ label }}
      <span v-if="required" class="prks-field__required" aria-hidden="true">Required</span>
    </label>
    <div class="prks-field__control">
      <slot />
    </div>
    <p v-if="help" :id="`${forId}-help`" class="prks-field__help">{{ help }}</p>
    <p
      v-if="error != null"
      :id="`${forId}-error`"
      class="prks-field__error field-error"
      aria-live="polite"
    >{{ error }}</p>
  </div>
</template>
