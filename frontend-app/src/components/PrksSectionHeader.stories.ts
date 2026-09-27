import type { Meta, StoryObj } from '@storybook/vue3-vite'
import PrksSectionHeader from './PrksSectionHeader.vue'

const meta = {
  title: 'PRKS/PrksSectionHeader',
  component: PrksSectionHeader,
} satisfies Meta<typeof PrksSectionHeader>

export default meta
type Story = StoryObj<typeof meta>

export const ClientCoordinator: Story = {
  render: () => ({
    components: { PrksSectionHeader },
    template: '<PrksSectionHeader>Client request coordinator</PrksSectionHeader>',
  }),
}

export const PerformanceDiagnostics: Story = {
  render: () => ({
    components: { PrksSectionHeader },
    template: '<PrksSectionHeader>Performance diagnostics</PrksSectionHeader>',
  }),
}

export const LongTitleCompact: Story = {
  render: () => ({
    components: { PrksSectionHeader },
    template: `
      <div style="width: 220px">
        <PrksSectionHeader>
          BibTeX / BibLaTeX export
          <span class="prks-settings-scope">Library-wide</span>
        </PrksSectionHeader>
      </div>
    `,
  }),
}
