import type { Meta, StoryObj } from '@storybook/vue3-vite'
import PrksResearchRow from './PrksResearchRow.vue'

const meta = {
  title: 'PRKS/PrksResearchRow',
  component: PrksResearchRow,
} satisfies Meta<typeof PrksResearchRow>

export default meta
type Story = StoryObj<typeof meta>

export const Concept: Story = {
  args: { href: '#/concepts/C1', title: 'Agency' },
  render: () => ({
    components: { PrksResearchRow },
    template:
      '<PrksResearchRow href="#/concepts/C1" title="Agency" :meta="[\'Top-level concept\', \'2 subconcepts\']" />',
  }),
}

export const Stance: Story = {
  args: { href: '#/arguments/A1', title: 'Compatibilism', kind: 'Stance' },
  render: () => ({
    components: { PrksResearchRow },
    template:
      '<PrksResearchRow href="#/arguments/A1" title="Compatibilism" kind="Stance" :meta="[\'1 response\']" />',
  }),
}
