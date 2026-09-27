import type { Meta, StoryObj } from '@storybook/vue3-vite'
import { ref } from 'vue'
import WorkspacePaneHeader from './WorkspacePaneHeader.vue'
import { workspaceIntentsKey } from './intents'
import { workspaceProjectionKey } from './projection'
import type { WorkspaceProjection } from './types'

const projection = ref<WorkspaceProjection | null>({
  visualTiled: true,
  narrowFallback: false,
  tabStatus: {},
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
    ],
  },
})

const meta = {
  title: 'PRKS/Workspace/Pane header',
  component: WorkspacePaneHeader,
} satisfies Meta<typeof WorkspacePaneHeader>

export default meta
type Story = StoryObj<typeof meta>

function render(tabId: string) {
  return {
    components: { WorkspacePaneHeader },
    setup() {
      return { tabId }
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
    template: '<WorkspacePaneHeader :tab-id="tabId" />',
  }
}

export const Main: Story = {
  args: { tabId: 'tab-1' },
  render: () => render('tab-1'),
}

export const Secondary: Story = {
  args: { tabId: 'tab-2' },
  render: () => render('tab-2'),
}
