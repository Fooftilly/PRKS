import { createVNode } from 'vue'
import {
  presentRouteSurface,
  registerEarlyRoutePresenter,
  registerRouteWindowBridge,
  resetRouteSurfaceForTests,
  type RouteSurfaceOwner,
} from '../../route-surface/lifecycle'
import { browserProcessingIntents, type ProcessingIntentOwner } from './intents'
import { buildProcessingProjection, type ProcessingProjection, type ProcessingResume } from './projection'
import type { ProcessingRouteInstance } from './route'
import ProcessingFilesRoute from './ProcessingFilesRoute.vue'
import { processingRecords } from './records'

export interface ProcessingPresentInput {
  owner: RouteSurfaceOwner & ProcessingIntentOwner
  host: HTMLElement
  files: unknown
  people: unknown
  folders: unknown
  roleTypes: unknown
  domPrefix: string
  generation?: number
  /**
   * True when this owner is the Main shell. Recorded on the route instance.
   * Vue does not publish the processing sidebar.
   */
  shell?: boolean
  /** Keep the inbox window across a reload of this owner. */
  resume?: ProcessingResume | null
}

const PROCESSING_FEATURE = 'processing-files'

function isProcessingEarlyRequest(
  value: unknown,
): value is Omit<ProcessingPresentInput, 'host'> & { feature: typeof PROCESSING_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<ProcessingPresentInput> & { feature?: unknown }
  return record.feature === PROCESSING_FEATURE && !!record.owner && 'files' in record
}

/**
 * Paint one owner's Processing inbox. The coordinator has already loaded the
 * rescan (through `prksProcessingRecords`), people, and folders. The page
 * does not fetch them. The preview iframe
 * is attached by the page and released with the owner.
 */
export function presentProcessing(input: ProcessingPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const owner = input.owner
  const files = input.files
  const people = input.people
  const folders = input.folders
  const roleTypes = input.roleTypes
  const domPrefix = input.domPrefix
  const resume = input.resume
  const route: Omit<ProcessingRouteInstance, 'generation'> & { generation?: number } = {
    name: 'processing-files',
    canonicalHash: '#/processing-files',
    params: {},
    ownsMainShell: input.shell !== false,
    generation: input.generation,
  }
  presentRouteSurface({
    owner,
    host: input.host,
    route,
    render: (generation) => {
      const projection: ProcessingProjection = buildProcessingProjection({
        files,
        people,
        folders,
        roleTypes,
        domPrefix,
        generation,
        resume,
      })
      return createVNode(ProcessingFilesRoute, {
        projection,
        intents: browserProcessingIntents(owner, generation),
      })
    },
  })
}

export function resetProcessingSessionForTests(): void {
  resetRouteSurfaceForTests()
}

export function registerProcessingBridge(target: Window = window): void {
  registerRouteWindowBridge(target)
  target.prksProcessingRecords = processingRecords()
  registerEarlyRoutePresenter(
    PROCESSING_FEATURE,
    (request, host) => {
      if (!isProcessingEarlyRequest(request)) return false
      const { owner, files, people, folders, roleTypes, domPrefix, generation, shell, resume } = request
      presentProcessing({
        owner,
        host,
        files,
        people,
        folders,
        roleTypes,
        domPrefix: typeof domPrefix === 'string' ? domPrefix : '',
        generation,
        shell,
        resume,
      })
      return true
    },
    target,
  )
}
