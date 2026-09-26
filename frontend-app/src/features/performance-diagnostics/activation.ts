import { ref } from 'vue'

const active = ref(false)

/** First Diagnostics activation enables the server read. It is never turned back off. */
export function performanceDiagnosticsEnabled() {
  return active
}

export function activatePerformanceDiagnostics(): void {
  active.value = true
}

export function resetPerformanceDiagnosticsActivationForTests(): void {
  active.value = false
}

export function registerPerformanceDiagnosticsBridge(target: Window = window): void {
  target.prksVueActivatePerformanceDiagnostics = activatePerformanceDiagnostics
  if (target.__prksPerformanceDiagnosticsRequested === true) {
    activatePerformanceDiagnostics()
  }
}
