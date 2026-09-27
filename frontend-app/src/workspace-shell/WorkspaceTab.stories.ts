import type { Meta, StoryObj } from '@storybook/vue3-vite'
import { ref } from 'vue'
import WorkspaceTab from './WorkspaceTab.vue'
import { workspaceIntentsKey } from './intents'
import { workspaceProjectionKey } from './projection'
import type { WorkspaceProjection } from './types'

const projection = ref<WorkspaceProjection | null>({
  visualTiled: true,
  narrowFallback: false,
  tabStatus: { 'tab-2': 'drafting' },
  state: {
    version: 1,
    mode: 'tiled',
    mainTabId: 'tab-1',
    focusedTabId: 'tab-2',
    secondaryTree: { type: 'leaf', tabId: 'tab-2' },
    mainSplitRatio: 0.58,
    tabs: [
      { id: 'tab-1', route: '#/folders', title: 'Folders', icon: 'folder' },
      { id: 'tab-2', route: '#/people', title: 'People', icon: 'users' },
      { id: 'tab-3', route: '#/works/1', title: 'Parked work', icon: 'file-text' },
    ],
  },
})

const meta = {
  title: 'PRKS/Workspace/Tab',
  component: WorkspaceTab,
} satisfies Meta<typeof WorkspaceTab>

export default meta
type Story = StoryObj<typeof meta>

function render(id: string) {
  const tab = projection.value?.state.tabs.find((item) => item.id === id)
  return {
    components: { WorkspaceTab },
    setup() {
      return { tab }
    },
    provide: {
      [workspaceProjectionKey as symbol]: projection,
      [workspaceIntentsKey as symbol]: {
        activate() {},
        close() {},
        focus() {},
        tile() {},
        openTabMenu() {},
        setMainRatio() {},
        setNestedRatio() {},
      },
    },
    template: '<div class="prks-workspace-tabs"><WorkspaceTab v-if="tab" :tab="tab" /></div>',
  }
}

export const Main: Story = {
  args: { tab: { id: 'tab-1', route: '#/folders', title: 'Folders', icon: 'folder' } },
  render: () => render('tab-1'),
}

export const FocusedSecondary: Story = {
  args: { tab: { id: 'tab-2', route: '#/people', title: 'People', icon: 'users' } },
  render: () => render('tab-2'),
}

export const Parked: Story = {
  args: { tab: { id: 'tab-3', route: '#/works/1', title: 'Parked work', icon: 'file-text' } },
  render: () => render('tab-3'),
}
