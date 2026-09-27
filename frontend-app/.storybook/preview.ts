import type { Preview } from '@storybook/vue3-vite'
import '../../frontend/vendor/inter/inter.css'
import '../../frontend/css/style.css'
import './preview.css'

const preview: Preview = {
  parameters: {
    layout: 'padded',
    controls: { disable: true },
    a11y: {
      context: '#storybook-root',
    },
  },
}

export default preview
