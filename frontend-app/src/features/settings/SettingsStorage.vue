<script setup lang="ts">
import { computed, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { getStorageStatus, storageStatusErrorMessage } from '../../api/storage-status'
import PrksButton from '../../components/PrksButton.vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import PrksSectionHeader from '../../components/PrksSectionHeader.vue'
import { prksQueryKeys } from '../../query/keys'
import { settingsActiveCategory, settingsOpenCount } from './navigation'
import { formatStorageBytes, storageRows } from './storage-labels'

/**
 * Storage (#720): where the Library's files are, read-only.
 * Under PostgreSQL the folder is paired with the database, so nothing here
 * changes it; the panel says what does. The status names host paths, so the
 * server sends it no-store; TanStack Query keeps it only in memory.
 */
const query = useQuery({
  queryKey: prksQueryKeys.storageStatus.status(),
  // Read only when the owner opens this category, never at Settings start.
  enabled: computed(() => settingsActiveCategory.value === 'storage'),
  staleTime: Number.POSITIVE_INFINITY,
  refetchOnWindowFocus: false,
  retry: false,
  queryFn: ({ signal }) => getStorageStatus(signal),
})

// Free space and the counts change while PRKS runs: read again each time the
// category or Settings reopens on it, once a first read has finished.
function readBefore(): boolean {
  return query.data.value !== undefined || query.isError.value
}
watch(settingsActiveCategory, (now, before) => {
  if (now === 'storage' && before !== 'storage' && readBefore()) void query.refetch()
})
watch(settingsOpenCount, () => {
  if (settingsActiveCategory.value === 'storage' && readBefore()) void query.refetch()
})

// A failed read hides the last answer too: it may belong to another session.
const status = computed(() => (query.error.value ? null : (query.data.value ?? null)))
const rows = computed(() => (status.value ? storageRows(status.value) : null))
const freeSpace = computed(() => {
  const fs = status.value?.filesystem
  if (!fs || fs.free_bytes === null) return 'Free space unknown'
  const free = `${formatStorageBytes(fs.free_bytes)} free`
  return fs.total_bytes === null ? free : `${free} of ${formatStorageBytes(fs.total_bytes)}`
})
const missing = computed(() => status.value?.objects?.missing ?? 0)
const errorText = computed(() => (query.error.value ? storageStatusErrorMessage(query.error.value) : ''))
</script>

<template>
  <h4 class="prks-settings-panel__title">Storage</h4>
  <section class="prks-settings-section" aria-labelledby="prks-settings-storage-heading">
    <PrksSectionHeader id="prks-settings-storage-heading">
      Library storage <span class="prks-settings-scope">Whole installation</span>
    </PrksSectionHeader>
    <p class="prks-settings-hint">
      A Library has two halves: its files, in a folder on the computer that runs PRKS, and its database. The two
      are paired, so the folder cannot be changed in the browser or while PRKS runs.
    </p>
    <PrksInlineMessage v-if="errorText" tone="error" status>{{ errorText }}</PrksInlineMessage>
    <p v-else-if="query.isPending.value" class="prks-settings-hint" aria-live="polite">Loading the storage status…</p>
    <template v-else-if="status && rows">
      <dl id="prks-storage-locations" class="prks-storage-facts">
        <div class="prks-storage-facts__row" data-prks-storage="files">
          <dt>Library files</dt>
          <dd>
            <code class="prks-storage-facts__path">{{ status.root.resolved }}</code>
            <span v-if="rows.configuredDiffers" class="prks-storage-facts__note">
              Configured as <code>{{ status.root.configured }}</code>
            </span>
            <span class="prks-storage-facts__note">{{ freeSpace }} · {{ rows.source }}</span>
          </dd>
        </div>
        <div class="prks-storage-facts__row" data-prks-storage="database">
          <dt>Library database</dt>
          <dd>
            {{ rows.database }}
            <span class="prks-storage-facts__note">Managed by your deployment</span>
          </dd>
        </div>
      </dl>
      <PrksInlineMessage v-if="missing > 0" tone="warning" status>
        {{ missing === 1 ? '1 file the Library names is' : `${missing} files the Library names are` }}
        missing from the folder. Restore them from a backup.
      </PrksInlineMessage>
      <PrksInlineMessage v-if="status.filesystem.network === 'network'" tone="warning" status>
        This folder is on a network filesystem, where PRKS's file locks are not reliable. Move the Library to a local disk.
      </PrksInlineMessage>
    </template>
    <div class="prks-form-actions">
      <PrksButton v-if="errorText" @click="query.refetch()">Try again</PrksButton>
      <PrksButton v-else-if="status" :disabled="query.isFetching.value" @click="query.refetch()">Check again</PrksButton>
    </div>
  </section>
  <section
    v-if="status && rows"
    class="prks-settings-section"
    aria-labelledby="prks-settings-storage-files-heading"
  >
    <PrksSectionHeader id="prks-settings-storage-files-heading">Files</PrksSectionHeader>
    <dl id="prks-storage-objects" class="prks-storage-facts">
      <div v-for="row in rows.objects" :key="row.id" class="prks-storage-facts__row" :data-prks-storage="row.id">
        <dt>{{ row.label }}</dt>
        <dd>{{ row.value }}</dd>
      </div>
    </dl>
  </section>
  <section
    v-if="status && rows"
    class="prks-settings-section"
    aria-labelledby="prks-settings-storage-folder-heading"
  >
    <PrksSectionHeader id="prks-settings-storage-folder-heading">Folder</PrksSectionHeader>
    <dl id="prks-storage-folder" class="prks-storage-facts">
      <div v-for="row in rows.folder" :key="row.id" class="prks-storage-facts__row" :data-prks-storage="row.id">
        <dt>{{ row.label }}</dt>
        <dd>{{ row.value }}</dd>
      </div>
    </dl>
    <p class="prks-settings-hint">
      To keep the Library in another folder, set up a new installation that uses it and restore a Library backup
      into it.
    </p>
  </section>
</template>
