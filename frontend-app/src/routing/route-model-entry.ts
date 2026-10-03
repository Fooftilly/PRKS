/**
 * Classic-script entry. The maintainer build emits `frontend/js/route-model.js`
 * as the global `prksRouteModel`; `navigation.js` publishes its names on
 * `window`. The Vue application imports `./route-model` and does not import
 * this module.
 */
export {
  HOME_HASH,
  PEOPLE_ROLES,
  PROGRESS_STATUS_VALUES,
  ROUTE_META,
  ROUTE_NAMES,
  graphFocusHash,
  isProgressStatus,
  isRecognizedRoute,
  normalizeHashInput,
  parseGraphFocus,
  parseRoute,
  progressCanonicalHash,
} from './route-model'
