import type { Meta, StoryObj } from '@storybook/vue3-vite'
import PrksDisclosureButton from './PrksDisclosureButton.vue'

const meta = {
  title: 'PRKS/PrksDisclosureButton',
  component: PrksDisclosureButton,
} satisfies Meta<typeof PrksDisclosureButton>

export default meta
type Story = StoryObj<typeof meta>

export const Closed: Story = {
  args: { expanded: false, controls: 'story-filters' },
  render: () => ({
    components: { PrksDisclosureButton },
    template: '<PrksDisclosureButton :expanded="false" controls="story-filters">Filters</PrksDisclosureButton>',
  }),
}

export const Open: Story = {
  args: { expanded: true, controls: 'story-legend' },
  render: () => ({
    components: { PrksDisclosureButton },
    template: '<PrksDisclosureButton :expanded="true" controls="story-legend">Legend</PrksDisclosureButton>',
  }),
}
