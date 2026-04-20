import { createContext, useContext, useMemo, useState } from 'react'
import { en } from './locales/en'
import { ru } from './locales/ru'

const dictionaries = { en, ru }

type LocaleCode = keyof typeof dictionaries

type I18nContextValue = {
  locale: LocaleCode
  setLocale: (locale: LocaleCode) => void
  t: (key: keyof typeof en, vars?: Record<string, string>) => string
}

const I18nContext = createContext<I18nContextValue | null>(null)

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<LocaleCode>(() => {
    const saved = localStorage.getItem('gateway-locale')
    return saved === 'ru' ? 'ru' : 'en'
  })

  const value = useMemo<I18nContextValue>(
    () => ({
      locale,
      setLocale: (nextLocale) => {
        localStorage.setItem('gateway-locale', nextLocale)
        setLocaleState(nextLocale)
      },
      t: (key, vars) => {
        const template = dictionaries[locale][key] ?? dictionaries.en[key]
        if (!vars) return template
        return Object.entries(vars).reduce(
          (result, [name, value]) => result.split(`{${name}}`).join(value),
          template,
        )
      },
    }),
    [locale],
  )

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

export function useI18n() {
  const value = useContext(I18nContext)
  if (!value) throw new Error('I18nProvider is missing')
  return value
}
