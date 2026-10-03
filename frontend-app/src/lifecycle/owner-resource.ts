/**
 * Owner-scoped external resource lifetime.
 *
 * TabContext hosts one registry per owner. A later Vue-native owner can host
 * the same registry. This is not a second TabContext and not a leave decision.
 *
 * A slot is absent, live, or warm-suspended. Cold park and owner destruction
 * dispose every slot. Warm park keeps only a registration that declares
 * `suspendable`. Research Graph does not. The PDF runtime will, in a later
 * slice; this module does not register one.
 *
 * VueUse is not used here. Cytoscape, the PDF viewer, and other owned browser
 * resources outlive a component mount, and their dispose stays on this registry.
 */

export const OWNER_RESOURCE_KINDS = ['researchGraph', 'pdf'] as const

export type OwnerResourceKind = (typeof OWNER_RESOURCE_KINDS)[number]

export interface ResourceTicket {
  ownerId: string
  /** Identity of this owner instance. A replacement with the same id has another token. */
  ownerToken: object
  /** Route generation captured when the work started. */
  generation: number
}

export interface OwnerResourceHost {
  ownerId: string
  ownerToken: object
  generation: () => number
  alive: () => boolean
}

export interface ResourceRegistration<T> {
  kind: OwnerResourceKind
  value: T
  /**
   * Warm park keeps this value and calls `suspend` / `resume`.
   * False releases it on warm park. Cold park and destruction always release it.
   */
  suspendable?: boolean
  dispose: (value: T) => void
  suspend?: (value: T) => void
  resume?: (value: T) => void
}

export type RegisterResult = 'attached' | 'replaced' | 'rejected'

interface Slot {
  kind: OwnerResourceKind
  value: unknown
  suspendable: boolean
  phase: 'live' | 'suspended'
  dispose: (value: unknown) => void
  suspend?: (value: unknown) => void
  resume?: (value: unknown) => void
  disposing: boolean
}

export interface OwnerResourceRegistry {
  register<T>(ticket: ResourceTicket, registration: ResourceRegistration<T>): RegisterResult
  get<T>(kind: OwnerResourceKind): T | undefined
  accepts(ticket: ResourceTicket): boolean
  dispose(kind: OwnerResourceKind): void
  warmSuspend(): void
  resume(): void
  releaseAll(): void
  kinds(): OwnerResourceKind[]
}

function call(fn: ((value: unknown) => void) | undefined, value: unknown): void {
  if (typeof fn !== 'function') return
  try {
    fn(value)
  } catch {
    /* a resource hook must not strand the rest of the owner */
  }
}

export function createOwnerResourceRegistry(host: OwnerResourceHost): OwnerResourceRegistry {
  const slots = new Map<OwnerResourceKind, Slot>()

  function ticketCurrent(ticket: ResourceTicket | null | undefined): ticket is ResourceTicket {
    if (!ticket || typeof ticket !== 'object') return false
    if (!host.alive()) return false
    if (ticket.ownerToken !== host.ownerToken) return false
    if (ticket.ownerId !== host.ownerId) return false
    if (typeof ticket.generation !== 'number') return false
    return ticket.generation === host.generation()
  }

  function drop(slot: Slot): void {
    if (slot.disposing) return
    slot.disposing = true
    slots.delete(slot.kind)
    call(slot.dispose, slot.value)
  }

  function register<T>(ticket: ResourceTicket, registration: ResourceRegistration<T>): RegisterResult {
    if (!ticketCurrent(ticket) || !registration) return 'rejected'
    if (registration.kind !== 'researchGraph' && registration.kind !== 'pdf') return 'rejected'
    const previous = slots.get(registration.kind)
    let result: RegisterResult = 'attached'
    if (previous) {
      drop(previous)
      if (!ticketCurrent(ticket)) return 'rejected'
      result = 'replaced'
    }
    slots.set(registration.kind, {
      kind: registration.kind,
      value: registration.value,
      suspendable: registration.suspendable === true,
      phase: 'live',
      dispose: registration.dispose as (value: unknown) => void,
      suspend: registration.suspend as ((value: unknown) => void) | undefined,
      resume: registration.resume as ((value: unknown) => void) | undefined,
      disposing: false,
    })
    return result
  }

  function get<T>(kind: OwnerResourceKind): T | undefined {
    const slot = slots.get(kind)
    return slot ? (slot.value as T) : undefined
  }

  function dispose(kind: OwnerResourceKind): void {
    const slot = slots.get(kind)
    if (slot) drop(slot)
  }

  function warmSuspend(): void {
    for (const slot of Array.from(slots.values())) {
      if (!slot.suspendable) {
        drop(slot)
        continue
      }
      if (slot.phase === 'suspended') continue
      slot.phase = 'suspended'
      call(slot.suspend, slot.value)
    }
  }

  function resume(): void {
    for (const slot of slots.values()) {
      if (slot.phase !== 'suspended') continue
      slot.phase = 'live'
      call(slot.resume, slot.value)
    }
  }

  function releaseAll(): void {
    for (const slot of Array.from(slots.values())) drop(slot)
  }

  function kinds(): OwnerResourceKind[] {
    return Array.from(slots.keys()).sort()
  }

  return {
    register,
    get,
    accepts: ticketCurrent,
    dispose,
    warmSuspend,
    resume,
    releaseAll,
    kinds,
  }
}

export function resourceTicket(host: OwnerResourceHost, generation?: number): ResourceTicket {
  return {
    ownerId: host.ownerId,
    ownerToken: host.ownerToken,
    generation: typeof generation === 'number' ? generation : host.generation(),
  }
}
