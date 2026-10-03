// Tiny syntax highlighter for the code viewer (comments, strings, numbers, keywords, calls).
export type Lang = 'python' | 'r' | 'yaml' | 'markdown' | 'tsv' | 'text' | 'json'

export const langOf = (path: string): Lang =>
  /\.py$/i.test(path) ? 'python' : /\.r$/i.test(path) ? 'r' : /\.ya?ml$/i.test(path) ? 'yaml'
    : /\.md$/i.test(path) ? 'markdown' : /\.(tsv|bed)$/i.test(path) ? 'tsv' : /\.json$/i.test(path) ? 'json' : 'text'

const KW: Record<Lang, string[]> = {
  python: ['def', 'return', 'if', 'elif', 'else', 'for', 'in', 'import', 'from', 'as', 'with', 'not', 'and', 'or', 'None', 'True', 'False', 'class', 'try', 'except', 'raise', 'lambda', 'while', 'pass', 'is'],
  r: ['function', 'if', 'else', 'for', 'in', 'return', 'TRUE', 'FALSE', 'NULL', 'NA', 'library', 'while', 'next', 'break'],
  yaml: [], markdown: [], tsv: [], text: [], json: ['true', 'false', 'null'],
}
const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

export function highlight(line: string, lang: Lang): string {
  if (lang === 'tsv' || lang === 'text') return esc(line) || ' '
  if (lang === 'json') lang = 'python'
  if (lang === 'markdown') return /^#/.test(line) ? `<span class="tk-kw">${esc(line)}</span>` : esc(line) || ' '
  if (lang === 'yaml') {
    const m = line.match(/^(\s*-?\s*)([\w-]+)(:)(.*)$/)
    if (/^\s*#/.test(line)) return `<span class="tk-com">${esc(line)}</span>`
    if (m) return `${esc(m[1]!)}<span class="tk-key">${esc(m[2]!)}</span>${m[3]}<span class="tk-str">${esc(m[4]!)}</span>`
    return esc(line) || ' '
  }
  const kw = KW[lang]
  const re = /(#.*$)|("""[^]*?"""|f?"(?:\\.|[^"\\])*"|f?'(?:\\.|[^'\\])*')|(\b\d+(?:\.\d+)?(?:e-?\d+)?\b)|([A-Za-z_][\w.]*)(?=\s*\()|([A-Za-z_][\w.]*)/g
  let out = ''
  let last = 0
  for (const m of line.matchAll(re)) {
    out += esc(line.slice(last, m.index))
    const [t, com, str, num, call, word] = m
    if (com) out += `<span class="tk-com">${esc(com)}</span>`
    else if (str) out += `<span class="tk-str">${esc(str)}</span>`
    else if (num) out += `<span class="tk-num">${esc(num)}</span>`
    else if (call) out += kw.includes(call) ? `<span class="tk-kw">${esc(call)}</span>` : `<span class="tk-fn">${esc(call)}</span>`
    else if (word) out += kw.includes(word) ? `<span class="tk-kw">${esc(word)}</span>` : esc(word)
    else out += esc(t)
    last = m.index! + t.length
  }
  return out + esc(line.slice(last)) || ' '
}
