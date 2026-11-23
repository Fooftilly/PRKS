<script setup lang="ts">
import { computed, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { deploymentSettingsErrorMessage, getDeploymentSettings } from '../../api/deployment-settings'
import PrksButton from '../../components/PrksButton.vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import PrksSectionHeader from '../../components/PrksSectionHeader.vue'
import { prksQueryKeys } from '../../query/keys'
import { settingsActiveCategory } from './navigation'

/**
 * Deployment settings (#612): how this PRKS was started, read-only.
 * The values come from the host (environment, Compose `.env`, secret files)
 * and are read once at start, so this panel never offers a Save button.
 */
const query = useQuery({
  queryKey: prksQueryKeys.deploymentSettings.list(),
  // Read only when the owner opens this category, never at Settings start.
  enabled: computed(() => settingsActiveCategory.value === 'deployment'),
  staleTime: Number.POSITIVE_INFINITY,
  refetchOnWindowFocus: false,
  retry: false,
  queryFn: ({ signal }) => getDeploymentSettings(signal),
})

// The panel stays mounted while other categories show, and the host may have
// restarted PRKS with new values since: read again each time it reopens.
watch(settingsActiveCategory, (now, before) => {
  if (now === 'deployment' && before !== 'deployment' && query.data.value) void query.refetch()
})

const settings = computed(() => query.data.value?.settings ?? [])
const changed = computed(() => settings.value.filter((setting) => setting.source !== 'default').length)

function sourceLabel(source: string): string {
  if (source === 'default') return 'Default'
  if (source === 'environment') return 'Set'
  return `Set by ${source}`
}
const errorText = computed(() => (query.error.value ? deploymentSettingsErrorMessage(query.error.value) : ''))
</script>

<template>
  <h4 class="prks-settings-panel__title">Deployment</h4>
  <section class="prks-settings-section" aria-labelledby="prks-settings-deployment-heading">
    <PrksSectionHeader id="prks-settings-deployment-heading">
      How this PRKS runs <span class="prks-settings-scope">Whole installation</span>
    </PrksSectionHeader>
    <p class="prks-settings-hint">
      Whoever runs PRKS sets these on the computer or container that runs it: in its environment, the Compose
      <code>.env</code> file or a secret file. PRKS reads them once at start, so a change needs a restart, and
      nothing here can be changed in the browser. Secrets and paths on that computer show only that they are set.
    </p>
    <p class="prks-settings-hint">
      To check a change before restarting, run <code>python prks_app.py config check</code> there.
    </p>
    <PrksInlineMessage v-if="errorText" tone="error" status>{{ errorText }}</PrksInlineMessage>
    <p v-else-if="query.isPending.value" class="prks-settings-hint" aria-live="polite">Loading deployment settings…</p>
    <template v-else>
      <p id="prks-deployment-summary" class="prks-settings-hint">
        {{ changed === 0 ? 'Every setting has its default.' : `${changed} of ${settings.length} settings are set; the rest have their defaults.` }}
      </p>
      <ul id="prks-deployment-settings" class="prks-deployment-list">
        <li v-for="setting in settings" :key="setting.key" class="prks-deployment-list__item" :data-prks-setting="setting.key">
          <div class="prks-deployment-list__row">
            <code class="prks-deployment-list__key">{{ setting.key }}</code>
            <span class="prks-deployment-list__value">{{ setting.value }}</span>
            <span class="prks-settings-scope">{{ sourceLabel(setting.source) }}</span>
          </div>
          <p class="prks-deployment-list__summary">{{ setting.summary }}</p>
        </li>
      </ul>
    </template>
    <div v-if="errorText" class="prks-form-actions">
      <PrksButton @click="query.refetch()">Try again</PrksButton>
    </div>
  </section>
</template>
