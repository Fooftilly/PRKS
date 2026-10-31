<script setup lang="ts">
import { computed } from 'vue'
import {
  resolveWorkVideoEmbed,
  VIDEO_IFRAME_ALLOW,
  VIDEO_IFRAME_TITLE,
  type WorkVideoSource,
} from './video-embed'

const props = defineProps<{
  source: WorkVideoSource | null
}>()

const presentation = computed(() =>
  resolveWorkVideoEmbed(props.source ?? { provider: '', providerId: '', sourceUrl: '' }),
)
const embedSrc = computed(() => (presentation.value.state === 'embed' ? presentation.value.src : ''))
const linkHref = computed(() => (presentation.value.state === 'link' ? presentation.value.href : ''))
const lead = computed(() =>
  presentation.value.state === 'link' ? 'No embeddable video URL. ' : 'No embeddable video URL.',
)
</script>

<template>
  <div v-if="embedSrc" class="work-pdf-pane">
    <div class="prks-work-video-frame">
      <iframe
        :src="embedSrc"
        :allow="VIDEO_IFRAME_ALLOW"
        :title="VIDEO_IFRAME_TITLE"
        allowfullscreen
      ></iframe>
    </div>
  </div>
  <div v-else class="work-pdf-pane work-pdf-pane--empty">
    <p class="work-pdf-empty">
      {{ lead }}<a v-if="linkHref" :href="linkHref" target="_blank" rel="noopener">Open link</a>
    </p>
  </div>
</template>
