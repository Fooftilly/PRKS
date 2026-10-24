import type { Meta, StoryObj } from '@storybook/vue3-vite'
import PrksResearchSectionHead from './PrksResearchSectionHead.vue'

const meta = {
  title: 'PRKS/PrksResearchSectionHead',
  component: PrksResearchSectionHead,
} satisfies Meta<typeof PrksResearchSectionHead>

export default meta
type Story = StoryObj<typeof meta>

export const Plain: Story = {
  args: { title: 'Definition', headingId: 'story-def-h' },
  render: () => ({
    components: { PrksResearchSectionHead },
    template: '<PrksResearchSectionHead title="Definition" heading-id="story-def-h" />',
  }),
}

export const WithEdit: Story = {
  args: {
    title: 'Parent concepts',
    headingId: 'story-parents-h',
    count: 2,
    actionId: 'story-edit-parents',
    actionLabel: 'Edit',
    sub: '2 parents',
  },
  render: () => ({
    components: { PrksResearchSectionHead },
    template:
      '<PrksResearchSectionHead title="Parent concepts" heading-id="story-parents-h" :count="2" action-id="story-edit-parents" action-label="Edit" sub="2 parents" />',
  }),
}
