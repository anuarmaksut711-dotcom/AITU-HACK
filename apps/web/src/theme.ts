import { Button, FileInput, PasswordInput, Select, Textarea, TextInput, createTheme, type CSSVariablesResolver } from '@mantine/core'

export const cssVariablesResolver: CSSVariablesResolver = (theme) => ({
  variables: {},
  light: {
    '--mantine-color-body': '#fffefa',
    '--mantine-color-text': theme.black,
    '--mantine-color-dimmed': theme.colors.gray[7],
    '--mantine-color-error': theme.colors.red[9],
    '--mantine-color-red-text': theme.colors.red[9],
  },
  dark: {},
})

export const theme = createTheme({
  primaryColor: 'forest',
  primaryShade: 7,
  black: '#202e27',
  colors: {
    forest: ['#edf5ef', '#dcece1', '#b9d7c5', '#93bea6', '#6fa589', '#4d8b6e', '#347358', '#215c49', '#194a3b', '#123b2f'],
    gray: ['#f6f5f0', '#efefe8', '#e6e8e0', '#d8ddd5', '#b9c2b9', '#8d9b91', '#677c70', '#4e6458', '#36493f', '#22372b'],
  },
  defaultRadius: 'md',
  fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  headings: { fontWeight: '600', sizes: { h1: { fontSize: '2.5rem', lineHeight: '1.16' } } },
  respectReducedMotion: true,
  components: {
    Button: Button.extend({ defaultProps: { size: 'md' } }),
    TextInput: TextInput.extend({ defaultProps: { size: 'md' } }),
    PasswordInput: PasswordInput.extend({ defaultProps: { size: 'md' } }),
    Textarea: Textarea.extend({ defaultProps: { size: 'md' } }),
    Select: Select.extend({ defaultProps: { size: 'md', allowDeselect: false } }),
    FileInput: FileInput.extend({ defaultProps: { size: 'md' } }),
  },
})
