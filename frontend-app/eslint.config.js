import pluginVue from 'eslint-plugin-vue'
import tseslint from 'typescript-eslint'
import vueParser from 'vue-eslint-parser'

/**
 * Correctness-oriented Vue + TypeScript lint for frontend-app.
 * Not a formatter. Legacy frontend JavaScript stays on the repo-root ESLint 10
 * bug-rule set in eslint.config.mjs.
 *
 * typescript-eslint recommended's eslintRecommended slice (prefer-const,
 * no-var, and the rest) is files-gated to ts/tsx/mts/cts. That glob is
 * extended to Vue SFCs so script lang=ts gets the same core rules as .ts.
 * vue-eslint-parser applies to .vue only; plain .ts keeps the TypeScript
 * parser from typescript-eslint base.
 */
function recommendedWithVueSfcs() {
  return tseslint.configs.recommended.map((config) => {
    if (config.name !== 'typescript-eslint/eslint-recommended') {
      return config
    }
    const files = Array.isArray(config.files) ? config.files : []
    return {
      ...config,
      files: [...files, '**/*.vue'],
    }
  })
}

export default tseslint.config(
  {
    ignores: [
      'storybook-static/**',
      'node_modules/**',
      'src/api/generated/**',
    ],
  },
  ...pluginVue.configs['flat/essential'],
  ...recommendedWithVueSfcs(),
  {
    files: ['**/*.{ts,vue}'],
    rules: {
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
  {
    files: ['**/*.vue'],
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
    },
  },
)
