export interface GroupDraft {
  name: string
  parentId: string
  parentQuery: string
  description: string
}

export interface GroupSaveDeps {
  modal: () => HTMLElement | null
  isBusy: () => boolean
  setBusy: (busy: boolean) => void
  readDraft: () => GroupDraft | null
  resolveParent: (parentId: string, parentQuery: string) => Promise<string | undefined>
  createGroup: (body: {
    name: string
    description: string
    parent_id: string
  }) => Promise<{ entity_id?: string } | null>
  alert: (message: string, title: string) => Promise<void>
  failureMessage: (error: unknown) => string
  afterCreate: (created: { entity_id?: string } | null, stillOpening: () => boolean) => Promise<void>
}

/**
 * One in-flight create per modal opening.
 * prepare() may clear busy for a newer draft; an older completion must not
 * clear that busy state or close/navigate the newer opening.
 */
export function createGroupSaveGate() {
  let inFlight: { opening: string } | null = null

  async function save(deps: GroupSaveDeps): Promise<void> {
    const groupModal = deps.modal()
    const opening = groupModal ? groupModal.dataset.prksOpenGeneration || '' : ''
    const matchesOpening = () =>
      !!groupModal && groupModal.dataset.prksOpenGeneration === opening
    const isLiveOpening = () => matchesOpening() && !groupModal!.classList.contains('hidden')
    if (inFlight && inFlight.opening === opening) return
    if (deps.isBusy()) return
    const draft = deps.readDraft()
    if (!draft) return
    const saveToken = { opening }
    inFlight = saveToken
    deps.setBusy(true)
    try {
      let created: { entity_id?: string } | null
      try {
        const parentId = await deps.resolveParent(draft.parentId, draft.parentQuery)
        if (parentId === undefined) return
        if (!matchesOpening()) return
        created = await deps.createGroup({
          name: draft.name.trim(),
          description: draft.description.trim(),
          parent_id: parentId,
        })
      } catch (error) {
        if (!isLiveOpening()) return
        await deps.alert(deps.failureMessage(error), 'Could not save')
        return
      } finally {
        if (inFlight === saveToken) {
          inFlight = null
          if (isLiveOpening()) deps.setBusy(false)
        }
      }
      // Dismiss leaves the generation unchanged; require visibility so we do not
      // closeModals() into an unrelated open modal draft.
      if (!isLiveOpening()) return
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
