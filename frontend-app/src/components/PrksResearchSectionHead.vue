<script setup lang="ts">
import PrksButton from './PrksButton.vue'

defineProps<{
  title: string
  headingId?: string
  count?: number
  sub?: string
  actionId?: string
  actionLabel?: string
  actionRole?: string
}>()

const emit = defineEmits<{
  action: []
}>()
</script>

<template>
  <div>
    <div class="research-entity__section-head">
      <h3 :id="headingId">{{ title }}</h3>
      <div v-if="count != null || actionId" class="research-entity__section-head-actions">
        <span v-if="count != null" class="research-entity__section-count">{{ count }}</span>
        <PrksButton
          v-if="actionId"
          :id="actionId"
          size="sm"
          :data-prks-role="actionRole"
          @click="emit('action')"
        >
          {{ actionLabel || 'Edit' }}
        </PrksButton>
      </div>
    </div>
    <p v-if="sub" class="research-entity__section-sub meta-row">{{ sub }}</p>
  </div>
</template>
