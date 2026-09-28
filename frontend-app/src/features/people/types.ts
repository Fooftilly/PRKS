/** People index/detail types. Effective values arrive already overlaid. */

export type PeopleIndexAvailability = 'ready' | 'unavailable' | 'unknown-role'

export type PersonDetailAvailability = 'ready' | 'unavailable' | 'not-found'

export interface PersonGroupChip {
  readonly id: string
  readonly name: string
}

export interface PersonIndexItem {
  readonly id: string
  readonly firstName: string
  readonly lastName: string
  readonly aliases: string
  readonly about: string
  readonly lifespan: string
  readonly roles: readonly string[]
  readonly groups: readonly PersonGroupChip[]
}

export interface PersonWorkItem {
  readonly id: string
  readonly title: string
  readonly roleType: string
  readonly orderIndex: string
  readonly subtitle: string
  readonly filePath: string
  readonly thumbUrl: string
  readonly thumbPage: number | null
  readonly status: string
  readonly docType: string
  readonly year: string
  readonly publishedDate: string
  readonly sizeBytes: number | null
  readonly linkedAuthors: string
  readonly authorText: string
  readonly primaryAuthor: string
  readonly primaryEditor: string
  readonly sourceKind: string
  readonly sourceUrl: string
  readonly provider: string
  readonly providerId: string
}

/** Fields the profile editor may send. Dates stay in the form's display format. */
export interface PersonFieldDraft {
  first_name: string
  last_name: string
  aliases: string
  about: string
  birth_date: string
  death_date: string
  image_url: string
  link_wikipedia: string
  link_stanford_encyclopedia: string
  link_iep: string
  links_other: string
}

export interface PersonDetail {
  readonly id: string
  readonly firstName: string
  readonly lastName: string
  readonly fields: PersonFieldDraft
  readonly lifespan: string
  readonly aliases: readonly string[]
  readonly about: string
  readonly imageUrl: string
  readonly links: readonly { label: string; href: string }[]
  readonly otherLinks: string
  readonly groups: readonly PersonGroupChip[]
  readonly works: readonly PersonWorkItem[]
  readonly referenceCount: number
}
