/**
 * Classic-script entry. The maintainer build emits `frontend/js/owner-resource.js`
 * as the global `prksOwnerResource`. TabContext hosts one registry per owner.
 * Vue does not import this module.
 */
export { createOwnerResourceRegistry, resourceTicket } from './owner-resource'
