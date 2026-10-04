import type { Meta, StoryObj } from '@storybook/vue3-vite'
import PrksScopeLine from './PrksScopeLine.vue'

const meta = {
  title: 'PRKS/PrksScopeLine',
  component: PrksScopeLine,
} satisfies Meta<typeof PrksScopeLine>

export default meta
type Story = StoryObj<typeof meta>

export const Total: Story = {
  render: () => ({
    components: { PrksScopeLine },
    template: '<PrksScopeLine :total="8" label="Concepts" />',
  }),
}

export const Matching: Story = {
  render: () => ({
    components: { PrksScopeLine },
    template: '<PrksScopeLine :shown="2" :total="8" filter="ag" label="Concepts" />',
  }),
}
