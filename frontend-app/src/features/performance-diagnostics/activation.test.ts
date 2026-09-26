import { describe, expect, it } from 'vitest'
import {
  activatePerformanceDiagnostics,
  performanceDiagnosticsEnabled,
  registerPerformanceDiagnosticsBridge,
  resetPerformanceDiagnosticsActivationForTests,
} from './activation'

describe('performance diagnostics activation', () => {
  it('stays idle until the legacy settings category asks', () => {
    resetPerformanceDiagnosticsActivationForTests()
    expect(performanceDiagnosticsEnabled().value).toBe(false)
    const target = {
      __prksPerformanceDiagnosticsRequested: true,
    } as Window
    registerPerformanceDiagnosticsBridge(target)
    expect(target.prksVueActivatePerformanceDiagnostics).toBeTypeOf('function')
    expect(performanceDiagnosticsEnabled().value).toBe(true)
    resetPerformanceDiagnosticsActivationForTests()
  })

  it('activates when the bridge is called later', () => {
    resetPerformanceDiagnosticsActivationForTests()
    const target = {} as Window
    registerPerformanceDiagnosticsBridge(target)
    expect(performanceDiagnosticsEnabled().value).toBe(false)
    target.prksVueActivatePerformanceDiagnostics?.()
    expect(performanceDiagnosticsEnabled().value).toBe(true)
    activatePerformanceDiagnostics()
    expect(performanceDiagnosticsEnabled().value).toBe(true)
    resetPerformanceDiagnosticsActivationForTests()
  })
})
