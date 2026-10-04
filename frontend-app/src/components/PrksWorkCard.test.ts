import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PrksWorkCard from './PrksWorkCard.vue'
import {
  WORK_THUMB_PLACEHOLDER,
  workCardCollectionFingerprint,
  workCardCreditText,
  workCardEmptyThumbTitle,
  workCardFileSizeLabel,
  workCardThumbOptions,
  workCardThumbUrl,
  workCardYearPlain,
} from './work-card'

describe('Work-card helpers', () => {
  it('keeps bibliographic credit and year separate from contextual subtitle', () => {
    const work = {
      id: 'W-1',
      title: 'The Culture Industry',
      primary_author: 'Theodor W. Adorno',
      year: '1972',
    }
    expect(workCardCreditText(work)).toBe('Author: Theodor W. Adorno')
    expect(workCardYearPlain(work)).toBe('1972')
    expect(workCardYearPlain({ id: 'W-2', published_date: '2001-06-01' })).toBe('2001')
    expect(workCardYearPlain({ id: 'W-bad', year: [] })).toBe('')
    expect(workCardFileSizeLabel({ file_size_bytes: 1024 * 1024 })).toBe('1.00 MB')
  })

  it('suppresses cached thumbs and states PDF page 1 explicitly', () => {
    const pdf = { id: 'W-8', file_path: '/api/pdfs/w8.pdf' }
    expect(workCardThumbUrl(pdf)).toBe('/api/works/W-8/thumbnail?page=1')
    expect(workCardThumbUrl(pdf, { suppressThumbnail: true })).toBe('')
    expect(workCardEmptyThumbTitle(pdf, { suppressThumbnail: true })).toBe('Preview not available offline')
    expect(workCardThumbOptions(true, { subtitle: 'sub' })).toEqual({
      subtitle: 'sub',
      suppressThumbnail: true,
    })
    expect(workCardThumbOptions(false, { subtitle: 'sub' })).toEqual({ subtitle: 'sub' })
  })

  it('fingerprints work/thumb identity, not parent noise', () => {
    const pdf = { id: 'W-8', file_path: '/api/pdfs/w8.pdf', thumb_page: 1, title: 'One' }
    const sameThumb = workCardCollectionFingerprint([{ ...pdf, title: 'Edited' }])
    const origin = workCardCollectionFingerprint([pdf])
    expect(sameThumb).toBe(origin)
    expect(workCardCollectionFingerprint([{ ...pdf, thumb_page: 2 }])).not.toBe(origin)
    expect(
      workCardCollectionFingerprint([
        { id: 'W-5', source_kind: 'video', thumb_url: 'https://img.example/a.jpg' },
      ]),
    ).not.toBe(
      workCardCollectionFingerprint([
        { id: 'W-5', sourceKind: 'video', thumbUrl: 'https://img.example/b.jpg' },
      ]),
    )
    expect(workCardCollectionFingerprint([pdf], { suppressThumbnail: true })).not.toBe(origin)
  })
})

describe('PrksWorkCard', () => {
  it('is one dense navigation card with meta above context', () => {
    const wrapper = mount(PrksWorkCard, {
      props: {
        work: {
          id: 'W-1',
          title: 'The Culture Industry',
          primary_author: 'Theodor W. Adorno',
          year: '1972',
          status: 'In Progress',
          file_size_bytes: 1024 * 1024,
          file_path: '/api/pdfs/w1.pdf',
        },
        options: { subtitle: 'Added Sep 5, 2026' },
      },
    })
    const card = wrapper.get('.project-card--work-card')
    expect(card.attributes('data-work-id')).toBe('W-1')
    expect(card.attributes('data-prks-route')).toBe('#/works/W-1')
    expect(card.attributes('role')).toBeUndefined()
    expect(card.attributes('tabindex')).toBeUndefined()
    const link = wrapper.get('a.work-card__link')
    expect(link.element.tagName).toBe('A')
    expect(link.attributes('href')).toBe('#/works/W-1')
    expect(link.attributes('aria-label')).toBe('The Culture Industry')
    expect(wrapper.find('.work-card__select').exists()).toBe(false)
    expect(wrapper.get('.card-title').text()).toBe('The Culture Industry')
    expect(wrapper.get('.work-card__meta').text()).toBe('Author: Theodor W. Adorno · 1972')
    expect(wrapper.get('.work-card__context').text()).toBe('Added Sep 5, 2026')
    expect(wrapper.get('.work-card__meta').text()).not.toContain('Added Sep 5')
    expect(wrapper.get('.status-badge').text()).toBe('In Progress')
    expect(wrapper.get('.status-badge').classes()).toContain('In.Progress')
    expect(wrapper.get('.work-card__file-size').text()).toBe('1.00 MB')
    expect(wrapper.get('.work-card__thumb').classes()).toContain('work-card__thumb--pdf')
    expect(wrapper.get('.work-card__thumb').classes()).toContain('work-card__thumb--loading')
    expect(wrapper.get('.work-card__thumb').attributes('data-prks-thumb-preview-kind')).toBe('pdf')
    expect(wrapper.get('.work-card__thumb').attributes('data-prks-thumb-page')).toBe('1')
    expect(wrapper.get('img').attributes('data-prks-thumb-lazy')).toBe('1')
    expect(wrapper.html()).not.toContain('data-prks-thumb-preview-src')
    expect(wrapper.html()).not.toContain('data-prks-thumb-src=')
  })

  it('omits context, hides the type badge, and uses an empty offline thumb', () => {
    const wrapper = mount(PrksWorkCard, {
      props: {
        work: { id: 'W-2', title: 'No Subtitle', year: '2001', doc_type: 'article' },
        options: { hideDocTypeBadge: true, suppressThumbnail: true },
      },
    })
    expect(wrapper.find('.work-card__context').exists()).toBe(false)
    expect(wrapper.get('.work-card__thumb').classes()).toContain('work-card__thumb--empty')
    expect(wrapper.get('.work-card__thumb').attributes('title')).toBe('Preview not available offline')
    expect(wrapper.html()).not.toContain('/thumbnail')
  })

  it('keeps video thumbs off the markup URL and empty video slots source-aware', () => {
    const withThumb = mount(PrksWorkCard, {
      props: {
        work: {
          id: 'W-5',
          title: 'Video Work',
          source_kind: 'video',
          thumb_url: 'https://img.example/thumb.jpg',
        },
      },
    })
    expect(withThumb.get('.work-card__thumb').classes()).toContain('work-card__thumb--video')
    expect(withThumb.html()).not.toContain('https://img.example/thumb.jpg')
    expect(withThumb.get('img').attributes('data-prks-thumb-lazy')).toBe('1')

    const empty = mount(PrksWorkCard, {
      props: { work: { id: 'W-6', title: 'Video no thumb', source_kind: 'video' } },
    })
    expect(empty.get('.work-card__thumb').classes()).toContain('work-card__thumb--empty')
    expect(empty.get('.work-card__thumb').classes()).toContain('work-card__thumb--video')
    expect(empty.get('.work-card__thumb').attributes('title')).toBe('No video preview')
  })

  it('recreates the thumb subtree when the same Work changes page', async () => {
    const wrapper = mount(PrksWorkCard, {
      props: {
        work: {
          id: 'W-8',
          title: 'Paged',
          file_path: '/api/pdfs/w8.pdf',
          thumb_page: 1,
        },
      },
    })
    const img = wrapper.get('img').element as HTMLImageElement
    expect(img.getAttribute('data-prks-thumb-lazy')).toBe('1')
    img.setAttribute('src', '/hydrated-page-1.png')
    img.removeAttribute('data-prks-thumb-lazy')
    await wrapper.setProps({
      work: {
        id: 'W-8',
        title: 'Paged',
        file_path: '/api/pdfs/w8.pdf',
        thumb_page: 2,
      },
    })
    const next = wrapper.get('img').element as HTMLImageElement
    expect(next).not.toBe(img)
    expect(next.getAttribute('data-prks-thumb-lazy')).toBe('1')
    expect(next.getAttribute('src')).toBe(WORK_THUMB_PLACEHOLDER)
    expect(wrapper.get('.work-card__thumb').attributes('data-prks-thumb-page')).toBe('2')
  })
})
