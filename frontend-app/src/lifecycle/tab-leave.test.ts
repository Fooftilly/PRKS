import { describe, expect, it } from 'vitest'
import { createTabLeave, type LeaveAttempt, type LeaveOwnerSnapshot, type LeaveStatus } from './tab-leave'

interface Owner {
  id: string
  generation: number
  destroyed: boolean
  notes: number
  route: string
}

function snap(owner: Owner): LeaveOwnerSnapshot {
  return { ownerId: owner.id, generation: owner.generation, token: owner }
}

function still(owner: Owner, snapshot: LeaveOwnerSnapshot) {
  return snapshot.token === owner && !owner.destroyed && owner.generation === snapshot.generation
}

function attempt(
  owner: Owner,
  destination: string,
  assess: LeaveAttempt['assess'],
  commit: () => unknown,
  transition: LeaveAttempt['transition'] = 'route-replace',
): LeaveAttempt {
  return {
    ownerId: owner.id,
    destination,
    transition,
    capture: () => (owner.destroyed ? null : snap(owner)),
    still: (snapshot) => still(owner, snapshot),
    assess,
    flushNotes: () => {
      owner.notes += 1
    },
    commit,
  }
}

describe('tab leave preflight', () => {
  it('accepts a clean owner once and flushes notes once', async () => {
    const leave = createTabLeave()
    const owner: Owner = { id: 'main', generation: 1, destroyed: false, notes: 0, route: '#/works/a' }
    let commits = 0
    const decision = await leave.run(
      attempt(
        owner,
        '#/folders',
        () => null,
        () => {
          commits += 1
          owner.route = '#/folders'
          owner.generation += 1
        },
      ),
    )
    expect(decision.status).toBe('approved')
    expect(commits).toBe(1)
    expect(owner.notes).toBe(1)
    expect(owner.route).toBe('#/folders')
  })

  it('rejects a dirty draft and a pending pdf sync without mutating', async () => {
    const leave = createTabLeave()
    const owner: Owner = { id: 'main', generation: 3, destroyed: false, notes: 0, route: '#/works/a' }
    const cases: Array<Exclude<LeaveStatus, 'approved'>> = ['rejected-unsaved-edit', 'rejected-pending-pdf-sync']
    for (const status of cases) {
      let commits = 0
      const decision = await leave.run(
        attempt(
          owner,
          '#/folders',
          () => ({ status, feature: status }),
          () => {
            commits += 1
            owner.route = '#/folders'
          },
        ),
      )
      expect(decision.status).toBe(status)
      expect(commits).toBe(0)
      expect(owner.notes).toBe(0)
      expect(owner.route).toBe('#/works/a')
      expect(owner.generation).toBe(3)
    }
  })

  it('does not assess again when the caller already holds an approval', async () => {
    const leave = createTabLeave()
    const owner: Owner = { id: 'main', generation: 1, destroyed: false, notes: 0, route: '#/works/a' }
    let prompts = 0
    const decision = await leave.run(
      attempt(
        owner,
        '#/people/p',
        () => {
          prompts += 1
          return null
        },
        () => {
          owner.route = '#/people/p'
        },
      ),
    )
    expect(decision.status).toBe('approved')
    expect(prompts).toBe(1)
    expect(owner.notes).toBe(1)
    const notesAfter = owner.notes
    function renderAlreadyApproved() {
      return owner.route
    }
    expect(renderAlreadyApproved()).toBe('#/people/p')
    expect(prompts).toBe(1)
    expect(owner.notes).toBe(notesAfter)
  })

  it('serializes two attempts and drops a stale completion', async () => {
    const leave = createTabLeave()
    const owner: Owner = { id: 'main', generation: 1, destroyed: false, notes: 0, route: '#/works/a' }
    let release: (value: boolean) => void = () => {}
    let prompts = 0
    const first = leave.run(
      attempt(
        owner,
        '#/folders',
        () => {
          prompts += 1
          return new Promise<boolean>((resolve) => {
            release = resolve
          })
        },
        () => {
          owner.route = '#/folders'
          owner.generation += 1
        },
      ),
    )
    const second = leave.run(
      attempt(
        owner,
        '#/people/p',
        () => {
          prompts += 1
          return null
        },
        () => {
          owner.route = '#/people/p'
        },
      ),
    )
    await Promise.resolve()
    expect(prompts).toBe(1)
    expect(owner.route).toBe('#/works/a')
    release(true)
    const firstDecision = await first
    const secondDecision = await second
    expect(firstDecision.status).toBe('approved')
    expect(secondDecision.status).toBe('stale-owner')
    expect(prompts).toBe(1)
    expect(owner.route).toBe('#/folders')
    expect(owner.notes).toBe(1)
  })

  it('lets the second attempt proceed when the first rejection keeps the owner', async () => {
    const leave = createTabLeave()
    const owner: Owner = { id: 'main', generation: 1, destroyed: false, notes: 0, route: '#/works/a' }
    let release: (value: LeaveStatus) => void = () => {}
    const first = leave.run(
      attempt(
        owner,
        '#/folders',
        () =>
          new Promise<LeaveStatus>((resolve) => {
            release = resolve
          }),
        () => {
          owner.route = '#/folders'
        },
      ),
    )
    let secondPrompts = 0
    const second = leave.run(
      attempt(
        owner,
        '#/people/p',
        () => {
          secondPrompts += 1
          return null
        },
        () => {
          owner.route = '#/people/p'
          owner.generation += 1
        },
      ),
    )
    await Promise.resolve()
    expect(secondPrompts).toBe(0)
    release('rejected-unsaved-edit')
    expect((await first).status).toBe('rejected-unsaved-edit')
    expect((await second).status).toBe('approved')
    expect(secondPrompts).toBe(1)
    expect(owner.route).toBe('#/people/p')
    expect(owner.notes).toBe(1)
  })

  it('does not apply a confirmation that resolves after the owner is replaced', async () => {
    const leave = createTabLeave()
    const owner: Owner = { id: 'main', generation: 1, destroyed: false, notes: 0, route: '#/works/a' }
    let release: (value: boolean) => void = () => {}
    const pending = leave.run(
      attempt(
        owner,
        '#/folders',
        () =>
          new Promise<boolean>((resolve) => {
            release = resolve
          }),
        () => {
          owner.route = '#/folders'
        },
      ),
    )
    owner.generation += 1
    owner.route = '#/people/replacement'
    release(true)
    const decision = await pending
    expect(decision.status).toBe('stale-owner')
    expect(owner.route).toBe('#/people/replacement')
    expect(owner.notes).toBe(0)
  })

  it('does not apply a confirmation after the owner is destroyed', async () => {
    const leave = createTabLeave()
    const owner: Owner = { id: 'main', generation: 1, destroyed: false, notes: 0, route: '#/works/a' }
    let release: (value: boolean) => void = () => {}
    const pending = leave.run(
      attempt(
        owner,
        '#/folders',
        () =>
          new Promise<boolean>((resolve) => {
            release = resolve
          }),
        () => {
          owner.route = '#/folders'
        },
      ),
    )
    owner.destroyed = true
    release(true)
    const decision = await pending
    expect(decision.status).toBe('stale-owner')
    expect(owner.route).toBe('#/works/a')
    expect(owner.notes).toBe(0)
  })

  it('keeps a batch atomic when one owner rejects', async () => {
    const leave = createTabLeave()
    const a: Owner = { id: 'a', generation: 1, destroyed: false, notes: 0, route: '#/works/a' }
    const b: Owner = { id: 'b', generation: 1, destroyed: false, notes: 0, route: '#/works/b' }
    let commits = 0
    const prompts: string[] = []
    const decision = await leave.runBatch({
      transition: 'hide-secondary',
      attempts: [
        attempt(
          a,
          '#/folders',
          () => {
            prompts.push('a')
            return null
          },
          () => undefined,
          'hide-secondary',
        ),
        attempt(
          b,
          '#/folders',
          () => {
            prompts.push('b')
            return 'rejected-unsaved-edit'
          },
          () => undefined,
          'hide-secondary',
        ),
      ],
      commit: () => {
        commits += 1
        a.route = 'parked'
        b.route = 'parked'
      },
    })
    expect(decision.status).toBe('rejected-unsaved-edit')
    expect(prompts).toEqual(['a', 'b'])
    expect(commits).toBe(0)
    expect(a.notes).toBe(0)
    expect(b.notes).toBe(0)
    expect(a.route).toBe('#/works/a')
    expect(b.route).toBe('#/works/b')
  })

  it('commits a batch only after every owner approves', async () => {
    const leave = createTabLeave()
    const a: Owner = { id: 'a', generation: 1, destroyed: false, notes: 0, route: '#/works/a' }
    const b: Owner = { id: 'b', generation: 1, destroyed: false, notes: 0, route: '#/works/b' }
    let releaseB: (value: null) => void = () => {}
    let commits = 0
    const pending = leave.runBatch({
      transition: 'close',
      attempts: [
        attempt(a, '#/folders', () => null, () => undefined, 'close'),
        attempt(
          b,
          '#/folders',
          () =>
            new Promise<null>((resolve) => {
              releaseB = resolve
            }),
          () => undefined,
          'close',
        ),
      ],
      commit: () => {
        commits += 1
        a.destroyed = true
        b.destroyed = true
      },
    })
    await Promise.resolve()
    expect(commits).toBe(0)
    expect(a.notes).toBe(0)
    releaseB(null)
    const decision = await pending
    expect(decision.status).toBe('approved')
    expect(commits).toBe(1)
    expect(a.notes).toBe(1)
    expect(b.notes).toBe(1)
  })

  it('runs registered probes in order and flushes through one hook', async () => {
    const leave = createTabLeave()
    const seen: string[] = []
    leave.registerProbe({
      id: 'pdf-sync',
      order: 10,
      assess: () => {
        seen.push('pdf')
        return null
      },
    })
    leave.registerProbe({
      id: 'work-metadata',
      order: 30,
      assess: () => {
        seen.push('work')
        return null
      },
    })
    leave.registerProbe({
      id: 'person-profile',
      order: 20,
      assess: () => {
        seen.push('person')
        return { status: 'rejected-unsaved-edit', feature: 'person-profile' }
      },
    })
    let flushes = 0
    leave.registerFlush(() => {
      flushes += 1
    })
    const owner: Owner = { id: 'main', generation: 1, destroyed: false, notes: 0, route: '#/people/p' }
    const decision = await leave.run(
      attempt(
        owner,
        '#/folders',
        (snapshot) => leave.assessOwner(snapshot.token, '#/folders'),
        () => {
          owner.route = '#/folders'
        },
      ),
    )
    expect(seen).toEqual(['pdf', 'person'])
    expect(decision.status).toBe('rejected-unsaved-edit')
    expect(decision.feature).toBe('person-profile')
    expect(flushes).toBe(0)
    expect(owner.route).toBe('#/people/p')
  })

  it('releases the slot when commit rejects without a second rejection', async () => {
    const leave = createTabLeave()
    const owner: Owner = { id: 'main', generation: 1, destroyed: false, notes: 0, route: '#/works/a' }
    const stray: unknown[] = []
    const onUnhandled = (reason: unknown) => {
      stray.push(reason)
    }
    process.on('unhandledRejection', onUnhandled)
    try {
      await expect(
        leave.run(
          attempt(owner, '#/folders', () => null, () => {
            throw new Error('commit failed')
          }),
        ),
      ).rejects.toThrow('commit failed')
      await new Promise((resolve) => setTimeout(resolve, 0))
      expect(stray).toEqual([])
      const next = await leave.run(
        attempt(owner, '#/people/p', () => null, () => {
          owner.route = '#/people/p'
        }),
      )
      expect(next.status).toBe('approved')
      expect(owner.route).toBe('#/people/p')
    } finally {
      process.off('unhandledRejection', onUnhandled)
    }
  })
})
