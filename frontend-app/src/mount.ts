import { createApp, type App as VueApp } from 'vue'
import { VueQueryPlugin } from '@tanstack/vue-query'
import Root from './App.vue'
import { createPrksQueryClient } from './query/client'

export const PRKS_VUE_ROOT_ID = 'prks-vue-root'

export function mountPrksVue(target: HTMLElement): VueApp<Element> | null {
  if (target.dataset.prksVueMounted === 'true') {
    return null
  }
  const app = createApp(Root)
  app.use(VueQueryPlugin, { queryClient: createPrksQueryClient() })
  app.mount(target)
  target.dataset.prksVueMounted = 'true'
  return app
}
