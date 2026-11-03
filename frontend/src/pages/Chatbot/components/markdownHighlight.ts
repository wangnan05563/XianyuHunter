/**
 * highlight.js 语言子集 + 轻量 rehype 高亮插件
 *
 * 为什么不直接用 rehype-highlight：它在模块顶层静态 `import {common} from 'lowlight'`，
 * 并在 `settings.languages || common` 里引用它。这行代码让 `common`（约 40 个语言定义）
 * 无法被 tree-shaking，即便传入 languages 子集选项，产物体积也不会下降。
 * 本项目聊天代码块的语言实际集中在 Python / TS-JS / SQL / Shell / 配置几类，
 * 因此自持一个只注册子集的插件，把语言表收敛到真正用到的这些。
 *
 * 行为与 rehype-highlight 默认配置对齐：
 * - 仅处理 `pre > code`；
 * - 未标注语言的代码块不做自动探测（`detect` 默认关闭，避免误判导致的高亮噪声）；
 * - 语言未注册时保持原文不高亮；
 * - `no-highlight` / `nohighlight` class 显式跳过。
 */
import { createLowlight } from 'lowlight'
import bash from 'highlight.js/lib/languages/bash'
import dockerfile from 'highlight.js/lib/languages/dockerfile'
import ini from 'highlight.js/lib/languages/ini'
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import markdown from 'highlight.js/lib/languages/markdown'
import plaintext from 'highlight.js/lib/languages/plaintext'
import python from 'highlight.js/lib/languages/python'
import sql from 'highlight.js/lib/languages/sql'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'
import yaml from 'highlight.js/lib/languages/yaml'
import type { Content, Element, ElementContent, Root } from 'hast'

// 语言别名（py/ts/js/sh/html/yml/toml 等）由 highlight.js 随语言定义自动注册
const lowlight = createLowlight({
  bash,
  dockerfile,
  ini,
  javascript,
  json,
  markdown,
  plaintext,
  python,
  sql,
  typescript,
  xml,
  yaml,
})

function collectText(node: Content): string {
  switch (node.type) {
    case 'text':
    case 'raw':
      return node.value
    case 'element':
      return node.children.map(collectText).join('')
    default:
      return ''
  }
}

// react-markdown 把围栏语言写成 `language-xxx`，手写 HTML 里更常见的是 `lang-xxx`
function resolveLanguage(node: Element): string | false | undefined {
  const list = node.properties.className
  if (!Array.isArray(list)) return undefined
  let detected: string | undefined
  for (const raw of list) {
    const value = String(raw)
    if (value === 'no-highlight' || value === 'nohighlight') return false
    if (!detected && value.startsWith('language-')) detected = value.slice(9)
    if (!detected && value.startsWith('lang-')) detected = value.slice(5)
  }
  // 围栏写成 ``` no-highlight 时，react-markdown 产出的是 language-no-highlight
  if (detected === 'no-highlight' || detected === 'nohighlight') return false
  return detected
}

function highlightCodeElement(node: Element): void {
  const lang = resolveLanguage(node)
  // false（no-highlight）与 undefined（未标语言）都不处理
  if (!lang) return

  let result: Root
  try {
    // 不传 prefix：lowlight 默认 classPrefix 为 `hljs-`，与 atom-one-light.css 的 token 类名一致
    result = lowlight.highlight(lang, collectText(node))
  } catch {
    // 子集之外的语言保持原样：不加 hljs class，避免套上高亮背景却只有纯文本
    return
  }
  const classes = Array.isArray(node.properties.className) ? node.properties.className : []
  if (!classes.includes('hljs')) classes.unshift('hljs')
  node.properties.className = classes
  if (result.children.length > 0) {
    node.children = result.children as ElementContent[]
  }
}

function walk(node: Root | Element, parent: Element | undefined): void {
  for (const child of node.children) {
    if (child.type !== 'element') continue
    if (child.tagName === 'code' && parent?.tagName === 'pre') {
      highlightCodeElement(child)
    }
    walk(child, child)
  }
}

export function rehypeHighlightSubset() {
  return (tree: Root): void => {
    walk(tree, undefined)
  }
}
