import { mountPrksVue, PRKS_VUE_ROOT_ID } from './mount'
import { registerConceptsBridge } from './features/concepts/session'
import { registerFolderDetailBridge } from './features/folder-detail/session'
import { registerFolderLibraryBridge } from './features/folder-library/session'
import { registerRecentBridge } from './features/recent/session'
import { registerTypesBridge } from './features/types/session'
import { registerTagsBridge } from './features/tags/session'
import { registerPublishersBridge } from './features/publishers/session'
import { registerProcessingBridge } from './features/processing/session'
import { registerResearchGraphBridge } from './features/research-graph/session'
import { registerSavedViewsBridge } from './features/saved-views/session'
import { registerSearchBridge } from './features/search/session'
import { registerArgumentsBridge } from './features/arguments/session'
import { registerPeopleBridge } from './features/people/session'
import { registerPersonGroupsBridge } from './features/person-groups/session'
import { registerPlaylistsBridge } from './features/playlists/session'
import { registerPositionsBridge } from './features/positions/session'
import { registerPerformanceDiagnosticsBridge } from './features/performance-diagnostics/activation'
import { registerProgressBridge } from './features/progress/session'
import { registerWorkPanelReadBridge } from './features/work/panel-session'
import { registerWorkMetadataEditorBridge } from './features/work/metadata-session'
import { registerWorkPrivateNotesBridge } from './features/work/private-note-session'
import { registerWorkResearchNotesBridge } from './features/work/research-note-session'
import { registerWorkDetailBridge } from './features/work/session'
import { registerWorkPdfAnnotationPopupBridge } from './features/work/pdf-annotation-popup'
import { registerWorkPdfAnnotationDrawerBridge } from './features/work/pdf-annotation-drawer'

const target = document.getElementById(PRKS_VUE_ROOT_ID)
if (target) {
  mountPrksVue(target)
}

registerPerformanceDiagnosticsBridge(window)
registerProgressBridge(window)
registerConceptsBridge(window)
registerFolderLibraryBridge(window)
registerFolderDetailBridge(window)
registerRecentBridge(window)
registerTypesBridge(window)
registerTagsBridge(window)
registerPublishersBridge(window)
registerProcessingBridge(window)
registerResearchGraphBridge(window)
registerSearchBridge(window)
registerSavedViewsBridge(window)
registerPositionsBridge(window)
registerArgumentsBridge(window)
registerPlaylistsBridge(window)
registerPeopleBridge(window)
registerPersonGroupsBridge(window)
registerWorkPanelReadBridge(window)
registerWorkMetadataEditorBridge(window)
registerWorkPrivateNotesBridge(window)
registerWorkResearchNotesBridge(window)
registerWorkDetailBridge(window)
registerWorkPdfAnnotationPopupBridge(window)
registerWorkPdfAnnotationDrawerBridge(window)
