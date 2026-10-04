<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import PrksWorkCard from '../../components/PrksWorkCard.vue'
import { useWorkCardCollection } from '../../components/use-work-card-collection'
import { workCardThumbOptions } from '../../components/work-card'
import { recentOpenedSubtitle, type RecentProjection } from './projection'

const props = defineProps<{
  projection: RecentProjection
}>()

const headerIcon = computed(() => window.prksPageHeaderIconHtml?.('clock') ?? '')
const offlineCached = computed(() => props.projection.offlineCached === true)
const rows = computed(() => props.projection.rows)
const collectionClass = computed(() => {
  void props.projection.generation
  const fn = window.prksWorkBrowseCollectionClass
  return typeof fn === 'function' ? fn() : 'card-grid'
})

const modeHost = ref<HTMLElement | null>(null)
const rootEl = ref<HTMLElement | null>(null)

useWorkCardCollection(rootEl, { initWhen: () => !offlineCached.value })

function paintMode(): void {
  const host = modeHost.value
  if (!host) return
  host.innerHTML = window.prksWorkBrowseModeToggleHtml?.('prks-work-browse-mode-recent') ?? ''
  window.prksBindWorkBrowseMode?.(rootEl.value)
}

onMounted(() => {
  paintMode()
})

watch(
  () => props.projection.generation,
  () => {
    paintMode()
  },
  { flush: 'post' },
)
</script>

<template>
  <div ref="rootEl" data-prks-recent-view>
    <div class="prks-page-header page-header page-header--split">
      <div class="page-header__title-row">
        <h2 class="prks-page-title">
          <span style="display: contents" v-html="headerIcon"></span>
          Recently Opened
        </h2>
        <div ref="modeHost" data-prks-recent-mode-host style="display: contents"></div>
      </div>
    </div>
    <div :class="collectionClass">
      <PrksWorkCard
        v-for="work in rows"
        :key="String(work.id ?? '')"
        :work="work"
        :options="workCardThumbOptions(offlineCached, { subtitle: recentOpenedSubtitle(work.last_opened_at) })"
      />
      <p v-if="!rows.length" class="prks-inline-message">No recently opened documents found.</p>
    </div>
  </div>
</template>
