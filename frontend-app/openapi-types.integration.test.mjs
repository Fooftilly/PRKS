import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { generateOpenApiTypes } from './scripts/generate-openapi-types.mjs'

describe('OpenAPI transport types', () => {
  it('matches the checked-in family artifacts on two generations', () => {
    execFileSync(process.execPath, ['scripts/generate-openapi-types.mjs', '--check'], {
      stdio: 'pipe',
    })
    execFileSync(process.execPath, ['scripts/generate-openapi-types.mjs', '--check'], {
      stdio: 'pipe',
    })
  })

  it('rejects an output path that escapes the generated directory', async () => {
    const clientPath = 'src/api/saved-views.ts'
    const before = readFileSync(clientPath, 'utf8')
    await expect(
      generateOpenApiTypes({
        families: [
          {
            name: 'saved-views',
            artifact: 'docs/api/openapi-saved-views.json',
            output: 'frontend-app/src/api/generated/../saved-views.ts',
          },
        ],
      }),
    ).rejects.toThrow('output must resolve inside frontend-app/src/api/generated')
    expect(readFileSync(clientPath, 'utf8')).toBe(before)
  })
})
