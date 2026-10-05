import type { Meta, StoryObj } from '@storybook/vue3-vite'
import PrksButton from './PrksButton.vue'

const meta = {
  title: 'PRKS/PrksButton',
  component: PrksButton,
} satisfies Meta<typeof PrksButton>

export default meta
type Story = StoryObj<typeof meta>

export const Primary: Story = {
  render: () => ({
    components: { PrksButton },
    template: '<PrksButton variant="primary">New Person</PrksButton>',
  }),
}

export const Refresh: Story = {
  render: () => ({
    components: { PrksButton },
    template: '<PrksButton>Refresh</PrksButton>',
  }),
}

export const Danger: Story = {
  render: () => ({
    components: { PrksButton },
    template: '<PrksButton variant="danger">Delete annotation</PrksButton>',
  }),
}

export const Ghost: Story = {
  render: () => ({
    components: { PrksButton },
    template: '<PrksButton variant="ghost">Clear search</PrksButton>',
  }),
}

export const QuietDanger: Story = {
  render: () => ({
    components: { PrksButton },
    template: '<PrksButton variant="quiet-danger">Delete</PrksButton>',
  }),
}

export const Small: Story = {
  render: () => ({
    components: { PrksButton },
    template: '<PrksButton size="sm">Aliases</PrksButton>',
  }),
}

export const Disabled: Story = {
  render: () => ({
    components: { PrksButton },
    template: '<PrksButton disabled>Restore this backup</PrksButton>',
  }),
}

export const Refreshing: Story = {
  render: () => ({
    components: { PrksButton },
    template: '<PrksButton busy busy-label="Refreshing…">Refresh</PrksButton>',
  }),
}

export const RefreshWhileResetting: Story = {
  render: () => ({
    components: { PrksButton },
    template: '<PrksButton busy>Refresh</PrksButton>',
  }),
}

export const DiagnosticsActions: Story = {
  render: () => ({
    components: { PrksButton },
    template: `
      <div class="prks-backup-restore-row">
        <PrksButton>Refresh</PrksButton>
        <PrksButton busy busy-label="Resetting…">Reset</PrksButton>
        <PrksButton>Copy report</PrksButton>
      </div>
    `,
  }),
}

export const LongLabelCompact: Story = {
  render: () => ({
    components: { PrksButton },
    template: `
      <div style="width: 220px">
        <PrksButton>Linearize existing PDFs</PrksButton>
      </div>
    `,
  }),
}
