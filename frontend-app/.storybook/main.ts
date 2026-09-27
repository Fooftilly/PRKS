import type { StorybookConfig } from '@storybook/vue3-vite'

// Maintainer catalog. Output stays in frontend-app/storybook-static and is
// not served by PRKS. Docgen is off: stories name real states, and the
// deprecated vue-docgen-api pass is not part of this catalog.
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
