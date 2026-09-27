<script setup lang="ts">
import { computed } from 'vue'
import { formatPerformanceReport, formatClientRequestBody, formatSpans, formatSummary, formatThumbnailCache, dbCallsPerRequest, formatPerfMs, routeLabel } from './format'
import { usePerformanceDiagnostics } from './usePerformanceDiagnostics'

const diagnostics = usePerformanceDiagnostics()
const statusText = diagnostics.statusText
const refreshBusy = computed(() => diagnostics.isRefreshPending.value || diagnostics.isResetPending.value)
const resetBusy = computed(() => diagnostics.isResetPending.value)

const summaryText = computed(() => {
  const snap = diagnostics.data.value
  return snap ? formatSummary(snap) : 'Loading measurements…'
})

const routes = computed(() => diagnostics.data.value?.routes ?? [])

const spansText = computed(() => (diagnostics.data.value ? formatSpans(diagnostics.data.value.spans) : ''))

const thumbsText = computed(() =>
  diagnostics.data.value ? formatThumbnailCache(diagnostics.data.value.counters) : '',
)

const clientText = computed(() => {
  if (!diagnostics.data.value) return 'Loading measurements…'
  return formatClientRequestBody(diagnostics.clientSnapshot.value)
})

async function onRefresh(): Promise<void> {
  await diagnostics.refresh()
}

async function onReset(): Promise<void> {
  await diagnostics.resetMeasurements()
}

async function onCopy(): Promise<void> {
  const text = formatPerformanceReport(diagnostics.data.value, diagnostics.clientSnapshot.value)
  try {
    if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable')
    await navigator.clipboard.writeText(text)
    statusText.value = 'Report copied.'
  } catch {
    statusText.value = 'Could not copy report.'
  }
}
</script>

<template>
  <p id="prks-perf-summary" class="prks-settings-hint" aria-live="polite">{{ summaryText }}</p>
  <div class="prks-perf-table-wrap">
    <table id="prks-perf-routes" class="prks-perf-table">
      <caption class="prks-sr-only">Slowest API routes</caption>
      <thead>
        <tr>
          <th scope="col">Route</th>
          <th scope="col">Calls</th>
          <th scope="col">Avg</th>
          <th scope="col">P95</th>
          <th scope="col">Max</th>
          <th scope="col">DB</th>
          <th scope="col">DB/call</th>
        </tr>
      </thead>
      <tbody id="prks-perf-routes-body">
        <tr v-if="!routes.length">
          <td colspan="7">No API requests measured yet.</td>
        </tr>
        <tr v-for="row in routes" :key="routeLabel(row)">
          <td>{{ routeLabel(row) }}</td>
          <td>{{ row.count || 0 }}</td>
          <td>{{ formatPerfMs(row.avg_ms) }}</td>
          <td>{{ formatPerfMs(row.p95_ms) }}</td>
          <td>{{ formatPerfMs(row.max_ms) }}</td>
          <td>{{ row.measured_db_share_percent == null ? '—' : String(row.measured_db_share_percent) + '%' }}</td>
          <td>{{ formatPerfMs(dbCallsPerRequest(row)) }}</td>
        </tr>
      </tbody>
    </table>
  </div>
  <p id="prks-perf-spans" class="prks-settings-hint">{{ spansText }}</p>
  <p id="prks-perf-thumbs" class="prks-settings-hint">{{ thumbsText }}</p>
  <div id="prks-perf-client" class="prks-perf-client">
    <h5 class="prks-settings-section__title">Client request coordinator</h5>
    <p id="prks-perf-client-body" class="prks-settings-hint">{{ clientText }}</p>
  </div>
  <div class="prks-backup-restore-row">
    <button
      id="prks-perf-refresh-btn"
      type="button"
      class="prks-btn prks-btn--secondary"
      :disabled="refreshBusy"
      :aria-busy="refreshBusy ? 'true' : undefined"
      @click="onRefresh"
    >
      {{ refreshBusy && !resetBusy ? 'Refreshing…' : 'Refresh' }}
    </button>
    <button
      id="prks-perf-reset-btn"
      type="button"
      class="prks-btn prks-btn--secondary"
      :disabled="resetBusy"
      :aria-busy="resetBusy ? 'true' : undefined"
      @click="onReset"
    >
      {{ resetBusy ? 'Resetting…' : 'Reset' }}
    </button>
    <button id="prks-perf-copy-btn" type="button" class="prks-btn prks-btn--secondary" @click="onCopy">
      Copy report
    </button>
  </div>
  <p id="prks-perf-status" class="prks-settings-hint" aria-live="polite">{{ statusText }}</p>
</template>
