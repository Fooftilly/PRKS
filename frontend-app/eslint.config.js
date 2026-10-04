import pluginVue from 'eslint-plugin-vue'
import tseslint from 'typescript-eslint'
import vueParser from 'vue-eslint-parser'

/**
 * Correctness-oriented Vue + TypeScript lint for frontend-app.
 * Not a formatter. Legacy frontend JavaScript stays on the repo-root ESLint 10
 * bug-rule set in eslint.config.mjs.
 */
export default tseslint.config(
  {
    ignores: [
      'storybook-static/**',
      'node_modules/**',
      'src/api/generated/**',
    ],
  },
  ...pluginVue.configs['flat/essential'],
  ...tseslint.configs.recommended,
  {
    files: ['**/*.{ts,vue}'],
    languageOptions: {
      parser: vueParser,
      parserOptions: {
        parser: tseslint.parser,
        extraFileExtensions: ['.vue'],
        sourceType: 'module',
        ecmaVersion: 'latest',
      },
    },
    rules: {
      // Icons and sanitizer-backed research markdown remain Vue v-html slots.
      'vue/no-v-html': 'off',
      '@typescript-eslint/no-unused-vars': [
        'error',
        {
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
        },
      ],
      // Vue SFCs already go through vue-tsc; this rule duplicates that noise
      // on template-only refs and generated component types.
      '@typescript-eslint/no-explicit-any': 'off',
    },
  },
)
