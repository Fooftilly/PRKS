import { createApp, type App as VueApp } from 'vue'
import SetupApp from './SetupApp.vue'

/** The element of frontend/setup.html the first-run setup flow mounts on (#612). */
export const PRKS_SETUP_ROOT_ID = 'prks-setup-root'

export function mountPrksSetup(target: HTMLElement): VueApp<Element> | null {
  if (target.dataset.prksVueMounted === 'true') return null
  const app = createApp(SetupApp)
  app.mount(target)
  target.dataset.prksVueMounted = 'true'
  return app
}
