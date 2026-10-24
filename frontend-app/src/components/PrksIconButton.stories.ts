import type { Meta, StoryObj } from '@storybook/vue3-vite'
import PrksIconButton from './PrksIconButton.vue'

const meta = {
  title: 'PRKS/PrksIconButton',
  component: PrksIconButton,
} satisfies Meta<typeof PrksIconButton>

export default meta
type Story = StoryObj<typeof meta>

export const TagAlias: Story = {
  args: { label: 'Edit aliases for History', title: 'Aliases', size: 'sm' },
  render: () => ({
    components: { PrksIconButton },
    template: `
      <PrksIconButton label="Edit aliases for History" title="Aliases" size="sm">
        <span aria-hidden="true">⋯</span>
      </PrksIconButton>
    `,
  }),
}

export const PaneActions: Story = {
  args: { label: 'Pane actions', title: 'Pane actions', variant: 'ghost' },
  render: () => ({
    components: { PrksIconButton },
    template: `
      <PrksIconButton
        label="Pane actions"
        title="Pane actions"
        variant="ghost"
        class="prks-tile-header__menu"
        aria-haspopup="menu"
        aria-expanded="false"
      >
        <span aria-hidden="true">…</span>
      </PrksIconButton>
    `,
  }),
}

export const Close: Story = {
  args: { label: 'Close' },
  render: () => ({
    components: { PrksIconButton },
    template: `
      <PrksIconButton label="Close" class="close-btn">
        <span aria-hidden="true">×</span>
      </PrksIconButton>
    `,
  }),
}

export const Disabled: Story = {
  args: { label: 'Close', disabled: true },
  render: () => ({
    components: { PrksIconButton },
    template: '<PrksIconButton label="Close" disabled><span aria-hidden="true">×</span></PrksIconButton>',
  }),
}

export const RemovingAlias: Story = {
  args: { label: 'Remove alias', variant: 'danger', size: 'sm', busy: true, busyLabel: 'Removing…' },
  render: () => ({
    components: { PrksIconButton },
    template: `
      <PrksIconButton
        label="Remove alias"
        variant="danger"
        size="sm"
        busy
        busy-label="Removing…"
      >
        <span aria-hidden="true">×</span>
      </PrksIconButton>
    `,
  }),
}
