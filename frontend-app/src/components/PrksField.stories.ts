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
      <PrksField v-slot="{ labelledBy, describedBy }" label="Title" for-id="story-title">
        <input id="story-title" type="text" value="Agency" :aria-labelledby="labelledBy" :aria-describedby="describedBy">
      </PrksField>
    `,
  }),
}

export const Invalid: Story = {
  args: { label: 'Title', forId: 'story-title-err', error: 'Title is required.' },
  render: () => ({
    components: { PrksField },
    template: `
      <PrksField v-slot="{ labelledBy, describedBy }" label="Title" for-id="story-title-err" error="Title is required.">
        <input id="story-title-err" type="text" aria-invalid="true" :aria-labelledby="labelledBy" :aria-describedby="describedBy">
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
      <PrksField v-slot="{ labelledBy, describedBy }" label="DOI" for-id="story-doi" help="Provenance only. This does not change the file kind.">
        <input id="story-doi" type="text" :aria-labelledby="labelledBy" :aria-describedby="describedBy">
      </PrksField>
    `,
  }),
}

export const HelpAndError: Story = {
  args: {
    label: 'YouTube URL',
    forId: 'story-video-url',
    help: 'Replaces which video this file is.',
    error: 'Enter a YouTube URL.',
  },
  render: () => ({
    components: { PrksField },
    template: `
      <PrksField
        v-slot="{ labelledBy, describedBy }"
        label="YouTube URL"
        for-id="story-video-url"
        help="Replaces which video this file is."
        error="Enter a YouTube URL."
      >
        <input id="story-video-url" type="url" aria-invalid="true" :aria-labelledby="labelledBy" :aria-describedby="describedBy">
      </PrksField>
    `,
  }),
}
