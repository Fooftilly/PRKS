import { researchMarkdownHtml as renderResearchMarkdown } from '../../research-index/markdown'

export function researchMarkdownHtml(text: string | null | undefined): string {
  return renderResearchMarkdown(text, '<p class="meta-row">No description yet.</p>')
}
