import type { Meta, StoryObj } from '@storybook/vue3-vite'
import PrksStatusText from './PrksStatusText.vue'

const meta = {
  title: 'PRKS/PrksStatusText',
  component: PrksStatusText,
} satisfies Meta<typeof PrksStatusText>

export default meta
type Story = StoryObj<typeof meta>

export const Reset: Story = {
  render: () => ({
    components: { PrksStatusText },
    template: '<PrksStatusText>Measurements reset.</PrksStatusText>',
  }),
}

export const CopyFailed: Story = {
  render: () => ({
    components: { PrksStatusText },
    template: '<PrksStatusText>Could not copy report.</PrksStatusText>',
  }),
}

export const ResetFailed: Story = {
  render: () => ({
    components: { PrksStatusText },
    template: '<PrksStatusText>Could not reset measurements.</PrksStatusText>',
  }),
}

export const LongHintCompact: Story = {
  render: () => ({
    components: { PrksStatusText },
    template: `
      <div style="width: 280px">
        <PrksStatusText>
          Runtime-only measurements from this PRKS process. No research content, search terms or file names are recorded. Measurements reset when PRKS restarts.
        </PrksStatusText>
      </div>
    `,
  }),
}
