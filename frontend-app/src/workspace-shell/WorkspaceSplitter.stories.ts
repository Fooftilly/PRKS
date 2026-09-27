import type { Meta, StoryObj } from '@storybook/vue3-vite'
import WorkspaceSplitter from './WorkspaceSplitter.vue'

const meta = {
  title: 'PRKS/Workspace/Splitter',
  component: WorkspaceSplitter,
} satisfies Meta<typeof WorkspaceSplitter>

export default meta
type Story = StoryObj<typeof meta>

export const Vertical: Story = {
  args: { axis: 'left-right', root: true },
}

export const Horizontal: Story = {
  args: { axis: 'top-bottom', splitId: 'split-1' },
}
