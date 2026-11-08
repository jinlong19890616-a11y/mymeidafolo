<script setup lang="ts">
import { RenderProps } from '@/api/types'

// 定义 props
defineProps<{
  config: RenderProps // JSON 配置
  model: Record<string, any> // 数据模型
}>()

/**
 * 解析属性，支持 v-model 和动态绑定
 * @param rawProps 原始属性
 * @param model 数据模型
 * @returns 解析后的属性
 */
const parseProps = (rawProps: Record<string, any>, model: Record<string, any>) => {
  const parsedProps: Record<string, any> = {}

  const isExpression = (value: string) => value.startsWith('{{') && value.endsWith('}}')
  const extractExpression = (value: string) => value.slice(2, -2).trim()

  for (const [key, value] of Object.entries(rawProps)) {
    if (key === 'modelvalue') {
      // 将 modelvalue 转换为 v-model:value 的形式
      parsedProps['value'] = model[value]
      parsedProps['onUpdate:value'] = (newValue: any) => {
        model[value] = newValue
      }
    } else if (['model', 'v-model'].includes(key)) {
      // 处理 v-model
      parsedProps['modelValue'] = model[value]
      parsedProps['onUpdate:modelValue'] = (newValue: any) => {
        model[value] = newValue
      }
    } else if (['show', 'v-show'].includes(key)) {
      // 处理 v-show，实现显示隐藏
      const expression = isExpression(value) ? extractExpression(value) : value
      const isVisible = new Function('model', `with(model) { return ${expression} }`)(model)
      // 动态设置 style.display
      if (!parsedProps.style) {
        parsedProps.style = {}
      }
      parsedProps.style.display = isVisible ? '' : 'none'
    } else if (key.startsWith('model:') || key.startsWith('v-model:')) {
      // 处理 v-model:<prop>
      const propName = key.split(':')[1]
      parsedProps[propName] = model[value]
      parsedProps[`onUpdate:${propName}`] = (newValue: any) => {
        model[value] = newValue
      }
    } else if (key.startsWith('on')) {
      // 处理事件监听，值是函数的代码 function xxx(e) { ... }
      if (typeof value === 'string') {
        // 创建动态函数并绑定model上下文
        const handler = new Function(
          'model',
          'event',
          `
            try {
              with(model) {
                return (${value})(event);
              }
            } catch(e) {
              console.error('事件处理函数执行错误:', e);
            }
          `,
        )
        // 包装事件处理器，保持vue事件参数传递特性
        parsedProps[key] = (...args: any[]) => {
          const [event] = args
          return handler(model, event)
        }
      }
    } else {
      // 如果是表达式，需要绑定
      if (typeof value === 'string' && isExpression(value)) {
        const expression = extractExpression(value)
        parsedProps[key] = new Function('model', `with(model) { return ${expression} }`)(model)
      } else if (typeof value === 'string' && value in model) {
        // 如果是数据模型的属性，直接绑定
        parsedProps[key] = model[value]
      } else {
        // 其他情况直接赋值
        parsedProps[key] = value
      }
    }
  }

  return parsedProps
}

/**
 * 渲染插槽内容
 * @param slotContent 插槽配置
 * @param model 数据模型
 * @param slotScope 插槽作用域
 */
const renderSlotContent = (slotContent: any, model: any, slotScope: any) => {
  if (Array.isArray(slotContent)) {
    // 如果插槽内容是数组，递归渲染
    return slotContent.map(childConfig => renderComponent(childConfig, model, slotScope))
  }
  // 如果插槽内容是单个配置，递归渲染
  return renderComponent(slotContent, model, slotScope)
}

/**
 * 渲染组件函数（递归支持嵌套）
 * @param config JSON 配置
 * @param model 数据模型
 * @param slotScope 插槽作用域
 * @returns 渲染的组件 VNode
 */
const renderComponent = (config: any, model: any, slotScope: any = {}) => {
  const { component, props: componentProps = {}, content = [], slots = {}, html, text } = config

  // 动态解析组件
  const Component = resolveComponent(component)

  // 解析属性
  const parsedProps = parseProps(componentProps, model)

  // 动态插槽解析
  const slotNodes: Record<string, any> = {}
  for (const [slotName, slotContent] of Object.entries(slots)) {
    slotNodes[slotName] = (slotScopeData: any) =>
      renderSlotContent(slotContent, model, { ...slotScope, ...slotScopeData })
  }

  // 渲染组件内容
  const renderContent = () => {
    // 如果配置了 `html`，直接渲染为 HTML 内容
    if (html) {
      return h(Component, { innerHTML: typeof html === 'string' ? html : model[html] })
    }

    // 如果配置了 `text`，直接渲染为文本内容
    if (text) {
      return typeof text === 'string' ? text : model[text]
    }

    // 如果配置了 `content`，递归渲染子组件
    if (Array.isArray(content)) {
      return content.map((childConfig: any) => renderComponent(childConfig, model, slotScope))
    }

    return null
  }

  // 渲染组件
  return h(Component, parsedProps, {
    ...slotNodes,
    default: renderContent,
  })
}
</script>

<template>
  <Component :is="renderComponent(config, model)" />
</template>
