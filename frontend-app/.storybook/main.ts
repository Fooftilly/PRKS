import type { StorybookConfig } from '@storybook/vue3-vite'

// Maintainer catalog. Output stays in frontend-app/storybook-static and is
// not served by PRKS. Docgen stays off. vue-docgen-api is deprecated.
// vue-component-meta (already present via vue-tsc) extracts the real props,
// then also emits Vue internals (key, ref, onVue:* hooks) and leaves local
// aliases such as Variant/Size unresolved. That table would mislead Autodocs.
// Stories name the production states instead. Do not enable blanket Autodocs.
const config: StorybookConfig = {
  stories: ['../src/**/*.stories.ts'],
  addons: ['@storybook/addon-a11y'],
  framework: {
    name: '@storybook/vue3-vite',
    options: {
      docgen: false,
    },
  },
  staticDirs: [{ from: '../../frontend/vendor/inter', to: '/vendor/inter' }],
  core: {
    disableTelemetry: true,
  },
}

export default config
