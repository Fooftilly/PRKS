<script setup lang="ts">
/**
 * New File (Work) create form body (#1036, step NF-1).
 *
 * Markup only, so far. The classic runtime (`ui.js` New File helpers and the
 * `save-work-btn` submit in `app.js`) still binds these ids at
 * DOMContentLoaded, which runs after this module script mounts. Nothing here
 * is reactive, so Vue never re-renders the tree and the classic DOM writes
 * (classes, chips, preview, errors) stand. Later steps move each section's
 * behavior in here; the shell, header and sticky footer stay in
 * `frontend/index.html` until ModalHost mounts feature forms.
 */
</script>

<template>
  <div class="upload-grid form-pane">
      <section class="prks-create-section prks-create-section--source" aria-labelledby="work-create-source-heading">
          <div class="prks-create-section__head">
              <h4 id="work-create-source-heading" class="prks-create-section__title">Source</h4>
              <span class="prks-field__required">Required</span>
              <p id="work-file-kind-label" class="prks-sr-only">Source type</p>
              <input type="hidden" id="work-source-kind" value="pdf">
              <div class="prks-kind-toggle prks-kind-toggle--compact" role="tablist" aria-labelledby="work-file-kind-label">
                  <button type="button" class="prks-kind-toggle__btn is-active" data-kind="pdf" role="tab" aria-selected="true">
                      <span class="prks-kind-toggle__icon"><i data-lucide="file-text" class="prks-icon" aria-hidden="true"></i></span>
                      <span class="prks-kind-toggle__label">PDF</span>
                  </button>
                  <button type="button" class="prks-kind-toggle__btn" data-kind="video" role="tab" aria-selected="false">
                      <span class="prks-kind-toggle__icon"><i data-lucide="play" class="prks-icon" aria-hidden="true"></i></span>
                      <span class="prks-kind-toggle__label">YouTube</span>
                  </button>
              </div>
          </div>

          <div id="upload-source-pdf">
              <div class="preview-pane" id="upload-drop-zone" tabindex="0" role="button" aria-labelledby="drop-zone-label" aria-describedby="work-file-error">
                  <div id="drop-zone-prompt" class="drop-zone drop-zone--inline">
                      <span class="drop-zone__icon"><i data-lucide="file-text" class="prks-icon" aria-hidden="true"></i></span>
                      <p id="drop-zone-label">Drop a PDF here <span class="drop-zone__label-sub">or click to browse · PDF files only</span></p>
                  </div>
                  <div id="upload-selected-file" class="upload-selected-file hidden">
                      <span class="upload-selected-file__icon" aria-hidden="true"><i data-lucide="file-text" class="prks-icon"></i></span>
                      <div class="upload-selected-file__meta">
                          <p class="upload-selected-file__kind">PDF</p>
                          <p class="upload-selected-file__name" id="upload-selected-file-name"></p>
                          <p class="upload-selected-file__size" id="upload-selected-file-size"></p>
                      </div>
                      <button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" id="upload-pdf-change-btn" title="Choose a different PDF">Change</button>
                  </div>
                  <div id="upload-pdf-preview-actions" class="upload-pdf-preview-actions hidden" hidden></div>
                  <input type="file" id="work-file" accept=".pdf,application/pdf" class="file-input-hidden" aria-label="PDF file" aria-required="true">
              </div>
              <p id="work-file-error" class="folder-field-error hidden" data-prks-field-error="work-file" role="alert"></p>
          </div>

          <div id="work-video-url-row" class="prks-field hidden">
              <label for="work-video-url">YouTube URL</label>
              <input type="url" id="work-video-url" placeholder="https://www.youtube.com/watch?v=…" autocomplete="off" aria-required="true" aria-describedby="work-video-url-hint work-video-url-error">
              <p id="work-video-url-error" class="folder-field-error hidden" data-prks-field-error="work-video-url" role="alert"></p>
              <p id="work-video-url-hint" class="meta-row meta-row--hint meta-row--compact">Title and channel are filled in from YouTube when possible.</p>
              <div id="upload-viewer" class="upload-video-preview hidden"></div>
          </div>
      </section>

      <section class="prks-create-section" aria-labelledby="work-create-basic-heading">
          <h4 id="work-create-basic-heading" class="prks-create-section__title">Basic information</h4>

          <div class="prks-field">
              <label for="work-title">Title</label>
              <input type="text" id="work-title" placeholder="e.g. Capital Volume 1" autocomplete="off" aria-describedby="work-title-error">
              <p id="work-title-error" class="folder-field-error hidden" data-prks-field-error="work-title" role="alert"></p>
          </div>

          <div class="prks-field">
              <label for="work-folder-search">Folder <span class="prks-field__required" aria-hidden="true">Required</span></label>
              <div class="prks-combobox-with-action">
                  <div class="tag-add-shell combobox-container tag-add-shell--flush prks-inline-combobox-shell">
                      <div class="tag-add-shell__field">
                          <span class="tag-add-shell__icon"><i data-lucide="search" class="prks-icon" aria-hidden="true"></i></span>
                          <input type="text" id="work-folder-search" class="tag-add-shell__input" placeholder="Uncategorized" autocomplete="off" aria-required="true" aria-describedby="work-folder-error">
                      </div>
                      <input type="hidden" id="work-folder-id">
                      <div id="folder-results" class="combobox-results combobox-results--tag-panel hidden"></div>
                  </div>
                  <button type="button" onclick="quickCreateFolder()" class="prks-btn prks-btn--secondary prks-btn--sm" title="Create New Folder" aria-label="Create New Folder"><span class="ribbon-btn__icon"><i data-lucide="plus" class="prks-icon prks-icon--sm" aria-hidden="true"></i></span></button>
              </div>
              <p id="work-folder-error" class="folder-field-error hidden" data-prks-field-error="work-folder" role="alert"></p>
          </div>

          <div class="form-grid-2 prks-create-grid">
              <div class="prks-field">
                  <label for="work-doc-type-trigger">Document type</label>
                  <div class="prks-doc-type-menu combobox-container">
                      <input type="hidden" id="work-doc-type" value="article">
                      <button type="button" class="prks-doc-type-menu__trigger" id="work-doc-type-trigger" aria-haspopup="listbox" aria-expanded="false" aria-controls="work-doc-type-listbox" aria-label="Document type">
                          <span class="prks-doc-type-menu__label">Article</span>
                          <span class="prks-doc-type-menu__caret"><i data-lucide="chevron-down" class="prks-icon" aria-hidden="true"></i></span>
                      </button>
                      <div id="work-doc-type-listbox" class="prks-doc-type-menu__panel hidden" role="listbox"></div>
                  </div>
              </div>
              <div class="prks-field">
                  <label for="work-year">Year</label>
                  <input type="text" id="work-year" placeholder="1950" autocomplete="off">
              </div>
          </div>

          <div class="prks-field prks-upload-people" id="upload-people-field">
              <label for="upload-person-search">People</label>
              <div class="tag-add-shell combobox-container tag-add-shell--flush prks-inline-combobox-shell">
                  <div class="tag-add-shell__field">
                      <span class="tag-add-shell__icon"><i data-lucide="user-plus" class="prks-icon" aria-hidden="true"></i></span>
                      <input type="text" id="upload-person-search" class="tag-add-shell__input" placeholder="Add a person…" autocomplete="off" aria-describedby="upload-people-help upload-people-error">
                  </div>
                  <input type="hidden" id="upload-person-id">
                  <div id="person-results" class="combobox-results combobox-results--tag-panel hidden"></div>
              </div>
              <p id="upload-people-error" class="folder-field-error hidden" data-prks-field-error="upload-people" role="alert"></p>
              <ul id="upload-roles-list" class="prks-upload-people-list" aria-label="People on this file" hidden></ul>
              <p id="upload-people-help" class="meta-row meta-row--hint meta-row--compact">Choosing a person adds them as Author. Change the role on their row.</p>
              <div id="upload-role-credit-parking" hidden>
                  <div class="prks-upload-credit-panel" id="upload-role-credit-panel">
                      <div class="prks-role-credit-picker" id="upload-role-credit-wrap" hidden>
                          <label class="prks-role-credit-picker__heading" for="upload-role-credit-input">Name on this file</label>
                          <p id="upload-role-credit-profile" class="prks-role-credit-picker__profile meta-row meta-row--hint"></p>
                          <div id="upload-role-credit-shell" class="tag-add-shell tag-add-shell--flush prks-role-credit-picker__shell prks-role-credit-picker__shell--off">
                              <div class="tag-add-shell__field prks-role-credit-picker__field">
                                  <label class="prks-role-credit-picker__check" for="upload-role-credit-enable" title="Use a different name on this file">
                                      <input type="checkbox" id="upload-role-credit-enable" class="prks-role-credit-picker__enable-input" aria-label="Use a different name on this file">
                                  </label>
                                  <input type="text" id="upload-role-credit-input" class="tag-add-shell__input prks-role-credit-picker__input" disabled placeholder="Uses profile name" autocomplete="off" aria-label="Name shown on this file">
                              </div>
                          </div>
                          <div id="upload-role-credit-aliases" class="prks-role-credit-picker__aliases" hidden role="group" aria-label="Known aliases"></div>
                          <p class="meta-row meta-row--hint meta-row--compact">Check the box to override; pick an alias or type a name.</p>
                      </div>
                      <div class="prks-upload-credit-panel__actions">
                          <button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" id="upload-role-credit-done">Done</button>
                      </div>
                  </div>
              </div>
          </div>
      </section>

      <details id="work-upload-biblio-details" class="work-upload-meta__details">
          <summary class="work-upload-meta__summary">
              <span class="work-upload-meta__summary-chevron" aria-hidden="true"></span>
              <span class="work-upload-meta__summary-text">
                  <span class="work-upload-meta__summary-title-row">
                      <span class="work-upload-meta__summary-title">Publication details</span>
                      <span class="work-upload-meta__summary-optional">Optional</span>
                      <span class="work-upload-meta__summary-count" data-prks-count-for="work-upload-biblio-details" hidden></span>
                      <button type="button" class="prks-hint-btn work-upload-meta__hint-btn" data-prks-hint-type="upload-biblio-details" aria-label="What is included in publication details" aria-expanded="false" aria-controls="prks-hint-popover">?</button>
                  </span>
              </span>
          </summary>
          <div class="work-upload-meta__details-body form-pane form-pane--tight" inert>
              <div id="work-upload-pdf-only-meta" class="prks-create-details-grid">
                  <div class="form-grid-2 prks-create-grid">
                      <div class="prks-field" id="work-upload-published-date-col">
                          <label for="work-date">Published date</label>
                          <input type="text" id="work-date" placeholder="dd/mm/yyyy" inputmode="numeric" autocomplete="off" data-prks-count aria-describedby="work-date-error">
                          <p id="work-date-error" class="folder-field-error hidden" data-prks-field-error="work-date" role="alert"></p>
                      </div>
                      <div class="prks-field">
                          <label for="work-edition">Edition</label>
                          <input type="text" id="work-edition" placeholder="e.g. 2 or revised" autocomplete="off" data-prks-count>
                      </div>
                  </div>
                  <div class="prks-field" id="work-pdf-source-url-row">
                      <label for="work-pdf-source-url">Original URL</label>
                      <input type="url" id="work-pdf-source-url" placeholder="https://… (article or source page)" autocomplete="off" data-prks-count aria-describedby="work-pdf-source-url-hint">
                      <p id="work-pdf-source-url-hint" class="meta-row meta-row--hint meta-row--compact">If this PDF was saved from the web, keep the link for reference and BibTeX.</p>
                  </div>
                  <div class="form-grid-2 prks-create-grid">
                      <div class="prks-field">
                          <label for="work-publisher">Publisher</label>
                          <input type="text" id="work-publisher" autocomplete="off" data-prks-count>
                      </div>
                      <div class="prks-field">
                          <label for="work-location">Location (place of publication)</label>
                          <input type="text" id="work-location" autocomplete="off" placeholder="e.g. Cambridge, UK or Paris; Berlin" data-prks-count>
                      </div>
                  </div>
                  <div class="prks-field">
                      <label for="work-journal">Journal</label>
                      <input type="text" id="work-journal" autocomplete="off" data-prks-count>
                  </div>
                  <div class="form-grid-2 prks-create-grid">
                      <div class="prks-field"><label for="work-volume">Volume</label><input type="text" id="work-volume" autocomplete="off" data-prks-count></div>
                      <div class="prks-field"><label for="work-issue">Issue</label><input type="text" id="work-issue" autocomplete="off" data-prks-count></div>
                  </div>
                  <div class="form-grid-2 prks-create-grid">
                      <div class="prks-field"><label for="work-pages">Pages</label><input type="text" id="work-pages" autocomplete="off" data-prks-count></div>
                      <div class="prks-field"><label for="work-isbn">ISBN</label><input type="text" id="work-isbn" autocomplete="off" data-prks-count></div>
                  </div>
                  <div class="form-grid-2 prks-create-grid">
                      <div class="prks-field"><label for="work-doi">DOI</label><input type="text" id="work-doi" autocomplete="off" data-prks-count></div>
                      <div class="prks-field"><label for="work-thumb-page">Thumbnail page (PDF)</label><input type="number" id="work-thumb-page" min="1" step="1" inputmode="numeric" placeholder="1" autocomplete="off" data-prks-count></div>
                  </div>
              </div>
              <div id="work-upload-video-only-meta" class="prks-create-details-grid hidden">
                  <div class="form-grid-2 prks-create-grid">
                      <div class="prks-field">
                          <label for="work-video-channel">Channel / author</label>
                          <input type="text" id="work-video-channel" placeholder="e.g. YaleCourses" autocomplete="off" data-prks-count>
                      </div>
                      <div class="prks-field">
                          <label for="work-video-published-date">Date published</label>
                          <input type="text" id="work-video-published-date" placeholder="dd/mm/yyyy" inputmode="numeric" autocomplete="off" data-prks-count aria-describedby="work-video-published-date-error">
                          <p id="work-video-published-date-error" class="folder-field-error hidden" data-prks-field-error="work-video-published-date" role="alert"></p>
                      </div>
                  </div>
                  <div class="prks-field">
                      <label for="work-video-urldate">Accessed date (urldate)</label>
                      <input type="text" id="work-video-urldate" placeholder="Auto (last edit)" disabled aria-describedby="work-video-urldate-hint">
                      <p id="work-video-urldate-hint" class="meta-row meta-row--hint meta-row--compact">BibLaTeX <code>@online</code> expects <code>url</code> + <code>urldate</code>.</p>
                  </div>
              </div>
          </div>
      </details>

      <details id="work-upload-more-details" class="work-upload-meta__details">
          <summary class="work-upload-meta__summary">
              <span class="work-upload-meta__summary-chevron" aria-hidden="true"></span>
              <span class="work-upload-meta__summary-text">
                  <span class="work-upload-meta__summary-title-row">
                      <span class="work-upload-meta__summary-title" id="work-upload-more-title">Tags, status &amp; notes</span>
                      <span class="work-upload-meta__summary-optional">Optional</span>
                      <span class="work-upload-meta__summary-count" data-prks-count-for="work-upload-more-details" hidden></span>
                  </span>
              </span>
          </summary>
          <div class="work-upload-meta__details-body form-pane form-pane--tight" inert>
              <div id="work-video-playlist-row" class="prks-field hidden">
                  <label for="work-video-playlist-search">Playlist</label>
                  <div class="tag-add-shell combobox-container tag-add-shell--flush">
                      <div class="tag-add-shell__field">
                          <span class="tag-add-shell__icon"><i data-lucide="plus" class="prks-icon" aria-hidden="true"></i></span>
                          <input type="text" id="work-video-playlist-search" class="tag-add-shell__input" placeholder="Search playlists…" maxlength="300" autocomplete="off" aria-describedby="work-video-playlist-hint">
                          <input type="hidden" id="work-video-playlist-id" value="" data-prks-count>
                      </div>
                      <div id="work-video-playlist-results" class="combobox-results combobox-results--tag-panel hidden"></div>
                  </div>
                  <p id="work-video-playlist-hint" class="meta-row meta-row--hint meta-row--compact">The video is added to this playlist when the file is created.</p>
              </div>

              <div id="work-upload-tags-block" class="prks-field">
                  <p class="tag-add-field__caption">Tags</p>
                  <div class="tag-add-shell combobox-container tag-add-shell--flush">
                      <div class="tag-add-shell__field">
                          <span class="tag-add-shell__icon"><i data-lucide="plus" class="prks-icon" aria-hidden="true"></i></span>
                          <input type="text" id="upload-tag-search" class="tag-add-shell__input" placeholder="Search or create tag…" maxlength="300" autocomplete="off" aria-label="Add tag for new file">
                      </div>
                      <div id="upload-tag-results" class="combobox-results combobox-results--tag-panel hidden"></div>
                  </div>
                  <div id="upload-tags-list" class="tag-cloud work-tags-list"></div>
              </div>

              <div class="prks-work-upload-status-field">
                  <label for="work-status">Status</label>
                  <div class="prks-segmented-wrap prks-segmented-wrap--status-row">
                      <input type="hidden" id="work-status" value="Not Started" data-prks-count data-prks-count-default="Not Started">
                      <div class="prks-segmented prks-segmented--status prks-segmented--single-row" role="radiogroup" aria-label="File status">
                          <button type="button" class="prks-segmented__btn prks-segmented__btn--active" data-value="Not Started" aria-checked="true" role="radio"><span class="prks-segmented__btn-icon"><i data-lucide="circle" class="prks-icon prks-icon--sm" aria-hidden="true"></i></span><span class="prks-segmented__btn-label">Not Started</span></button>
                          <button type="button" class="prks-segmented__btn" data-value="Planned" aria-checked="false" role="radio"><span class="prks-segmented__btn-icon"><i data-lucide="clipboard-list" class="prks-icon prks-icon--sm" aria-hidden="true"></i></span><span class="prks-segmented__btn-label">Planned</span></button>
                          <button type="button" class="prks-segmented__btn" data-value="In Progress" aria-checked="false" role="radio"><span class="prks-segmented__btn-icon"><i data-lucide="play" class="prks-icon prks-icon--sm" aria-hidden="true"></i></span><span class="prks-segmented__btn-label">In Progress</span></button>
                          <button type="button" class="prks-segmented__btn" data-value="Completed" aria-checked="false" role="radio"><span class="prks-segmented__btn-icon"><i data-lucide="check" class="prks-icon prks-icon--sm" aria-hidden="true"></i></span><span class="prks-segmented__btn-label">Completed</span></button>
                          <button type="button" class="prks-segmented__btn" data-value="Paused" aria-checked="false" role="radio"><span class="prks-segmented__btn-icon"><i data-lucide="pause" class="prks-icon prks-icon--sm" aria-hidden="true"></i></span><span class="prks-segmented__btn-label">Paused</span></button>
                      </div>
                  </div>
              </div>

              <div class="prks-field">
                  <label for="work-private-notes">Private notes</label>
                  <textarea id="work-private-notes" class="textarea-sm" placeholder="Reminders for yourself; not shown in search index." data-prks-count></textarea>
              </div>

              <div class="prks-field">
                  <label for="work-abstract">Abstract / Preface</label>
                  <textarea id="work-abstract" class="textarea-lg textarea-flex" placeholder="Briefly describe why this document is relevant, or use paper abstract..." data-prks-count></textarea>
              </div>
          </div>
      </details>
  </div>
</template>
