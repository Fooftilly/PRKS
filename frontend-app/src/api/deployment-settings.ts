/**
 * Typed client for GET /api/deployment-settings (#612), owner only.
 * Transport types come from docs/api/openapi-deployment-settings.json.
 * Read-only: deployment values are set on the host and need a restart, so
 * there is no write here. Runtime parsing still checks the response shape.
 */
import { PrksApiError, prksApiRequest } from './http'
import type { DeploymentSetting, DeploymentSettings } from './generated/deployment-settings'

export type { DeploymentSetting, DeploymentSettings }

const LOAD_ERROR = 'Could not load deployment settings.'

function invalid(): PrksApiError {
  return new PrksApiError(LOAD_ERROR, 200, 'invalid_response')
}

function text(record: Record<string, unknown>, key: string): string {
  const value = record[key]
  if (typeof value !== 'string') throw invalid()
  return value
}

function parseSetting(value: unknown): DeploymentSetting {
  if (value == null || typeof value !== 'object' || Array.isArray(value)) throw invalid()
  const record = value as Record<string, unknown>
  const source = record.source
  if (source !== 'environment' && source !== 'default' && source !== 'command line' && source !== 'bootstrap file') {
    throw invalid()
  }
  return {
    key: text(record, 'key'),
    summary: text(record, 'summary'),
    value: text(record, 'value'),
    default: text(record, 'default'),
    source,
  }
}

export function parseDeploymentSettings(payload: unknown): DeploymentSettings {
  if (payload == null || typeof payload !== 'object' || Array.isArray(payload)) throw invalid()
  const record = payload as Record<string, unknown>
  if (!Array.isArray(record.settings) || record.editable_in_browser !== false) throw invalid()
  return { settings: record.settings.map(parseSetting), editable_in_browser: false }
}

export async function getDeploymentSettings(signal?: AbortSignal): Promise<DeploymentSettings> {
  return parseDeploymentSettings(await prksApiRequest('/api/deployment-settings', { signal }))
}

export function deploymentSettingsErrorMessage(error: unknown): string {
  if (error instanceof PrksApiError && error.status === 403) {
    return 'Only the Library owner can see how this PRKS is deployed.'
  }
  return LOAD_ERROR
}
