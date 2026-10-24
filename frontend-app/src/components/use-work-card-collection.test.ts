import { defineComponent, nextTick, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { useWorkCardCollection } from './use-work-card-collection'
import { workCardCollectionFingerprint } from './work-card'

type HostWork = { id: string; file_path?: string; thumb_page?: number }

const Host = defineComponent({
  props: {
    works: { type: Array as () => HostWork[], required: true },
    noise: { type: String, default: '' },
    offlineCached: { type: Boolean, default: false },
  },
  setup(props) {
    const root = ref<HTMLElement | null>(null)
    useWorkCardCollection(root, {
      initWhen: () => !props.offlineCached,
      source: () =>
        workCardCollectionFingerprint(props.works, {
          suppressThumbnail: props.offlineCached,
        }),
    })
    return { root }
  },
  template: `
    <div ref="root">
      <span>{{ noise }}</span>
      <div v-for="work in works" :key="work.id" :data-work-id="work.id">
        <img data-prks-thumb-lazy="1" alt="" />
      </div>
    </div>
  `,
})

describe('useWorkCardCollection', () => {
  it('does not release or re-init when only parent noise changes', async () => {
    const release = vi.fn()
    const init = vi.fn()
    window.prksReleaseWorkThumbPreview = release
    window.prksReleaseLazyWorkThumbs = release
    window.prksInitLazyWorkThumbs = init
    window.prksRefreshIcons = () => {}

    const wrapper = mount(Host, {
      props: {
        works: [{ id: 'W-1', file_path: '/api/pdfs/w1.pdf', thumb_page: 1 }],
        noise: 'Ada',
      },
    })
    await nextTick()
    expect(init).toHaveBeenCalledTimes(1)
    release.mockClear()
    init.mockClear()

    await wrapper.setProps({ noise: 'Ada Lovelace' })
    await nextTick()
    expect(release).not.toHaveBeenCalled()
    expect(init).not.toHaveBeenCalled()
  })

  it('releases then inits when the same Work changes thumb page', async () => {
    const release = vi.fn()
    const init = vi.fn()
    window.prksReleaseWorkThumbPreview = release
    window.prksReleaseLazyWorkThumbs = release
    window.prksInitLazyWorkThumbs = init
    window.prksRefreshIcons = () => {}

    const wrapper = mount(Host, {
      props: {
        works: [{ id: 'W-1', file_path: '/api/pdfs/w1.pdf', thumb_page: 1 }],
      },
    })
    await nextTick()
    release.mockClear()
    init.mockClear()

    await wrapper.setProps({
      works: [{ id: 'W-1', file_path: '/api/pdfs/w1.pdf', thumb_page: 2 }],
    })
    await nextTick()
    expect(release).toHaveBeenCalled()
    expect(init).toHaveBeenCalled()
  })
})
