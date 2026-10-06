import type { Meta, StoryObj } from '@storybook/vue3-vite'
import PrksInlineMessage from './PrksInlineMessage.vue'

const meta = {
  title: 'PRKS/PrksInlineMessage',
  component: PrksInlineMessage,
} satisfies Meta<typeof PrksInlineMessage>

export default meta
type Story = StoryObj<typeof meta>

export const OfflineList: Story = {
  render: () => ({
    components: { PrksInlineMessage },
    template: `
      <PrksInlineMessage data-prks-role="offline-unavailable">
        This list has not been cached on this device.
      </PrksInlineMessage>
    `,
  }),
}

export const LoadError: Story = {
  render: () => ({
    components: { PrksInlineMessage },
    template: `
      <PrksInlineMessage tone="error" status>
        Could not refresh tags. The list below is unchanged.
      </PrksInlineMessage>
    `,
  }),
}

export const NotFound: Story = {
  render: () => ({
    components: { PrksInlineMessage },
    template: '<PrksInlineMessage tone="error">Folder not found.</PrksInlineMessage>',
  }),
}

export const LongRefreshFailure: Story = {
  render: () => ({
    components: { PrksInlineMessage },
    template: `
      <div style="width: 320px">
        <PrksInlineMessage tone="error" status>
          Could not refresh publishers. The names already on screen stay as they were. Try again when PRKS can reach the library.
        </PrksInlineMessage>
      </div>
    `,
  }),
}
