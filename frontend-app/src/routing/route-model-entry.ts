/**
 * Classic-script entry. The maintainer build emits `frontend/js/route-model.js`
 * as the global `prksRouteModel`; `navigation.js` publishes its names on
 * `window`. The Vue application imports `./route-model` and the `src/domain/`
 * modules and does not import this entry.
 *
 * `WORK_STATUSES` and `PEOPLE_ROLES` are owned by `src/domain/`. They ride in
 * this script only because the parser already bundles them; routing does not
 * own them.
 */
export { PEOPLE_ROLES } from '../domain/people-roles'
export { WORK_STATUSES } from '../domain/work-status'
export {
  HOME_HASH,
  ROUTE_META,
  ROUTE_NAMES,
  graphFocusHash,
  isRecognizedRoute,
  normalizeHashInput,
  parseGraphFocus,
  parseRoute,
  progressCanonicalHash,
} from './route-model'
