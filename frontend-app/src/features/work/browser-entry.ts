/**
 * Classic-script entry. The maintainer build emits frontend/js/work-route-projection.js.
 * The Vue application does not import this module. No Vue mount, Pinia, or Vue Router.
 */
import {
  projectWorkRoute,
  publishWorkRouteProjection,
  replaceWorkRoutePlacement,
  workOpenShouldRecord,
} from './projection'

type WorkRouteGlobal = typeof globalThis & {
  prksProjectWorkRoute: typeof projectWorkRoute
  prksPublishWorkRouteProjection: typeof publishWorkRouteProjection
  prksReplaceWorkRoutePlacement: typeof replaceWorkRoutePlacement
  prksWorkOpenShouldRecord: typeof workOpenShouldRecord
}

const root = globalThis as WorkRouteGlobal
root.prksProjectWorkRoute = projectWorkRoute
root.prksPublishWorkRouteProjection = publishWorkRouteProjection
root.prksReplaceWorkRoutePlacement = replaceWorkRoutePlacement
root.prksWorkOpenShouldRecord = workOpenShouldRecord

export {
  projectWorkRoute,
  publishWorkRouteProjection,
  replaceWorkRoutePlacement,
  workOpenShouldRecord,
}
