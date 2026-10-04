import type { Meta, StoryObj } from '@storybook/vue3-vite'
import PrksField from './PrksField.vue'

const meta = {
  title: 'PRKS/PrksField',
  component: PrksField,
} satisfies Meta<typeof PrksField>

export default meta
type Story = StoryObj<typeof meta>

export const Idle: Story = {
  args: { label: 'Title', forId: 'story-title' },
  render: () => ({
    components: { PrksField },
    template: `
      <PrksField label="Title" for-id="story-title">
        <input id="story-title" type="text" value="Agency">
      </PrksField>
    `,
  }),
}

export const Invalid: Story = {
  args: { label: 'Title', forId: 'story-title-err', error: 'Title is required.' },
  render: () => ({
    components: { PrksField },
    template: `
      <PrksField label="Title" for-id="story-title-err" error="Title is required.">
        <input id="story-title-err" type="text" aria-invalid="true" aria-describedby="story-title-err-error">
      </PrksField>
    `,
  }),
}

export const WithHelp: Story = {
  args: {
    label: 'DOI',
    forId: 'story-doi',
    help: 'Provenance only. This does not change the file kind.',
  },
  render: () => ({
    components: { PrksField },
    template: `
      <PrksField label="DOI" for-id="story-doi" help="Provenance only. This does not change the file kind.">
        <input id="story-doi" type="text">
      </PrksField>
    `,
  }),
}
