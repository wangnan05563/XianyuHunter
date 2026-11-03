// frontend/src/pages/Chatbot/__tests__/markdownHighlight.test.tsx
// T04105：highlight.js 收敛为语言子集后，聊天代码块的高亮行为不能退化。
// 覆盖点：子集内语言出 token、子集外语言保持原文、no-highlight 被跳过、复制取纯文本。
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MarkdownContent } from '../components/MarkdownContent'

describe('MarkdownContent 语法高亮（lowlight 子集）', () => {
  it('子集内语言（python）渲染出 hljs token', () => {
    const { container } = render(
      <MarkdownContent content={'```python\ndef hello():\n    return 1\n```'} />,
    )
    const code = container.querySelector('code')
    expect(code?.className).toContain('hljs')
    expect(code?.className).toContain('language-python')
    expect(code?.querySelector('.hljs-keyword')?.textContent).toBe('def')
  })

  it('语言别名（py / ts）同样命中子集', () => {
    const { container } = render(
      <MarkdownContent content={'```py\nimport os\n```\n\n```ts\nconst a: number = 1\n```'} />,
    )
    const codes = container.querySelectorAll('code')
    expect(codes.length).toBe(2)
    expect(codes[0].querySelector('.hljs-keyword')).not.toBeNull()
    expect(codes[1].querySelector('.hljs-keyword')).not.toBeNull()
  })

  it('子集外语言（rust）保留原文且不报错', () => {
    const src = 'fn main() { println!("hi"); }'
    const { container } = render(<MarkdownContent content={`\`\`\`rust\n${src}\n\`\`\``} />)
    const code = container.querySelector('code')
    expect(code?.textContent).toContain('fn main()')
    expect(code?.querySelector('.hljs-title')).toBeNull()
  })

  it('no-highlight 标注的代码块不做高亮', () => {
    const { container } = render(
      <MarkdownContent content={'``` no-highlight\nprint(1)\n```'} />,
    )
    const code = container.querySelector('code')
    expect(code?.className).not.toContain('hljs')
    expect(code?.querySelector('span')).toBeNull()
  })

  it('代码块头部展示语言标签与可复制的纯文本', () => {
    render(<MarkdownContent content={'```sql\nSELECT 1\n```'} />)
    expect(screen.getByText('sql')).toBeInTheDocument()
    expect(screen.getByLabelText('复制代码')).toBeInTheDocument()
  })
})
