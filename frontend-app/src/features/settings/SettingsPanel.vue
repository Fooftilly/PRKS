<script setup lang="ts">
defineProps<{
  category: string
  active: boolean
}>()
</script>

<template>
  <section
    class="prks-settings-panel"
    :id="`prks-settings-panel-${category}`"
    role="tabpanel"
    :aria-labelledby="`prks-settings-tab-${category}`"
    tabindex="0"
    :hidden="active ? undefined : true"
    :inert="active ? undefined : true"
  >
    <slot />
  </section>
</template>
