/**
 * Classic-script entry. The maintainer build emits `frontend/js/search-query-codec.js`
 * as the global `prksSearchQueryCodec`. That object is the one legacy bridge.
 *
 * The Vue application imports `./codec` and does not import this module.
 * No Vue mount, Pinia, or Vue Router.
 */
export { definitionFromRoute, hashFromDefinition, optionsFromDefinition, summaryText } from './codec'
