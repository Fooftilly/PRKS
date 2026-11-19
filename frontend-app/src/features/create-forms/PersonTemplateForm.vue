<script setup lang="ts">
/**
 * The Edit Profile Using Template dialog body (#1036).
 *
 * ModalHost renders it as the body of the `#person-template-modal`
 * ModalDialog. The opener (`openPersonProfileTemplateModal`, the Person sidebar's "Edit using
 * template") is installed while this form is mounted. Apply opens the
 * focused person's profile editor and fills it; the editor's Save is the
 * only write, so this dialog has no unsaved-changes confirm.
 */
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksField from '../../components/PrksField.vue'
import { focusField } from './keyboard'
import {
  copyText,
  fillProfileEditor,
  formatPersonTemplate,
  parsePersonTemplate,
  PERSON_TEMPLATE_HELP_TEXT,
  templateFromPerson,
  type PersonTemplateKey,
} from './person-template/template'

const MODAL_ID = 'person-template-modal'
const TEXTAREA_ID = 'person-template-json'
const FLASH_MS = 1500

type CopyKind = 'guide' | 'both'

interface PersonTemplateWindow {
  openModal?: (id: string) => void
  closeModals?: () => void
  prksFocusedEntity?: (type: string) => Partial<Record<PersonTemplateKey, unknown>> | null
  personDateToDisplayFormat?: (value: string) => string
  openPersonProfileEdit?: () => unknown
  prksAutosizeTextarea?: (el: HTMLTextAreaElement) => void
  prksIcon?: (name: string, opts?: { size?: string }) => string
  prksRefreshIcons?: (root?: ParentNode) => void
  openPersonProfileTemplateModal?: () => void
}

function runtime(): PersonTemplateWindow {
  return window as unknown as PersonTemplateWindow
}

const text = ref('')
const feedback = ref('')
const feedbackError = ref(false)
const applying = ref(false)
/** The copy button showing its Copied / Copy failed state, and whether it failed. */
const flashed = ref<{ kind: CopyKind; ok: boolean } | null>(null)
let flashTimer: ReturnType<typeof setTimeout> | null = null
const textareaEl = ref<HTMLTextAreaElement | null>(null)
const guideIconEl = ref<HTMLElement | null>(null)
const bothIconEl = ref<HTMLElement | null>(null)

function say(message: string, isError = false): void {
  feedback.value = message
  feedbackError.value = isError
}

function currentTemplate(): string {
  const w = runtime()
  const person = typeof w.prksFocusedEntity === 'function' ? w.prksFocusedEntity('person') : null
  const display = typeof w.personDateToDisplayFormat === 'function' ? w.personDateToDisplayFormat : undefined
  return formatPersonTemplate(templateFromPerson(person, display))
}

async function autosize(): Promise<void> {
  await nextTick()
  const el = textareaEl.value
  if (el) runtime().prksAutosizeTextarea?.(el)
}

/** Fills the template from the focused person and clears any message. */
function prepare(): void {
  text.value = currentTemplate()
  say('')
  clearFlash()
  void autosize()
}

function open(): void {
  prepare()
  runtime().openModal?.(MODAL_ID)
}

function reload(): void {
  text.value = currentTemplate()
  say('Template loaded from current profile values.')
  void autosize()
}

function clearFlash(): void {
  if (flashTimer) clearTimeout(flashTimer)
  flashTimer = null
  flashed.value = null
}

async function paintIcons(): Promise<void> {
  await nextTick()
  const w = runtime()
  for (const el of [guideIconEl.value, bothIconEl.value]) if (el) w.prksRefreshIcons?.(el)
}

function iconHtml(kind: CopyKind): string {
  const w = runtime()
  const icon = (name: string, size?: string) =>
    typeof w.prksIcon === 'function' ? w.prksIcon(name, size ? { size } : undefined) : ''
  const state = flashed.value
  if (state && state.kind === kind) return icon(state.ok ? 'check' : 'x', 'sm')
  return kind === 'guide' ? icon('copy') : icon('copy') + icon('plus', 'sm')
}

function copyLabel(kind: CopyKind): string {
  const state = flashed.value
  if (state && state.kind === kind) return state.ok ? 'Copied' : 'Copy failed'
  return kind === 'guide' ? 'Copy template field guide' : 'Copy template and field guide'
}

async function copy(kind: CopyKind): Promise<void> {
  const template = text.value.trim() || currentTemplate()
  const payload = kind === 'guide' ? PERSON_TEMPLATE_HELP_TEXT : `${template}\n\n${PERSON_TEMPLATE_HELP_TEXT}`
  const ok = await copyText(payload)
  if (kind === 'guide') say(ok ? 'Template field guide copied.' : 'Could not copy guide. Copy manually.', !ok)
  else say(ok ? 'Template + field guide copied.' : 'Could not copy template + guide. Copy manually.', !ok)
  clearFlash()
  flashed.value = { kind, ok }
  flashTimer = setTimeout(() => {
    flashed.value = null
    flashTimer = null
    void paintIcons()
  }, FLASH_MS)
  void paintIcons()
}

async function apply(): Promise<void> {
  if (applying.value) return
  const parsed = parsePersonTemplate(text.value)
  if (!parsed.ok) {
    say(parsed.error, true)
    return
  }
  applying.value = true
  try {
    const w = runtime()
    await w.openPersonProfileEdit?.()
    // The profile editor is the Vue Person route; let it render its fields.
    await nextTick()
    const filled = fillProfileEditor(parsed.values)
    if (!filled.ok) {
      say(filled.error, true)
      return
    }
    for (const id of ['pd-about', 'pd-links-other']) {
      const el = document.getElementById(id)
      if (el instanceof HTMLTextAreaElement) w.prksAutosizeTextarea?.(el)
    }
    w.closeModals?.()
  } finally {
    applying.value = false
  }
}

function focusInitial(): void {
  focusField(TEXTAREA_ID)
}

defineExpose({ prepare, focusInitial })

onMounted(() => {
  runtime().openPersonProfileTemplateModal = open
  void paintIcons()
})

onBeforeUnmount(() => {
  clearFlash()
  const w = runtime()
  if (w.openPersonProfileTemplateModal === open) delete w.openPersonProfileTemplateModal
})
</script>

<template>
  <div class="form-pane">
    <p class="modal-helper">Template uses strict JSON. Keep exact keys, string values only. Paste edited template then apply to open profile edit form.</p>
    <div class="prks-inline-message">
      <p class="meta-row meta-row--compact prks-cluster">
        <strong>How to fill template fields</strong>
        <button
          type="button"
          class="prks-btn prks-btn--ghost prks-btn--sm inline-action-btn"
          :class="{
            'inline-action-btn--copied': flashed?.kind === 'guide' && flashed.ok,
            'inline-action-btn--error': flashed?.kind === 'guide' && !flashed.ok,
          }"
          data-prks-copy="guide"
          :title="copyLabel('guide')"
          :aria-label="copyLabel('guide')"
          @click="copy('guide')"
        ><span ref="guideIconEl" style="display: contents" v-html="iconHtml('guide')"></span></button>
        <button
          type="button"
          class="prks-btn prks-btn--ghost prks-btn--sm inline-action-btn"
          :class="{
            'inline-action-btn--copied': flashed?.kind === 'both' && flashed.ok,
            'inline-action-btn--error': flashed?.kind === 'both' && !flashed.ok,
          }"
          data-prks-copy="both"
          :title="copyLabel('both')"
          :aria-label="copyLabel('both')"
          @click="copy('both')"
        ><span ref="bothIconEl" style="display: contents" v-html="iconHtml('both')"></span></button>
      </p>
        <p>Fill this template according to the provided rules:</p>
        <ul class="prks-guide-list">
        <li><code>first_name</code>, <code>last_name</code>, <code>aliases</code>: plain text strings. Middle names should be placed in <code>first_name</code> field.</li>
        <li><code>about</code>: MUST review and update when filling this template; DO NOT leave unchanged or empty if you are enriching other fields and reliable sources exist. Short encyclopedic biography for person cards and search (About / expertise). Include who they are, time period, field/domain, 2–4 main ideas/works/contributions, and why they are notable. DO NOT put birth year, death year, or lifespan dates in <code>about</code>; use <code>birth_date</code> and <code>death_date</code> only. Neutral third-person prose, complete sentences, plain text only (JSON string; optional blank line between paragraphs as <code>\n\n</code>). No Markdown, bullet lists, or URLs (use <code>link_*</code> and <code>links_other</code>). Target ~80–500 words (~400–2500 characters); avoid one-liners and essay-length dumps. Synthesize from Wikipedia, SEP, IEP, or provided links; do not invent facts. If existing text is present, improve or expand it rather than skipping this field.</li>
        <li><code>birth_date</code>, <code>death_date</code>: plain date strings only — never prose, never <code>BC</code>, <code>BCE</code>, or <code>AD</code> suffixes. Allowed: (1) AD year-only positive <code>yyyy</code>, e.g. <code>1903</code>. (2) BC year-only: leading minus + BC year digits, e.g. source says 428 BC → <code>-428</code> (same for 384 BC → <code>-384</code>). (3) Full date <code>dd/mm/yyyy</code>; BC uses negative year in last segment, e.g. 15 March 384 BC → <code>15/03/-384</code>; AD e.g. <code>20/02/2001</code>. Wrong: <code>384 BC</code>, <code>428 BCE</code>, <code>-384 BC</code>, <code>born 428 BC</code>. Use day and month only if source states them; else year-only. Unknown date → <code>""</code>.</li>
        <li><code>image_url</code>: direct public HTTP/HTTPS portrait URL only (<code>https://</code> preferred). Static JPEG/PNG/WebP/GIF raster only. The URL itself must return HTTP 200 with the image; PRKS does not follow redirects. Private/local network URLs are not accepted. MUST verify the link works before filling (use GET or HEAD, or open in browser). If 404, 403, login wall, HTML error page, redirect, or broken hotlink, set <code>image_url</code> to <code>""</code>. Prefer current stable <code>upload.wikimedia.org</code> (or equivalent direct file) URLs from the person’s verified Wikimedia/Wikipedia file page — not guessed filenames, not old revision URLs, not search-result or temporary CDN/signed links. Do not invent URLs from memory. If no working portrait URL is confirmed, leave empty.</li>
        <li><code>link_wikipedia</code>, <code>link_stanford_encyclopedia</code>, <code>link_iep</code>: bare URL string only (starts with <code>https://</code>). Wrong: <code>[Title](https://...)</code>, Markdown, or labels — <code>[Title](url)</code> format is <strong>only</strong> for <code>links_other</code>. One direct entry URL per field; prefer <code>https://</code>; not homepage/search URL. Do not repeat in <code>links_other</code>. Example <code>link_wikipedia</code>: <code>https://en.wikipedia.org/wiki/Example_Name</code>.</li>
        <li><code>links_other</code>: one entry per line inside single JSON string. MUST USE <code>[Title](https://...)</code> Markdown link format per line (this is the only field that uses brackets). Only additional references not already in <code>link_wikipedia</code>, <code>link_stanford_encyclopedia</code>, or <code>link_iep</code>. DO NOT duplicate Wikipedia, Stanford Encyclopedia of Philosophy, or IEP links here. DO NOT add free-text notes, DOI links, paper links, or links by this person unless biographical.</li>
        <li>empty fields should stay empty strings; do not fill placeholders such as N/A, unknown, none, -, or TBD.</li>
        <li><code>aliases</code>: free text; comma-separated aliases recommended. Include Serbian Latin (not Cyrillic) variation if it exists. Include common spelling/transliteration variants and first+last version when middle name exists. Do not include titles/honorifics (Dr., Prof., Sir) as aliases.</li>
        <li>Keep exact keys; do not add/remove keys.</li>
        <li>Keep all values as JSON strings.</li>
        </ul>
    </div>
    <PrksField v-slot="{ labelledBy, describedBy }" label="Profile template" :for-id="TEXTAREA_ID">
      <textarea
        :id="TEXTAREA_ID"
        ref="textareaEl"
        v-model="text"
        class="textarea-sm"
        placeholder="Use &quot;Reload template from profile&quot; to fill JSON from the open person’s current fields."
        :aria-labelledby="labelledBy"
        :aria-describedby="describedBy"
      ></textarea>
    </PrksField>
    <div class="prks-form-actions prks-form-actions--split form-actions">
      <PrksButton variant="secondary" @click="reload">Reload template from profile</PrksButton>
      <PrksButton variant="primary" :busy="applying" busy-label="Applying…" @click="apply">Apply template</PrksButton>
    </div>
    <p
      id="person-template-feedback"
      class="prks-inline-message"
      :class="{ 'prks-inline-message--error': feedbackError }"
      :role="feedbackError ? 'alert' : 'status'"
    >{{ feedback }}</p>
  </div>
</template>
