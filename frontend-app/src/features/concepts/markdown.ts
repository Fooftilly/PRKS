import { researchMarkdownHtml as renderResearchMarkdown } from '../../research-index/markdown'

const EMPTY_DEFINITION = '<p class="meta-row">No definition yet.</p>'

/** Concept detail empty sentence over the shared markdown boundary. */
export function researchMarkdownHtml(text: string | null | undefined): string {
  return renderResearchMarkdown(text, EMPTY_DEFINITION)
}
