<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksField from '../../components/PrksField.vue'
import PrksIconButton from '../../components/PrksIconButton.vue'
import { WORK_STATUSES } from '../../domain/work-status'
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
  updateField: [field: WorkMetaField, value: string]
}>()

const isVideo = props.sourceKind === 'video'
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

function setField(field: WorkMetaField, value: string): void {
  emit('updateField', field, value)
}

function onInput(field: WorkMetaField, event: Event): void {
  const target = event.target
  if (!(target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement)) return
  setField(field, target.value)
}

function chooseStatus(value: string): void {
  if (fieldOff('status')) return
  setField('status', value)
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
  if (shown && shown !== props.draft.doc_type) setField('doc_type', shown)
}

onMounted(() => {
  syncDocTypeMenu()
  const hidden = document.getElementById('meta-doc-type')
  const wrap = hidden?.closest('.prks-doc-type-menu')
  wrap?.addEventListener('click', () => {
    if (!(hidden instanceof HTMLInputElement)) return
    setField('doc_type', hidden.value)
  })
  docMenuReady.value = true
  const panel = document.getElementById('panel-content')
  if (panel && typeof window.prksBindAutosizeTextareas === 'function') window.prksBindAutosizeTextareas(panel)
  if (typeof window.prksRefreshIcons === 'function') window.prksRefreshIcons(panel)
})

watch(docTypeDisabled, () => {
  syncDocTypeMenu()
})

/* The label and listbox are imperative. An acknowledgement can change
 * draft.doc_type without a click, so the menu has to follow that value.
 * syncDocTypeMenu keeps its equality check so this watch does not loop. */
watch(() => props.draft.doc_type, () => {
  syncDocTypeMenu()
})
</script>

<template>
  <div class="doc-meta-card form-pane doc-meta-card--editing work-meta-editor">
    <div class="card-heading-row">
      <h3 class="doc-meta-card__accent-title">Edit Metadata</h3>
      <PrksIconButton variant="ghost" class="inline-action-btn inline-action-btn--close" label="Close metadata editor" @click="closeEditor"><span aria-hidden="true">&times;</span></PrksIconButton>
    </div>

    <section class="work-meta-editor__section" data-prks-role="work-identity-editor">
      <h4>Identity</h4>
      <PrksField v-slot="{ labelledBy, describedBy }" label="Title" for-id="meta-title" :error="errorText('title')">
      <input
        id="meta-title"
        type="text"
        data-prks-work-field="title"
        :value="draft.title"
        @input="onInput('title', $event)"
        :disabled="fieldOff('title')"
        :title="fieldTitle('title')"
        :aria-invalid="invalid('title') ? 'true' : undefined"
        :aria-labelledby="labelledBy"
        :aria-describedby="describedBy"
        @focus="onFocus('title')"
        @blur="onBlur('title')"
      >
      </PrksField>

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
        <PrksButton id="save-work-identity-btn" :disabled="saveDisabled('identity')" @click="saveGroup('identity')">Save identity</PrksButton>
      </div>
      <div class="meta-row" data-prks-role="work-identity-sync" aria-live="polite">
        <span>{{ statusLine('identity') }}</span>
        <div v-for="conflict in conflicts('identity')" :key="conflict.opId" :data-prks-work-field-conflict="conflict.field">
          {{ conflict.text }}
          <PrksButton
            v-for="action in conflict.actions"
            :key="action.label"
            size="sm"
            @click="resolveField(conflict, action.apply)"
          >{{ action.label }}</PrksButton>
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
              v-for="label in WORK_STATUSES"
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
        <PrksButton id="save-work-status-btn" :disabled="saveDisabled('status')" @click="saveGroup('status')">Save status</PrksButton>
      </div>
      <div class="meta-row" data-prks-role="work-status-sync" aria-live="polite">
        <span>{{ statusLine('status') }}</span>
        <div v-for="conflict in conflicts('status')" :key="conflict.opId" :data-prks-work-field-conflict="conflict.field">
          {{ conflict.text }}
          <PrksButton
            v-for="action in conflict.actions"
            :key="action.label"
            size="sm"
            @click="resolveField(conflict, action.apply)"
          >{{ action.label }}</PrksButton>
        </div>
      </div>
    </section>

    <section v-if="isVideo" class="work-meta-editor__section" data-prks-role="work-source-editor">
      <h4>Video source</h4>
      <PrksField
        v-slot="{ labelledBy, describedBy }"
        label="YouTube URL"
        for-id="meta-video-url"
        :error="errorText('source_url')"
        help="Replaces which video this file is. Different links to the same video are the same source."
      >
      <input
        id="meta-video-url"
        type="url"
        placeholder="https://www.youtube.com/watch?v=…"
        autocomplete="off"
        :value="draft.source_url"
        @input="onInput('source_url', $event)"
        :disabled="fieldOff('source_url')"
        :title="fieldTitle('source_url')"
        :aria-invalid="invalid('source_url') ? 'true' : undefined"
        :aria-labelledby="labelledBy"
        :aria-describedby="describedBy"
        @focus="onFocus('source_url')"
        @blur="onBlur('source_url')"
      >
      </PrksField>
      <div class="prks-form-actions form-actions">
        <PrksButton id="save-work-source-btn" :disabled="saveDisabled('source')" @click="saveSource">Save video source</PrksButton>
      </div>
      <div class="meta-row" data-prks-role="work-source-sync" aria-live="polite">
        <span>{{ statusLine('source') }}</span>
        <div v-for="conflict in conflicts('source')" :key="conflict.opId" data-prks-work-source-conflict="">
          {{ conflict.text }}
          <PrksButton
            v-for="action in conflict.actions"
            :key="action.label"
            size="sm"
            @click="resolveSource(conflict, action.apply)"
          >{{ action.label }}</PrksButton>
        </div>
      </div>
    </section>

    <section class="work-meta-editor__section" data-prks-role="work-bib-editor">
      <h4>{{ isVideo ? 'Channel' : 'Bibliographic details' }}</h4>
      <PrksField
        v-slot="{ labelledBy, describedBy }"
        :label="isVideo ? 'Channel name' : 'Author (text)'"
        for-id="meta-author-text"
        :help="isVideo ? undefined : 'Used for the credit line only when no Author is linked to this file. A linked Author always takes precedence; a linked Editor stands in when this is empty.'"
      >
      <input
        id="meta-author-text"
        type="text"
        data-prks-work-field="author_text"
        autocomplete="off"
        :value="draft.author_text"
        @input="onInput('author_text', $event)"
        :disabled="fieldOff('author_text')"
        :title="fieldTitle('author_text')"
        :aria-labelledby="labelledBy"
        :aria-describedby="describedBy"
        @focus="onFocus('author_text')"
        @blur="onBlur('author_text')"
      >
      </PrksField>
      <template v-if="!isVideo">

      <div class="form-grid-2 form-grid-2--compact">
        <PrksField v-slot="{ labelledBy, describedBy }" label="Year" for-id="meta-year">
          <input id="meta-year" type="text" data-prks-work-field="year" :value="draft.year" @input="onInput('year', $event)" :disabled="fieldOff('year')" :title="fieldTitle('year')" :aria-labelledby="labelledBy" :aria-describedby="describedBy" @focus="onFocus('year')" @blur="onBlur('year')">
        </PrksField>
        <PrksField v-slot="{ labelledBy, describedBy }" label="Published Date" for-id="meta-date" :error="errorText('published_date')">
          <input
            id="meta-date"
            type="text"
            data-prks-work-field="published_date"
            placeholder="dd/mm/yyyy"
            inputmode="numeric"
            autocomplete="off"
            :value="draft.published_date"
            @input="onInput('published_date', $event)"
            :disabled="fieldOff('published_date')"
            :title="fieldTitle('published_date')"
            :aria-invalid="invalid('published_date') ? 'true' : undefined"
            :aria-labelledby="labelledBy"
            :aria-describedby="describedBy"
            @focus="onFocus('published_date')"
            @blur="onBlur('published_date')"
          >
        </PrksField>
      </div>

      <PrksField v-slot="{ labelledBy, describedBy }" label="Publisher" for-id="meta-publisher">
      <input id="meta-publisher" type="text" data-prks-work-field="publisher" :value="draft.publisher" @input="onInput('publisher', $event)" :disabled="fieldOff('publisher')" :title="fieldTitle('publisher')" :aria-labelledby="labelledBy" :aria-describedby="describedBy" @focus="onFocus('publisher')" @blur="onBlur('publisher')">
      </PrksField>

      <PrksField
        v-slot="{ labelledBy, describedBy }"
        label="Location (place of publication)"
        for-id="meta-location"
        help="Separate multiple places with semicolons; BibLaTeX export joins them with &quot; and &quot;."
      >
      <input id="meta-location" type="text" data-prks-work-field="location" placeholder="e.g. Cambridge, UK or Paris; Berlin" autocomplete="off" :value="draft.location" @input="onInput('location', $event)" :disabled="fieldOff('location')" :title="fieldTitle('location')" :aria-labelledby="labelledBy" :aria-describedby="describedBy" @focus="onFocus('location')" @blur="onBlur('location')">
      </PrksField>

      <PrksField v-slot="{ labelledBy, describedBy }" label="Edition" for-id="meta-edition">
      <input id="meta-edition" type="text" data-prks-work-field="edition" placeholder="e.g. 2 or revised" autocomplete="off" :value="draft.edition" @input="onInput('edition', $event)" :disabled="fieldOff('edition')" :title="fieldTitle('edition')" :aria-labelledby="labelledBy" :aria-describedby="describedBy" @focus="onFocus('edition')" @blur="onBlur('edition')">
      </PrksField>

      <PrksField v-slot="{ labelledBy, describedBy }" label="Journal" for-id="meta-journal">
      <input id="meta-journal" type="text" data-prks-work-field="journal" :value="draft.journal" @input="onInput('journal', $event)" :disabled="fieldOff('journal')" :title="fieldTitle('journal')" :aria-labelledby="labelledBy" :aria-describedby="describedBy" @focus="onFocus('journal')" @blur="onBlur('journal')">
      </PrksField>

      <div class="form-grid-2 form-grid-2--compact">
        <PrksField v-slot="{ labelledBy, describedBy }" label="Volume" for-id="meta-volume">
          <input id="meta-volume" type="text" data-prks-work-field="volume" :value="draft.volume" @input="onInput('volume', $event)" :disabled="fieldOff('volume')" :title="fieldTitle('volume')" :aria-labelledby="labelledBy" :aria-describedby="describedBy" @focus="onFocus('volume')" @blur="onBlur('volume')">
        </PrksField>
        <PrksField v-slot="{ labelledBy, describedBy }" label="Issue" for-id="meta-issue">
          <input id="meta-issue" type="text" data-prks-work-field="issue" :value="draft.issue" @input="onInput('issue', $event)" :disabled="fieldOff('issue')" :title="fieldTitle('issue')" :aria-labelledby="labelledBy" :aria-describedby="describedBy" @focus="onFocus('issue')" @blur="onBlur('issue')">
        </PrksField>
      </div>

      <div class="form-grid-2 form-grid-2--compact">
        <PrksField v-slot="{ labelledBy, describedBy }" label="Pages" for-id="meta-pages">
          <input id="meta-pages" type="text" data-prks-work-field="pages" :value="draft.pages" @input="onInput('pages', $event)" :disabled="fieldOff('pages')" :title="fieldTitle('pages')" :aria-labelledby="labelledBy" :aria-describedby="describedBy" @focus="onFocus('pages')" @blur="onBlur('pages')">
        </PrksField>
        <PrksField v-slot="{ labelledBy, describedBy }" label="ISBN" for-id="meta-isbn">
          <input id="meta-isbn" type="text" data-prks-work-field="isbn" :value="draft.isbn" @input="onInput('isbn', $event)" :disabled="fieldOff('isbn')" :title="fieldTitle('isbn')" :aria-labelledby="labelledBy" :aria-describedby="describedBy" @focus="onFocus('isbn')" @blur="onBlur('isbn')">
        </PrksField>
      </div>

      <PrksField v-slot="{ labelledBy, describedBy }" label="DOI" for-id="meta-doi">
      <input id="meta-doi" type="text" data-prks-work-field="doi" :value="draft.doi" @input="onInput('doi', $event)" :disabled="fieldOff('doi')" :title="fieldTitle('doi')" :aria-labelledby="labelledBy" :aria-describedby="describedBy" @focus="onFocus('doi')" @blur="onBlur('doi')">
      </PrksField>

      <PrksField
        v-slot="{ labelledBy, describedBy }"
        label="Original URL (optional)"
        for-id="meta-source-url"
        help="Online location if this file was converted or downloaded from the web. This is provenance only: it does not change what kind of file PRKS treats this as."
      >
      <input
        id="meta-source-url"
        type="url"
        data-prks-work-field="source_url"
        placeholder="https://…"
        autocomplete="off"
        :value="draft.source_url"
        @input="onInput('source_url', $event)"
        :disabled="fieldOff('source_url')"
        :title="fieldTitle('source_url')"
        :aria-labelledby="labelledBy"
        :aria-describedby="describedBy"
        @focus="onFocus('source_url')"
        @blur="onBlur('source_url')"
      >
      </PrksField>

      <PrksField v-slot="{ labelledBy, describedBy }" label="Abstract" for-id="meta-abstract">
      <textarea
        id="meta-abstract"
        class="textarea-md"
        data-prks-work-field="abstract"
        :value="draft.abstract"
        @input="onInput('abstract', $event)"
        :disabled="fieldOff('abstract')"
        :title="fieldTitle('abstract')"
        :aria-labelledby="labelledBy"
        :aria-describedby="describedBy"
        @focus="onFocus('abstract')"
        @blur="onBlur('abstract')"
      ></textarea>
      </PrksField>

      <PrksField
        v-slot="{ labelledBy, describedBy }"
        label="Thumbnail page"
        for-id="meta-thumb-page"
        :error="errorText('thumb_page')"
        help="Which page of the PDF to use as the card image. Leave empty for page 1."
      >
      <input
        id="meta-thumb-page"
        type="number"
        data-prks-work-field="thumb_page"
        min="1"
        step="1"
        inputmode="numeric"
        placeholder="1"
        :value="draft.thumb_page"
        @input="onInput('thumb_page', $event)"
        :disabled="fieldOff('thumb_page')"
        :title="fieldTitle('thumb_page')"
        :aria-invalid="invalid('thumb_page') ? 'true' : undefined"
        :aria-labelledby="labelledBy"
        :aria-describedby="describedBy"
        @focus="onFocus('thumb_page')"
        @blur="onBlur('thumb_page')"
      >
      </PrksField>
      </template>

      <div class="prks-form-actions form-actions">
        <PrksButton id="save-work-bib-btn" :disabled="saveDisabled('bib')" @click="saveGroup('bib')">{{ isVideo ? 'Save channel name' : 'Save bibliographic details' }}</PrksButton>
      </div>
      <div class="meta-row" data-prks-role="work-bib-sync" aria-live="polite">
        <span>{{ statusLine('bib') }}</span>
        <div v-for="conflict in conflicts('bib')" :key="conflict.opId" :data-prks-work-field-conflict="conflict.field">
          {{ conflict.text }}
          <PrksButton
            v-for="action in conflict.actions"
            :key="action.label"
            size="sm"
            @click="resolveField(conflict, action.apply)"
          >{{ action.label }}</PrksButton>
        </div>
      </div>
    </section>

    <div class="prks-form-actions prks-form-actions--split form-actions work-meta-editor__sticky-actions">
      <PrksButton @click="closeEditor">Close</PrksButton>
    </div>
  </div>
</template>
