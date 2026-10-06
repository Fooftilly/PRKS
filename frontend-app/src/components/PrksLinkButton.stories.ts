import type { Meta, StoryObj } from '@storybook/vue3-vite'
import PrksLinkButton from './PrksLinkButton.vue'

const meta = {
  title: 'PRKS/PrksLinkButton',
  component: PrksLinkButton,
} satisfies Meta<typeof PrksLinkButton>

export default meta
type Story = StoryObj<typeof meta>

export const Back: Story = {
  args: { href: '#/concepts' },
  render: () => ({
    components: { PrksLinkButton },
    template: '<PrksLinkButton href="#/concepts">Back to Concepts</PrksLinkButton>',
  }),
}

export const OpenSmall: Story = {
  args: { href: '#/views/v1', size: 'sm' },
  render: () => ({
    components: { PrksLinkButton },
    template: '<PrksLinkButton href="#/views/v1" size="sm">Open</PrksLinkButton>',
  }),
}

export const OpenAsSearch: Story = {
  args: { href: '#/search' },
  render: () => ({
    components: { PrksLinkButton },
    template: '<PrksLinkButton href="#/search">Open as Search</PrksLinkButton>',
  }),
}
