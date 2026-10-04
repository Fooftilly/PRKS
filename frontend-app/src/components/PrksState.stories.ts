import type { Meta, StoryObj } from '@storybook/vue3-vite'
import PrksButton from './PrksButton.vue'
import PrksState from './PrksState.vue'

const meta = {
  title: 'PRKS/PrksState',
  component: PrksState,
} satisfies Meta<typeof PrksState>

export default meta
type Story = StoryObj<typeof meta>

export const LoadingPublishers: Story = {
  render: () => ({
    components: { PrksState },
    template: '<PrksState kind="loading" message="Loading publishers…" />',
  }),
}

export const SavedViewsLoadError: Story = {
  render: () => ({
    components: { PrksButton, PrksState },
    template: `
      <PrksState kind="error" message="Could not load Saved Views.">
        <PrksButton variant="secondary" size="sm">Try again</PrksButton>
      </PrksState>
    `,
  }),
}

export const EmptyProgress: Story = {
  render: () => ({
    components: { PrksState },
    template: '<PrksState kind="empty" heading="No files with this progress status yet." />',
  }),
}

export const LongLoadError: Story = {
  render: () => ({
    components: { PrksButton, PrksState },
    template: `
      <div style="width: 280px">
        <PrksState
          kind="error"
          message="Could not load publishers. The library did not answer, so there is no list to show."
        >
          <PrksButton variant="secondary" size="sm">Try again</PrksButton>
        </PrksState>
      </div>
    `,
  }),
}
