import type { Meta, StoryObj } from '@storybook/vue3-vite'
import PrksRelSummary from './PrksRelSummary.vue'

const meta = {
  title: 'PRKS/PrksRelSummary',
  component: PrksRelSummary,
} satisfies Meta<typeof PrksRelSummary>

export default meta
type Story = StoryObj<typeof meta>

export const ParentsAndMentions: Story = {
  args: { parts: ['1 parent', '2 note mentions'] },
  render: () => ({
    components: { PrksRelSummary },
    template: '<PrksRelSummary :parts="[\'1 parent\', \'2 note mentions\']" />',
  }),
}
