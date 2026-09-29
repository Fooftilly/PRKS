<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import type { WorkMetaDraft, WorkMetaField } from './metadata-draft'
import type { WorkMetadataChrome, WorkMetadataConflict, WorkMetadataGroupChrome } from './metadata-session'

const props = defineProps<{
  workId: string
  sourceKind: string
  draft: WorkMetaDraft
  chrome: WorkMetadataChrome
}>()

const emit = defineEmits<{
  focusField: [field: string]
  blurField: [field: string]
}>()

const isVideo = props.sourceKind === 'video'
const statuses = ['Not Started', 'Planned', 'In Progress', 'Completed', 'Paused']
const docMenuReady = ref(false)

function group(name: string): WorkMetadataGroupChrome | undefined {
  return props.chrome.groups[name]
}

function fieldOff(field: WorkMetaField): boolean {
  const spec = groupForField(field)?.fields[field]
  if (!spec) return true
  return !!spec.disabled
}

function fieldTitle(field: WorkMetaField): string {
  return groupForField(field)?.fields[field]?.title || ''
}

function groupForField(field: WorkMetaField): WorkMetadataGroupChrome | undefined {
  if (field === 'title' || field === 'doc_type') return group('identity')
  if (field === 'status') return group('status')
  if (field === 'source_url' && isVideo) return group('source')
  return group('bib')
}

function invalid(field: string): boolean {
  return props.chrome.fieldError?.field === field
}

function errorText(field: string): string {
  return invalid(field) ? props.chrome.fieldError?.message || '' : ''
}

function saveDisabled(name: string): boolean {
  const spec = group(name)
  if (!spec) return true
  return spec.saveDisabled !== false
}

function conflicts(name: string): readonly WorkMetadataConflict[] {
  return group(name)?.conflicts || []
}

function statusLine(name: string): string {
  return group(name)?.status || ''
}

function chooseStatus(value: string): void {
  if (fieldOff('status')) return
  props.draft.status = value
}

function statusIcon(label: string): string {
  const fn = window.prksProgressStatusIconHtml
  return typeof fn === 'function' ? fn(label, { size: 'sm' }) : ''
}

function saveGroup(name: string): void {
  const save = window.prksSaveWorkMetadataFields
  if (typeof save === 'function') void save(props.workId, name)
}

function saveSource(): void {
  const save = window.prksSaveWorkSource
  if (typeof save === 'function') void save(props.workId)
}

function resolveField(conflict: WorkMetadataConflict, apply: boolean): void {
  const resolve = window.prksResolveWorkMetadataFieldConflict
  if (typeof resolve === 'function') void resolve(conflict.opId, apply, conflict.group)
}

function resolveSource(conflict: WorkMetadataConflict, apply: boolean): void {
  const resolve = window.prksResolveWorkSourceConflict
  if (typeof resolve === 'function') void resolve(conflict.opId, apply)
}

function onFocus(field: WorkMetaField): void {
  emit('focusField', field)
}

function onBlur(field: WorkMetaField): void {
  emit('blurField', field)
}

function closeEditor(): void {
  const close = window.prksCancelWorkMetaEdit
  if (typeof close === 'function') void close()
}

function docTypeDisabled(): boolean {
  return isVideo || fieldOff('doc_type')
}

function syncDocTypeMenu(): void {
  const hidden = document.getElementById('meta-doc-type')
  const init = window.initPrksDocTypeMenu
  if (!hidden || typeof init !== 'function') return
  const before = props.draft.doc_type
  init('meta-doc-type', { disabled: docTypeDisabled(), selectedValue: before || 'article' })
  const shown = hidden instanceof HTMLInputElement ? hidden.value : before
  if (shown && shown !== props.draft.doc_type) props.draft.doc_type = shown
}

onMounted(() => {
  syncDocTypeMenu()
  const hidden = document.getElementById('meta-doc-type')
  const wrap = hidden?.closest('.prks-doc-type-menu')
  wrap?.addEventListener('click', () => {
    if (!(hidden instanceof HTMLInputElement)) return
    props.draft.doc_type = hidden.value
  })
  docMenuReady.value = true
  const panel = document.getElementById('panel-content')
  if (panel && typeof window.prksBindAutosizeTextareas === 'function') window.prksBindAutosizeTextareas(panel)
  if (typeof window.prksRefreshIcons === 'function') window.prksRefreshIcons(panel)
})

watch(docTypeDisabled, () => {
  syncDocTypeMenu()
})
</script>

<template>
  <div class="doc-meta-card form-pane doc-meta-card--editing work-meta-editor">
    <div class="card-heading-row">
      <h3 class="doc-meta-card__accent-title">Edit Metadata</h3>
      <button type="button" class="prks-icon-btn prks-icon-btn--ghost inline-action-btn inline-action-btn--close" aria-label="Close metadata editor" @click="closeEditor"><span aria-hidden="true">&times;</span></button>
    </div>

    <section class="work-meta-editor__section" data-prks-role="work-identity-editor">
      <h4>Identity</h4>
      <label for="meta-title">Title</label>
      <input
        id="meta-title"
        type="text"
        data-prks-work-field="title"
        v-model="draft.title"
        :disabled="fieldOff('title')"
        :title="fieldTitle('title')"
        :aria-invalid="invalid('title') ? 'true' : undefined"
        aria-describedby="meta-title-error"
        @focus="onFocus('title')"
        @blur="onBlur('title')"
      >
      <p id="meta-title-error" class="field-error" aria-live="polite">{{ errorText('title') }}</p>

      <label for="meta-doc-type-trigger">Document type (BibLaTeX)</label>
      <div class="prks-doc-type-menu combobox-container">
        <input id="meta-doc-type" type="hidden" name="meta-doc-type" data-prks-work-field="doc_type" :value="draft.doc_type">
        <button
          id="meta-doc-type-trigger"
          type="button"
          class="prks-doc-type-menu__trigger"
          aria-haspopup="listbox"
          aria-expanded="false"
          aria-controls="meta-doc-type-listbox"
          aria-label="BibLaTeX document type"
          :disabled="docTypeDisabled()"
        >
          <span class="prks-doc-type-menu__label">Document type</span>
          <span class="prks-doc-type-menu__caret"></span>
        </button>
        <div id="meta-doc-type-listbox" class="prks-doc-type-menu__panel hidden" role="listbox"></div>
      </div>
      <div class="prks-form-actions form-actions">
        <button id="save-work-identity-btn" type="button" class="prks-btn prks-btn--secondary" :disabled="saveDisabled('identity')" @click="saveGroup('identity')">Save identity</button>
      </div>
      <div class="meta-row" data-prks-role="work-identity-sync" aria-live="polite">
        <span>{{ statusLine('identity') }}</span>
        <div v-for="conflict in conflicts('identity')" :key="conflict.opId" :data-prks-work-field-conflict="conflict.field">
          {{ conflict.text }}
          <button
            v-for="action in conflict.actions"
            :key="action.label"
            type="button"
            class="prks-btn prks-btn--secondary prks-btn--sm"
            @click="resolveField(conflict, action.apply)"
          >{{ action.label }}</button>
        </div>
      </div>
    </section>

    <section class="work-meta-editor__section" data-prks-role="work-status-editor">
      <h4>Progress</h4>
      <div class="prks-work-upload-status-field">
        <label for="meta-status">Status</label>
        <div class="prks-segmented-wrap prks-segmented-wrap--status-row">
          <input id="meta-status" type="hidden" data-prks-work-field="status" :value="draft.status">
          <div class="prks-segmented prks-segmented--status prks-segmented--single-row" role="radiogroup" aria-label="Status">
            <button
              v-for="label in statuses"
              :key="label"
              type="button"
              class="prks-segmented__btn"
              :class="{ 'prks-segmented__btn--active': draft.status === label }"
              :data-value="label"
              role="radio"
              :aria-checked="draft.status === label ? 'true' : 'false'"
              :aria-label="label"
              :title="label"
              :disabled="fieldOff('status')"
              @click="chooseStatus(label)"
            >
              <span class="prks-segmented__btn-icon" v-html="statusIcon(label)"></span>
              <span class="prks-segmented__btn-label">{{ label }}</span>
            </button>
          </div>
        </div>
      </div>
      <div class="prks-form-actions form-actions">
        <button id="save-work-status-btn" type="button" class="prks-btn prks-btn--secondary" :disabled="saveDisabled('status')" @click="saveGroup('status')">Save status</button>
      </div>
      <div class="meta-row" data-prks-role="work-status-sync" aria-live="polite">
        <span>{{ statusLine('status') }}</span>
        <div v-for="conflict in conflicts('status')" :key="conflict.opId" :data-prks-work-field-conflict="conflict.field">
          {{ conflict.text }}
          <button
            v-for="action in conflict.actions"
            :key="action.label"
            type="button"
            class="prks-btn prks-btn--secondary prks-btn--sm"
            @click="resolveField(conflict, action.apply)"
          >{{ action.label }}</button>
        </div>
      </div>
    </section>

    <section v-if="isVideo" class="work-meta-editor__section" data-prks-role="work-source-editor">
      <h4>Video source</h4>
      <label for="meta-video-url">YouTube URL</label>
      <input
        id="meta-video-url"
        type="url"
        placeholder="https://www.youtube.com/watch?v=…"
        autocomplete="off"
        v-model="draft.source_url"
        :disabled="fieldOff('source_url')"
        :title="fieldTitle('source_url')"
        :aria-invalid="invalid('source_url') ? 'true' : undefined"
        aria-describedby="meta-video-url-error"
        @focus="onFocus('source_url')"
        @blur="onBlur('source_url')"
      >
      <p id="meta-video-url-error" class="field-error" aria-live="polite">{{ errorText('source_url') }}</p>
      <p class="meta-row meta-row--hint">Replaces which video this file is. Different links to the same video are the same source.</p>
      <div class="prks-form-actions form-actions">
        <button id="save-work-source-btn" type="button" class="prks-btn prks-btn--secondary" :disabled="saveDisabled('source')" @click="saveSource">Save video source</button>
      </div>
      <div class="meta-row" data-prks-role="work-source-sync" aria-live="polite">
        <span>{{ statusLine('source') }}</span>
        <div v-for="conflict in conflicts('source')" :key="conflict.opId" data-prks-work-source-conflict="">
          {{ conflict.text }}
          <button
            v-for="action in conflict.actions"
            :key="action.label"
            type="button"
            class="prks-btn prks-btn--secondary prks-btn--sm"
            @click="resolveSource(conflict, action.apply)"
          >{{ action.label }}</button>
        </div>
      </div>
    </section>

    <section v-if="isVideo" class="work-meta-editor__section" data-prks-role="work-bib-editor">
      <h4>Channel</h4>
      <label for="meta-author-text">Channel name</label>
      <input
        id="meta-author-text"
        type="text"
        data-prks-work-field="author_text"
        autocomplete="off"
        v-model="draft.author_text"
        :disabled="fieldOff('author_text')"
        :title="fieldTitle('author_text')"
        @focus="onFocus('author_text')"
        @blur="onBlur('author_text')"
      >
      <div class="prks-form-actions form-actions">
        <button id="save-work-bib-btn" type="button" class="prks-btn prks-btn--secondary" :disabled="saveDisabled('bib')" @click="saveGroup('bib')">Save channel name</button>
      </div>
      <div class="meta-row" data-prks-role="work-bib-sync" aria-live="polite">
        <span>{{ statusLine('bib') }}</span>
        <div v-for="conflict in conflicts('bib')" :key="conflict.opId">
          {{ conflict.text }}
          <button
            v-for="action in conflict.actions"
            :key="action.label"
            type="button"
            class="prks-btn prks-btn--secondary prks-btn--sm"
            @click="resolveField(conflict, action.apply)"
          >{{ action.label }}</button>
        </div>
      </div>
    </section>

    <section v-else class="work-meta-editor__section" data-prks-role="work-bib-editor">
      <h4>Bibliographic details</h4>
      <label for="meta-author-text">Author (text)</label>
      <input
        id="meta-author-text"
        type="text"
        data-prks-work-field="author_text"
        autocomplete="off"
        v-model="draft.author_text"
        :disabled="fieldOff('author_text')"
        :title="fieldTitle('author_text')"
        @focus="onFocus('author_text')"
        @blur="onBlur('author_text')"
      >
      <p class="meta-row meta-row--hint">Used for the credit line only when no Author is linked to this file. A linked Author always takes precedence; a linked Editor stands in when this is empty.</p>

      <div class="form-grid-2 form-grid-2--compact">
        <div>
          <label for="meta-year">Year</label>
          <input id="meta-year" type="text" data-prks-work-field="year" v-model="draft.year" :disabled="fieldOff('year')" :title="fieldTitle('year')" @focus="onFocus('year')" @blur="onBlur('year')">
        </div>
        <div>
          <label for="meta-date">Published Date</label>
          <input
            id="meta-date"
            type="text"
            data-prks-work-field="published_date"
            placeholder="dd/mm/yyyy"
            inputmode="numeric"
            autocomplete="off"
            aria-describedby="meta-date-error"
            v-model="draft.published_date"
            :disabled="fieldOff('published_date')"
            :title="fieldTitle('published_date')"
            :aria-invalid="invalid('published_date') ? 'true' : undefined"
            @focus="onFocus('published_date')"
            @blur="onBlur('published_date')"
          >
        </div>
      </div>
      <p id="meta-date-error" class="field-error" aria-live="polite">{{ errorText('published_date') }}</p>

      <label for="meta-publisher">Publisher</label>
      <input id="meta-publisher" type="text" data-prks-work-field="publisher" v-model="draft.publisher" :disabled="fieldOff('publisher')" :title="fieldTitle('publisher')" @focus="onFocus('publisher')" @blur="onBlur('publisher')">

      <label for="meta-location">Location (place of publication)</label>
      <input id="meta-location" type="text" data-prks-work-field="location" placeholder="e.g. Cambridge, UK or Paris; Berlin" autocomplete="off" v-model="draft.location" :disabled="fieldOff('location')" :title="fieldTitle('location')" @focus="onFocus('location')" @blur="onBlur('location')">
      <p class="meta-row meta-row--hint">Separate multiple places with semicolons; BibLaTeX export joins them with &quot; and &quot;.</p>

      <label for="meta-edition">Edition</label>
      <input id="meta-edition" type="text" data-prks-work-field="edition" placeholder="e.g. 2 or revised" autocomplete="off" v-model="draft.edition" :disabled="fieldOff('edition')" :title="fieldTitle('edition')" @focus="onFocus('edition')" @blur="onBlur('edition')">

      <label for="meta-journal">Journal</label>
      <input id="meta-journal" type="text" data-prks-work-field="journal" v-model="draft.journal" :disabled="fieldOff('journal')" :title="fieldTitle('journal')" @focus="onFocus('journal')" @blur="onBlur('journal')">

      <div class="form-grid-2 form-grid-2--compact">
        <div>
          <label for="meta-volume">Volume</label>
          <input id="meta-volume" type="text" data-prks-work-field="volume" v-model="draft.volume" :disabled="fieldOff('volume')" :title="fieldTitle('volume')" @focus="onFocus('volume')" @blur="onBlur('volume')">
        </div>
        <div>
          <label for="meta-issue">Issue</label>
          <input id="meta-issue" type="text" data-prks-work-field="issue" v-model="draft.issue" :disabled="fieldOff('issue')" :title="fieldTitle('issue')" @focus="onFocus('issue')" @blur="onBlur('issue')">
        </div>
      </div>

      <div class="form-grid-2 form-grid-2--compact">
        <div>
          <label for="meta-pages">Pages</label>
          <input id="meta-pages" type="text" data-prks-work-field="pages" v-model="draft.pages" :disabled="fieldOff('pages')" :title="fieldTitle('pages')" @focus="onFocus('pages')" @blur="onBlur('pages')">
        </div>
        <div>
          <label for="meta-isbn">ISBN</label>
          <input id="meta-isbn" type="text" data-prks-work-field="isbn" v-model="draft.isbn" :disabled="fieldOff('isbn')" :title="fieldTitle('isbn')" @focus="onFocus('isbn')" @blur="onBlur('isbn')">
        </div>
      </div>

      <label for="meta-doi">DOI</label>
      <input id="meta-doi" type="text" data-prks-work-field="doi" v-model="draft.doi" :disabled="fieldOff('doi')" :title="fieldTitle('doi')" @focus="onFocus('doi')" @blur="onBlur('doi')">

      <label for="meta-source-url">Original URL (optional)</label>
      <input
        id="meta-source-url"
        type="url"
        data-prks-work-field="source_url"
        placeholder="https://…"
        autocomplete="off"
        v-model="draft.source_url"
        :disabled="fieldOff('source_url')"
        :title="fieldTitle('source_url')"
        @focus="onFocus('source_url')"
        @blur="onBlur('source_url')"
      >
      <p class="meta-row meta-row--hint">Online location if this file was converted or downloaded from the web. This is provenance only: it does not change what kind of file PRKS treats this as.</p>

      <label for="meta-abstract">Abstract</label>
      <textarea
        id="meta-abstract"
        class="textarea-md"
        data-prks-work-field="abstract"
        v-model="draft.abstract"
        :disabled="fieldOff('abstract')"
        :title="fieldTitle('abstract')"
        @focus="onFocus('abstract')"
        @blur="onBlur('abstract')"
      ></textarea>

      <label for="meta-thumb-page">Thumbnail page</label>
      <input
        id="meta-thumb-page"
        type="number"
        data-prks-work-field="thumb_page"
        min="1"
        step="1"
        inputmode="numeric"
        placeholder="1"
        aria-describedby="meta-thumb-page-error"
        v-model="draft.thumb_page"
        :disabled="fieldOff('thumb_page')"
        :title="fieldTitle('thumb_page')"
        :aria-invalid="invalid('thumb_page') ? 'true' : undefined"
        @focus="onFocus('thumb_page')"
        @blur="onBlur('thumb_page')"
      >
      <p id="meta-thumb-page-error" class="field-error" aria-live="polite">{{ errorText('thumb_page') }}</p>
      <p class="meta-row meta-row--hint">Which page of the PDF to use as the card image. Leave empty for page 1.</p>

      <div class="prks-form-actions form-actions">
        <button id="save-work-bib-btn" type="button" class="prks-btn prks-btn--secondary" :disabled="saveDisabled('bib')" @click="saveGroup('bib')">Save bibliographic details</button>
      </div>
      <div class="meta-row" data-prks-role="work-bib-sync" aria-live="polite">
        <span>{{ statusLine('bib') }}</span>
        <div v-for="conflict in conflicts('bib')" :key="conflict.opId" :data-prks-work-field-conflict="conflict.field">
          {{ conflict.text }}
          <button
            v-for="action in conflict.actions"
            :key="action.label"
            type="button"
            class="prks-btn prks-btn--secondary prks-btn--sm"
            @click="resolveField(conflict, action.apply)"
          >{{ action.label }}</button>
        </div>
      </div>
    </section>

    <div class="prks-form-actions prks-form-actions--split form-actions work-meta-editor__sticky-actions">
      <button type="button" class="prks-btn prks-btn--secondary" @click="closeEditor">Close</button>
    </div>
  </div>
</template>
