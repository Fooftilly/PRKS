import type { Meta, StoryObj } from '@storybook/vue3-vite'
import PrksWorkCard from './PrksWorkCard.vue'

const pdfWork = {
  id: 'W-1',
  title: 'The Culture Industry',
  primary_author: 'Theodor W. Adorno',
  year: '1972',
  status: 'In Progress',
  doc_type: 'article',
  file_size_bytes: 1024 * 1024,
  file_path: '/api/pdfs/w1.pdf',
}

const meta = {
  title: 'PRKS/PrksWorkCard',
  component: PrksWorkCard,
  args: { work: pdfWork },
} satisfies Meta<typeof PrksWorkCard>

export default meta
type Story = StoryObj<typeof meta>

export const GridCard: Story = {
  args: { work: pdfWork, options: { subtitle: 'Added Sep 5, 2026' } },
  render: (args) => ({
    components: { PrksWorkCard },
    setup: () => ({ args }),
    template: `
      <div class="work-browse-collection work-browse-collection--cards card-grid" style="max-width: 22rem">
        <PrksWorkCard v-bind="args" />
      </div>
    `,
  }),
}

export const ListRow: Story = {
  args: { work: pdfWork, options: { subtitle: 'Last opened: Sep 5, 2026' } },
  render: (args) => ({
    components: { PrksWorkCard },
    setup: () => ({ args }),
    template: `
      <div class="work-browse-collection work-browse-collection--list" style="max-width: 40rem">
        <PrksWorkCard v-bind="args" />
      </div>
    `,
  }),
}

export const OfflineSuppressedThumb: Story = {
  args: {
    work: pdfWork,
    options: { subtitle: 'Last opened: Unknown', suppressThumbnail: true },
  },
  render: (args) => ({
    components: { PrksWorkCard },
    setup: () => ({ args }),
    template: `
      <div class="work-browse-collection work-browse-collection--cards card-grid" style="max-width: 22rem">
        <PrksWorkCard v-bind="args" />
      </div>
    `,
  }),
}

export const SearchExcerpt: Story = {
  args: {
    work: { ...pdfWork, status: 'Completed' },
    options: { subtitle: 'Culture industry as mass deception…' },
  },
  render: (args) => ({
    components: { PrksWorkCard },
    setup: () => ({ args }),
    template: `
      <div class="work-browse-collection work-browse-collection--cards card-grid" style="max-width: 22rem">
        <PrksWorkCard v-bind="args" />
      </div>
    `,
  }),
}

export const TypeDetailHidesBadge: Story = {
  args: { work: pdfWork, options: { hideDocTypeBadge: true } },
  render: (args) => ({
    components: { PrksWorkCard },
    setup: () => ({ args }),
    template: `
      <div class="work-browse-collection work-browse-collection--cards card-grid" style="max-width: 22rem">
        <PrksWorkCard v-bind="args" />
      </div>
    `,
  }),
}
