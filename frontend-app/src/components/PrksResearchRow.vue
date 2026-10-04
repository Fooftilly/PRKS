<script setup lang="ts">
withDefaults(
  defineProps<{
    href: string
    title: string
    kind?: string
    meta?: readonly string[]
    iconHtml?: string
  }>(),
  {
    meta: () => [],
  },
)
</script>

<template>
  <a class="prks-list-row prks-research-row" :href="href">
    <span
      v-if="iconHtml"
      class="prks-research-row__icon"
      aria-hidden="true"
      v-html="iconHtml"
    ></span>
    <span class="prks-research-row__body">
      <span class="prks-research-row__title-line">
        <span class="prks-research-row__title">{{ title }}</span>
        <span v-if="kind" class="prks-research-row__kind">{{ kind }}</span>
      </span>
      <span v-if="meta.length" class="prks-research-row__meta">
        <span v-for="part in meta" :key="part" class="prks-research-row__meta-item">{{ part }}</span>
      </span>
    </span>
  </a>
</template>
