import { mountPrksVue, PRKS_VUE_ROOT_ID } from './mount'
import { registerPerformanceDiagnosticsBridge } from './features/performance-diagnostics/activation'

const target = document.getElementById(PRKS_VUE_ROOT_ID)
if (target) {
  mountPrksVue(target)
}

registerPerformanceDiagnosticsBridge(window)
