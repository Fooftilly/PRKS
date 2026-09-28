import { describe, expect, it } from 'vitest'
import { buildPeopleIndexProjection, buildPersonDetailProjection, personDetailFromRow } from './projection'

const ada = {
  id: 'P1',
  first_name: 'Ada',
  last_name: 'Lovelace',
  aliases: 'A. Lovelace',
  about: 'Mathematician',
  birth_date: '1815-12-10',
  death_date: '1852',
  assigned_roles: ['Author', 'Editor'],
  groups: [{ id: 'G1', name: 'Analysts' }],
  image_url: 'https://example.test/ada.jpg',
  link_wikipedia: 'https://example.test/wiki',
  link_stanford_encyclopedia: '',
  link_iep: '',
  links_other: '[Notes](https://example.test/notes)',
  works: [{ id: 'W1', title: 'Notes on the engine', role_type: 'Author', order_index: 0, credit_name: 'Ada' }],
}

describe('People projections', () => {
  it('keeps the full collection and labels a role view', () => {
    const projection = buildPeopleIndexProjection({
      items: [ada, { id: 'P2', first_name: 'Grace', last_name: 'Hopper', assigned_roles: ['Reviewer'] }],
      roleFilter: 'Author',
      generation: 3,
    })
    expect(projection.availability).toBe('ready')
    expect(projection.people.map((person) => person.id)).toEqual(['P1', 'P2'])
    expect(projection.roleFilter).toBe('Author')
    expect(projection.roleLabel).toBe('Authors')
    expect(projection.generation).toBe(3)
  })

  it('distinguishes unavailable from an unknown role', () => {
    expect(buildPeopleIndexProjection({ availability: 'unavailable', items: [ada] }).availability).toBe('unavailable')
    expect(buildPeopleIndexProjection({ availability: 'unavailable', items: [ada] }).people).toEqual([])
    expect(buildPeopleIndexProjection({ unknownRole: true, items: [ada] }).availability).toBe('unknown-role')
    expect(buildPeopleIndexProjection({ unknownRole: true, items: [ada] }).people).toEqual([])
  })

  it('projects detail fields, works, and links', () => {
    const person = personDetailFromRow(ada)
    expect(person?.fields.birth_date).toBe('10/12/1815')
    expect(person?.fields.death_date).toBe('1852')
    expect(person?.works[0]?.title).toBe('Notes on the engine')
    expect(person?.works[0]?.roleType).toBe('Author')
    expect(person?.groups[0]?.name).toBe('Analysts')
    expect(person?.links.map((link) => link.label)).toEqual(['Wikipedia', 'Notes'])
    expect(person?.referenceCount).toBe(2)
  })

  it('does not turn a missing person into unavailable', () => {
    const missing = buildPersonDetailProjection({ availability: 'ready', person: null, personId: 'gone' })
    expect(missing.availability).toBe('not-found')
    expect(missing.person).toBeNull()
    const offline = buildPersonDetailProjection({
      availability: 'unavailable',
      person: ada,
      personId: 'P1',
    })
    expect(offline.availability).toBe('unavailable')
    expect(offline.person).toBeNull()
  })
})
