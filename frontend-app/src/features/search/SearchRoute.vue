<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import SearchResultsCollection from './SearchResultsCollection.vue'
import type { SearchIntents } from './intents'
import type { SearchRouteProjection } from './projection'

const props = defineProps<{
  projection: SearchRouteProjection
  intents: SearchIntents
}>()

const rootEl = ref<HTMLElement | null>(null)
const modeHost = ref<HTMLElement | null>(null)

const request = computed(() => props.projection.request)
const showForm = computed(() => !request.value.tag)
const searchIcon = computed(() => window.prksTagSearchIconHtml?.() ?? '')
const scopeHtml = computed(() => {
  const total = props.projection.results.rows.length
  return window.prksScopeLineHtml?.({ total, label: total === 1 ? 'result' : 'results' }) ?? ''
})

const q = ref('')
const author = ref('')
const publisher = ref('')

function resetDraft(): void {
  q.value = request.value.q
  author.value = request.value.author
  publisher.value = request.value.publisher
}

resetDraft()
watch(request, resetDraft)

function run(): void {
  props.intents.run({
    any: request.value.any,
    q: q.value,
    author: author.value,
    publisher: publisher.value,
  })
}

function paintMode(): void {
  const host = modeHost.value
  if (!host) return
  host.innerHTML = window.prksWorkBrowseModeToggleHtml?.('prks-work-browse-mode-search') ?? ''
  window.prksBindWorkBrowseMode?.(rootEl.value)
  window.prksRefreshIcons?.(rootEl.value)
}

onMounted(paintMode)
watch(() => props.projection.generation, paintMode, { flush: 'post' })
</script>

<template>
  <div ref="rootEl" data-prks-search-view>
    <div class="prks-page-header page-header page-header--search">
      <div class="page-header__title-row">
        <h2 class="prks-page-title">{{ projection.title }}</h2>
        <div class="page-header__actions">
          <div ref="modeHost" class="work-html-slot" data-prks-search-mode-host></div>
          <PrksButton
            v-if="projection.canOfferSave"
            id="prks-save-view-btn"
            @click="intents.saveView()"
          >
            Save View
          </PrksButton>
        </div>
      </div>
      <div class="work-html-slot" v-html="scopeHtml"></div>
      <div v-if="showForm" class="search-advanced" role="search">
        <div v-if="request.any" class="search-advanced__row">
          <label class="search-advanced__label" for="search-any-input">All</label>
          <div class="tag-add-shell">
            <div class="tag-add-shell__field">
              <span class="work-html-slot" v-html="searchIcon"></span>
              <input
                id="search-any-input"
                v-model="q"
                type="search"
                class="tag-add-shell__input"
                placeholder="Title, notes, people, publisher…"
                maxlength="500"
                autocomplete="off"
                aria-label="Search all fields"
                @keydown.enter="run"
              />
            </div>
          </div>
        </div>
        <template v-else>
          <div class="search-advanced__row">
            <label class="search-advanced__label" for="search-q-input">Keywords</label>
            <div class="tag-add-shell">
              <div class="tag-add-shell__field">
                <span class="work-html-slot" v-html="searchIcon"></span>
                <input
                  id="search-q-input"
                  v-model="q"
                  type="search"
                  class="tag-add-shell__input"
                  placeholder="Title, notes, abstract, numbers…"
                  maxlength="500"
                  autocomplete="off"
                  aria-label="Search keywords"
                  @keydown.enter="run"
                />
              </div>
            </div>
          </div>
          <div class="search-advanced__row">
            <label class="search-advanced__label" for="search-author-input">Author</label>
            <div class="tag-add-shell">
              <div class="tag-add-shell__field">
                <span class="work-html-slot" v-html="searchIcon"></span>
                <input
                  id="search-author-input"
                  v-model="author"
                  type="search"
                  class="tag-add-shell__input"
                  placeholder="Name in metadata or linked person…"
                  maxlength="200"
                  autocomplete="off"
                  aria-label="Search by author"
                  @keydown.enter="run"
                />
              </div>
            </div>
          </div>
          <div class="search-advanced__row">
            <label class="search-advanced__label" for="search-publisher-input">Publisher</label>
            <div class="tag-add-shell">
              <div class="tag-add-shell__field">
                <span class="work-html-slot" v-html="searchIcon"></span>
                <input
                  id="search-publisher-input"
                  v-model="publisher"
                  type="search"
                  class="tag-add-shell__input"
                  placeholder="Publisher field; alternate names from Publishers page…"
                  maxlength="200"
                  autocomplete="off"
                  aria-label="Search by publisher"
                  @keydown.enter="run"
                />
              </div>
            </div>
          </div>
        </template>
        <PrksButton id="search-run-btn" class="search-advanced__submit" @click="run">
          Search
        </PrksButton>
      </div>
    </div>
    <SearchResultsCollection :projection="projection.results" />
  </div>
</template>
