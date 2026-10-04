import { execFileSync } from 'node:child_process'
import { describe, it } from 'vitest'

describe('OpenAPI transport types', () => {
  it('matches the checked-in family artifacts on two generations', () => {
    execFileSync(process.execPath, ['scripts/generate-openapi-types.mjs', '--check'], {
      stdio: 'pipe',
    })
    execFileSync(process.execPath, ['scripts/generate-openapi-types.mjs', '--check'], {
      stdio: 'pipe',
    })
  })
})
