import { render, screen, fireEvent } from '@testing-library/react'
import { vi } from 'vitest'
import ModelConfigForm from '../components/ModelConfigForm'

// 保存按钮已统一收到页面级 sticky 头（AIConfig/index.tsx），表单本身只负责把编辑写回草稿；
// 这里钉住「文本解析模型」字段的写回，避免表单又自造一份保存动作。
test('文本解析模型字段的编辑会写回草稿', () => {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation(() => ({
      matches: false,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  })
  const onConfigChange = vi.fn()
  render(
    <ModelConfigForm
      config={{
        ai_enabled: true,
        base_url: 'https://api.openai.com/v1',
        api_key: '****1234',
        model: 'gpt-4.1-mini',
        vision_model: 'gpt-4o',
      }}
      onConfigChange={onConfigChange}
      showApiKey={false}
      onToggleShowApiKey={vi.fn()}
      onApplyPreset={vi.fn()}
      testing={false}
      testResult={null}
      onTestConnection={vi.fn()}
    />,
  )

  fireEvent.change(screen.getByLabelText('文本解析模型'), { target: { value: 'gpt-4.1' } })

  expect(onConfigChange).toHaveBeenCalledWith({ model: 'gpt-4.1' })
})
