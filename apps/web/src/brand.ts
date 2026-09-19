import mark from './assets/soyle-mark.png'

export const brand = {
  name: 'Soyle',
  mark,
} as const

export function pageTitle(title: string) {
  return `${title} · ${brand.name}`
}
