/** Settings categories. Presentation only: never a route and never a stored setting. */

export const SETTINGS_NARROW_MEDIA_QUERY = '(max-width: 640px)'

export const SETTINGS_CATEGORIES = [
  { id: 'general', label: 'General' },
  { id: 'reading', label: 'Reading & layout' },
  { id: 'export', label: 'Export' },
  { id: 'backup', label: 'Backup' },
  { id: 'maintenance', label: 'Maintenance' },
  { id: 'diagnostics', label: 'Diagnostics' },
] as const

export type SettingsCategoryId = (typeof SETTINGS_CATEGORIES)[number]['id']

const IDS = new Set<string>(SETTINGS_CATEGORIES.map((category) => category.id))

export function settingsCategoryId(value: string | null | undefined): SettingsCategoryId {
  return value != null && IDS.has(value) ? (value as SettingsCategoryId) : 'general'
}
