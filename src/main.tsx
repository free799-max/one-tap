/**
 * React 应用入口文件
 *
 * 负责将根组件 App 挂载到 DOM 中的 #root 节点，
 * 并启用 StrictMode 以帮助发现潜在问题。
 */

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'  // 引入全局样式
import App from './App.tsx'  // 引入根组件

// 获取 DOM 中的根节点，创建 React 根实例并渲染应用
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
