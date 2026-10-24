<script setup lang="ts">
import { computed, watch } from 'vue'
import {
  WORK_THUMB_PLACEHOLDER,
  workCardCreditText,
  workCardEmptyThumbTitle,
  workCardFileSizeLabel,
  workCardHref,
  workCardId,
  workCardIsVideoKind,
  workCardResolvedThumbPage,
  workCardStatus,
  workCardStatusClass,
  workCardThumbUrl,
  workCardTitle,
  workCardYearPlain,
  type PrksWorkCardOptions,
  type PrksWorkCardWork,
} from './work-card'

const props = defineProps<{
  work: PrksWorkCardWork
  options?: PrksWorkCardOptions
}>()

const options = computed(() => props.options || {})
const title = computed(() => workCardTitle(props.work))
const workId = computed(() => workCardId(props.work))
const href = computed(() => workCardHref(props.work))
const status = computed(() => workCardStatus(props.work))
const statusClass = computed(() => workCardStatusClass(status.value))
const credit = computed(() => workCardCreditText(props.work))
const year = computed(() => workCardYearPlain(props.work))
const meta = computed(() => [credit.value, year.value].filter(Boolean).join(' · '))
const subtitle = computed(() => {
  const raw = options.value.subtitle
  return raw != null ? String(raw) : ''
})
const fileSizeLabel = computed(() => workCardFileSizeLabel(props.work))
const isVideoKind = computed(() => workCardIsVideoKind(props.work))
const thumbKindClass = computed(() => (isVideoKind.value ? 'work-card__thumb--video' : 'work-card__thumb--pdf'))
const thumbSrc = computed(() => workCardThumbUrl(props.work, options.value))
const thumbPage = computed(() =>
  thumbSrc.value && !isVideoKind.value ? String(workCardResolvedThumbPage(props.work, options.value)) : '',
)
const emptyTitle = computed(() => workCardEmptyThumbTitle(props.work, options.value))
const statusIconHtml = computed(() => {
  if (!status.value) return ''
  const fn = window.prksProgressStatusIconHtml
  return typeof fn === 'function' ? fn(status.value, { className: 'status-badge__icon', size: 'sm' }) : ''
})
const typeBadgeHtml = computed(() => {
  if (options.value.hideDocTypeBadge) return ''
  const fn = window.prksDocTypeBadgeHtml
  return typeof fn === 'function' ? fn(String(props.work.doc_type ?? '')) : ''
})

watch(
  () => [workId.value, thumbSrc.value] as const,
  ([id, src]) => {
    if (!id || !src) return
    window.prksRegisterWorkThumbUrl?.(id, src)
  },
  { immediate: true },
)
</script>

<template>
  <div
    class="project-card project-card--work-card"
    :data-work-id="workId"
    :data-prks-route="href || undefined"
    data-prks-middleclick-nav="1"
  >
    <a v-if="href" class="work-card__link" :href="href" :aria-label="title">
      <div
        v-if="thumbSrc"
        :key="thumbSrc"
        class="work-card__thumb work-card__thumb--loading"
        :class="thumbKindClass"
        data-prks-thumb-state="loading"
        :data-prks-thumb-preview-kind="isVideoKind ? 'video' : 'pdf'"
        :data-prks-thumb-page="thumbPage || undefined"
      >
        <img loading="lazy" alt="" :src="WORK_THUMB_PLACEHOLDER" data-prks-thumb-lazy="1" />
      </div>
      <div
        v-else
        class="work-card__thumb work-card__thumb--empty"
        :class="thumbKindClass"
        data-prks-thumb-state="empty"
        :title="emptyTitle"
        aria-hidden="true"
      ></div>
      <div class="work-card__body">
        <div class="card-title" :title="title">{{ title }}</div>
        <div v-if="meta" class="meta-row work-card__meta">{{ meta }}</div>
        <div v-if="subtitle" class="work-card__context">{{ subtitle }}</div>
        <div class="work-card__badges">
          <div class="work-card__badges-left">
            <span v-if="status" class="status-badge" :class="statusClass">
              <span v-if="statusIconHtml" style="display: contents" v-html="statusIconHtml"></span>{{ status }}
            </span>
            <span v-if="typeBadgeHtml" style="display: contents" v-html="typeBadgeHtml"></span>
          </div>
          <div v-if="fileSizeLabel" class="work-card__badges-right">
            <span class="work-card__file-size">{{ fileSizeLabel }}</span>
          </div>
        </div>
      </div>
    </a>
  </div>
</template>
