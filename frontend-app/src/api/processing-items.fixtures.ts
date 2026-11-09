import type { ProcessingItem } from './processing-items'

/** A `ready` queue item as the server answers it. */
export function processingItemRow(overrides: Partial<ProcessingItem> = {}): ProcessingItem {
  return {
    id: 'item-1',
    original_filename: 'paper.pdf',
    sha256: 'a'.repeat(64),
    byte_size: 2048,
    status: 'ready',
    revision: 1,
    created_at: '2026-11-06T10:00:00+00:00',
    updated_at: '2026-11-06T10:00:00+00:00',
    imported_at: null,
    imported_work_id: null,
    deleted_at: null,
    draft: {
      title: 'paper', author_text: '', year: '', doc_type: '', target_folder_id: '', abstract: '',
      doi: '', edition: '', isbn: '', issue: '', journal: '', location: '', pages: '', private_notes: '',
      published_date: '', publisher: '', source_url: '', status_draft: 'Not Started', volume: '',
      thumb_page: null, roles: [], tags: [],
    },
    ...overrides,
  }
}
