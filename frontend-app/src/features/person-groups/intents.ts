import type { InjectionKey } from 'vue'
import type { PersonGroupFieldDraft } from './types'

/** Owning TabContext fields Person Group intents need. Not a second route model. */
export interface PersonGroupIntentOwner {
  tabId?: string
  generation?: number
  isCurrent?: (generation: number) => boolean
  lastResolvedRoute?: { name?: string; params?: { groupId?: string } } | null
  route?: { name?: string; params?: { groupId?: string } } | null
  root?: HTMLElement | null
  ui?: {
    personGroupEditing?: boolean
    personGroupMembersEditing?: boolean
    personGroupEditSession?: number
    personGroupMemberSession?: number
    personGroupFieldBaseline?: PersonGroupFieldDraft & { groupId?: string } | null
  }
  getEntity?: (type: string) => { id?: string; name?: string } | null
  setEntity?: (type: string, value: unknown) => void
}

export interface PersonGroupSaveResult {
  ok: boolean
  quiet?: boolean
  message?: string
}

export interface PersonGroupIntents {
  ownerTabId(): string
  create(): void
  openGroup(groupId: string): void
  openPerson(personId: string): void
  beginEdit(): void
  cancelEdit(): void
  toggleMembers(): void
  editSession(): number
  memberSession(): number
  capturedBaseline(groupId: string): PersonGroupFieldDraft | null
  save(groupId: string, draft: PersonGroupFieldDraft, baseline: PersonGroupFieldDraft, session: number): Promise<PersonGroupSaveResult>
  remove(groupId: string, session: number): Promise<void>
  bindChrome(): void
}

export const personGroupIntentsKey: InjectionKey<PersonGroupIntents> = Symbol('prks-person-group-intents')

function editSessionOf(owner: PersonGroupIntentOwner | null | undefined): number {
  const value = owner?.ui?.personGroupEditSession
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function memberSessionOf(owner: PersonGroupIntentOwner | null | undefined): number {
  const value = owner?.ui?.personGroupMemberSession
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

export function browserPersonGroupIntents(owner: PersonGroupIntentOwner | null | undefined): PersonGroupIntents {
  return {
    ownerTabId() {
      return owner?.tabId ? String(owner.tabId) : ''
    },

    create() {
      const openFromPage = window.prksOpenNewGroupModalFromGroupsPage
      if (typeof openFromPage === 'function') {
        openFromPage(owner ?? undefined)
        return
      }
      const open = window.openModal
      if (typeof open === 'function') open('group-modal')
    },

    openGroup(groupId) {
      const navigate = window.prksNavigate
      if (typeof navigate !== 'function') return
      navigate(`#/people/groups/${encodeURIComponent(groupId)}`, { tabId: owner?.tabId })
    },

    openPerson(personId) {
      const navigate = window.prksNavigate
      if (typeof navigate !== 'function') return
      navigate(`#/people/${encodeURIComponent(personId)}`, { tabId: owner?.tabId })
    },

    beginEdit() {
      const open = window.openPersonGroupEdit
      if (typeof open === 'function') open(owner ?? undefined)
    },

    cancelEdit() {
      const close = window.closePersonGroupEdit
      if (typeof close === 'function') close(owner ?? undefined)
    },

    toggleMembers() {
      const toggle = window.prksTogglePersonGroupMembersEdit
      if (typeof toggle === 'function') toggle(owner ?? undefined)
    },

    editSession() {
      return editSessionOf(owner)
    },

    memberSession() {
      return memberSessionOf(owner)
    },

    capturedBaseline(groupId) {
      const stored = owner?.ui?.personGroupFieldBaseline
      if (!stored || String(stored.groupId || '') !== String(groupId)) return null
      return {
        name: String(stored.name || ''),
        description: String(stored.description || ''),
        parent_id: String(stored.parent_id || ''),
        parent_name: String(stored.parent_name || ''),
      }
    },

    async save(groupId, draft, baseline, session) {
      const save = window.savePersonGroupEditor
      if (typeof save !== 'function') return { ok: false, message: 'Could not save this group.' }
      return save(owner, groupId, draft, baseline, session)
    },

    async remove(groupId, session) {
      const remove = window.deletePersonGroupEditor
      if (typeof remove !== 'function') return
      await remove(owner, groupId, session)
    },

    bindChrome() {
      const bind = window.prksBindPersonGroupDetailChrome
      if (typeof bind === 'function' && owner) bind(owner)
    },
  }
}
