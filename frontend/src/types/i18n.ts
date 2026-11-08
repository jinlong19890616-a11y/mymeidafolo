export interface LocaleInfo {
  name: string
  title: string
  flag?: string
}

export const SUPPORTED_LOCALES: Record<string, LocaleInfo> = {
  'zh-CN': {
    name: 'zh-CN',
    title: '简体中文',
    flag: '🇨🇳',
  },
  'zh-TW': {
    name: 'zh-TW',
    title: '繁體中文',
    flag: '🇨🇳',
  },
  'en-US': {
    name: 'en-US',
    title: 'English',
    flag: '🇺🇸',
  },
}

export type SupportedLocale = keyof typeof SUPPORTED_LOCALES
