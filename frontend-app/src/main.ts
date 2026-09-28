import { mountPrksVue, PRKS_VUE_ROOT_ID } from './mount'
import { registerConceptsBridge } from './features/concepts/session'
import { registerFolderLibraryBridge } from './features/folder-library/session'
import { registerArgumentsBridge } from './features/arguments/session'
import { registerPeopleBridge } from './features/people/session'
import { registerPlaylistsBridge } from './features/playlists/session'
import { registerPositionsBridge } from './features/positions/session'
import { registerPerformanceDiagnosticsBridge } from './features/performance-diagnostics/activation'
import { registerProgressBridge } from './features/progress/session'

const target = document.getElementById(PRKS_VUE_ROOT_ID)
if (target) {
  mountPrksVue(target)
}

registerPerformanceDiagnosticsBridge(window)
registerProgressBridge(window)
registerConceptsBridge(window)
registerFolderLibraryBridge(window)
registerPositionsBridge(window)
registerArgumentsBridge(window)
registerPlaylistsBridge(window)
registerPeopleBridge(window)
