import type { PersonPayload } from './submit'

export interface PersonSaveDeps {
  modal: () => HTMLElement | null
  isBusy: () => boolean
  setBusy: (busy: boolean) => void
  readPayload: () => PersonPayload | null
  createPerson: (payload: PersonPayload) => Promise<{ entity_id?: string } | null>
  alert: (message: string, title: string) => Promise<void>
  afterCreate: (created: { entity_id?: string } | null, stillOpening: () => boolean) => Promise<void>
}

/**
 * One in-flight create per modal opening.
 * Reopening clears the view busy flag for the new draft; an older completion
 * must not clear that busy state or close/navigate the newer opening.
 */
export function createPersonSaveGate() {
  let inFlight: { opening: string } | null = null

  async function save(deps: PersonSaveDeps): Promise<void> {
    const personModal = deps.modal()
    const opening = personModal ? personModal.dataset.prksOpenGeneration || '' : ''
    const matchesOpening = () =>
      !!personModal && personModal.dataset.prksOpenGeneration === opening
    const isLiveOpening = () => matchesOpening() && !personModal!.classList.contains('hidden')
    if (inFlight && inFlight.opening === opening) return
    if (deps.isBusy()) return
    const payload = deps.readPayload()
    if (!payload) return
    const saveToken = { opening }
    inFlight = saveToken
    deps.setBusy(true)
    try {
      let created: { entity_id?: string } | null
      try {
        created = await deps.createPerson(payload)
      } catch {
        if (!isLiveOpening()) return
        await deps.alert('Could not save this person locally. Please retry.', 'Could not save')
        return
      } finally {
        if (inFlight === saveToken) {
          inFlight = null
          if (isLiveOpening()) deps.setBusy(false)
        }
      }
      if (!matchesOpening()) return
      await deps.afterCreate(created, matchesOpening)
    } finally {
      if (inFlight === saveToken) {
        inFlight = null
        if (isLiveOpening()) deps.setBusy(false)
      }
    }
  }

  return { save }
}
