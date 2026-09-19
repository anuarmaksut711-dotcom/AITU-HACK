import fs from 'node:fs'
import path from 'node:path'
import assert from 'node:assert/strict'
import ts from 'typescript'
const root = path.resolve(import.meta.dirname, '../src')
const catalogs = Object.fromEntries(['ru', 'en', 'kk'].map((locale) => [locale, JSON.parse(fs.readFileSync(path.join(root, 'i18n', `${locale}.json`), 'utf8'))]))
const keys = Object.keys(catalogs.ru).sort()
const placeholders = (text) => [...text.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort()
for (const [locale, catalog] of Object.entries(catalogs)) {
  assert.deepEqual(Object.keys(catalog).sort(), keys, `${locale}: keys differ`)
  for (const key of keys) {
    assert.ok(catalog[key].trim(), `${locale}: empty ${key}`)
    assert.deepEqual(placeholders(catalog[key]), placeholders(key), `${locale}: placeholders differ in ${key}`)
  }
}
const uses = new Set()
function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const file = path.join(dir, entry.name)
    if (entry.isDirectory()) { if (entry.name !== 'i18n') walk(file); continue }
    if (!/\.tsx?$/.test(file) || file.endsWith('.d.ts')) continue
    const source = ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true)
    function visit(node) {
      if (ts.isCallExpression(node) && node.expression.getText(source) === 't') {
        assert.ok(node.arguments[0] && ts.isStringLiteral(node.arguments[0]), `${file}: use a literal translation key`)
        const key = node.arguments[0].text
        assert.ok(Object.hasOwn(catalogs.ru, key), `${file}: missing ${key}`)
        uses.add(key)
      }
      if ((ts.isStringLiteral(node) || ts.isJsxText(node) || ts.isNoSubstitutionTemplateLiteral(node)) && /[А-Яа-яЁё]/.test(node.text)) {
        const parent = node.parent
        const translated = ts.isCallExpression(parent) && parent.expression.getText(source) === 't'
        const sentinel = node.text === 'Новый чат' && ts.isBinaryExpression(parent)
        const nativeLanguageCode = file.endsWith('language-switcher.tsx') && node.text === 'ҚАЗ'
        const modelContext = file.endsWith('conversation-runner.ts')
        assert.ok(translated || sentinel || nativeLanguageCode || modelContext, `${file}: untranslated interface text: ${node.text}`)
      }
      ts.forEachChild(node, visit)
    }
    visit(source)
  }
}
walk(root)
console.log(`i18n: ${keys.length} keys complete in ru/en/kk; ${uses.size} keys checked in source; interpolation matches.`)
