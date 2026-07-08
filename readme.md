# OverWall - 你有张良计 我有过墙梯

> v1.1 — 某企业安全积分自动化脚本

## 功能

| 模块 | 说明 |
|------|------|
| 每日练习 | AI 答题 + 本地题库缓存，日限 3 分自动停止 |
| 模拟考试 | 手动触发，支持单选/多选/判断/填空/简答，需手动签名 |
| 图文学习 | 自动点击文章卡片 → 等待倒计时 → 获取积分 |
| 视频学习 | 自动点击视频卡片 → 播放 → 等待倒计时 → 获取积分 |
| 自动刷分 | 按策略优先级自动循环执行 |

## 技术栈

- **后端**: Python + Flask + Playwright
- **前端**: 原生 HTML/CSS/JS（轮询 + 设置覆盖层）
- **AI**: DeepSeek API（支持 V3 / R1 / V4 Pro）
- **题库**: JSON 本地存储，MD5 去重，置信度分级

## 系统兼容性

| 打包版本 | 适用系统 | 说明 |
|---------|---------|------|
| `dist/OverWall_Win11.exe` | **Windows 10 / Windows 11 (64位)** | Python 3.13 构建，开箱即用 |
| 源码运行 | Windows / macOS / Linux | 需 Python 3.9+ 环境 |

> Windows 7 和 Windows 8/8.1 不再支持。老系统缺少新版 API Set（`api-ms-win-core-path-l1-1-0` 等），无法运行打包后的 exe，直接源码运行也不保证稳定性。

## 快速开始

### 方式一：直接运行 exe（Windows 10/11）

下载 `dist/OverWall_Win11.exe`，双击运行，浏览器自动打开 `http://127.0.0.1:15888`

### 方式二：源码运行

```bash
pip install -r requirements.txt
python -m playwright install chromium
python main.py
```

## 文件说明

| 文件 | 作用 |
|------|------|
| `main.py` | Flask 服务入口，API 路由，工作线程管理 |
| `engine.py` | 调度引擎，策略决策，状态管理 |
| `executor.py` | Playwright 执行层，浏览器自动化 |
| `ai_solver.py` | DeepSeek API 调用，题型适配提示词 |
| `question_bank.py` | 题库管理，MD5 匹配，置信度系统 |
| `config.py` | 配置加解密读写 |
| `crypto_utils.py` | 敏感字段 XOR 加密 |
| `templates/index.html` | Web UI |
| `static/styles.css` | 样式（亮色主题，CSS 变量驱动） |
| `static/app.js` | 前端逻辑 |

## 置信度系统

| 来源 | 置信度 | 说明 |
|------|--------|------|
| submitAnswer | 100 | 平台判分，不可覆盖 |
| deepseek-v4-pro | 80 | AI 推理模型 |
| deepseek-reasoner | 65 | R1 推理 |
| deepseek-chat | 40 | V3 快速 |
| pending | 0 | 待定 |

低置信度答案不会覆盖高置信度，平台正确答案永远最高优先级。

## 版本演进

| 版本 | 日期 | 主要变更 |
|------|------|---------|
| v1.1 | 2026-07-09 | Win10/11 exe 打包 + 圆角图标 + 系统兼容性说明 |
| v1.0 | 2026-07-09 | 首版发布：每日练习/图文/视频/模拟考试 + AI答题 + 题库系统 |

## v1.1 更新

- PyInstaller 打包为独立 exe：`dist/OverWall_Win11.exe`，免安装 Python 环境
- 圆角图标：6 尺寸（16~256px），四角透明无白底
- 系统兼容性说明：Win10/11 开箱即用，Win7/8 不再支持
- 添加 `OverWall.spec` 打包配置文件

### XTong 的贡献
- **兼容性测试**：Win7 老电脑实测，发现 API Set DLL 缺失问题
- **图标优化**：指出四角直角白底问题，提出圆角需求

### Claude (AI Assistant) 的贡献
- **打包构建**：PyInstaller 配置 + 多 Python 版本兼容调试（3.8/3.9/3.13）
- **图标处理**：Pillow 圆角遮罩 + 多尺寸 ICO 生成
- **文档更新**：兼容性说明 + 版本演进

## v1.0 更新

- 登录自动化（账号密码 + 加密存储）
- 每日练习：AI 答题 + 平台判分 + 题库缓存，日限 3 分自动停止
- 模拟考试：支持单选/多选/判断/填空/简答，手动选卷，手动签名
- 图文学习：自动点击文章卡片 → 等待倒计时 → 自动去重
- 视频学习：自动点击视频卡片 → 播放 → 等待倒计时
- 自动刷分：按策略优先级循环执行
- 设置覆盖层：账号/策略/API/主题配置
- 置信度系统：平台判分 > V4 Pro > R1 > V3，低置信不覆盖高置信
- 支持 DeepSeek V3 / R1 / V4 Pro 模型切换
- 敏感字段本地 XOR 加密存储
- 多选 checkbox 通过每次重新查询 DOM 解决 layui 重渲染引用失效

### XTong 的贡献
- **产品设计**：需求定义，策略优先级，UI 交互设计
- **测试反馈**：全流程测试，页面结构分析，bug 定位
- **外部协作**：邀请社区专家协助解决 layui 多选和 DeepSeek 推理 token 问题

### Claude (AI Assistant) 的贡献
- **架构设计**：Flask + Playwright + 轮询架构，工作线程管理
- **代码实现**：全模块开发（登录/答题/图文/视频/考试/题库/UI）
- **调试优化**：多选 DOM 定位，AI 提示词优化，token 配置调整

## 免责声明

本工具仅供个人学习和技术研究使用。使用者应自行承担使用风险，包括但不限于：

- 平台账号被封禁或限制
- 因违反平台使用协议产生的法律责任
- AI 答题可能存在错误，不保证答题正确率

**严禁用于任何违反法律法规或平台服务条款的场景。** 作者不对因使用本工具产生的任何后果承担责任。

## 许可证

[MIT License](LICENSE)

## 致谢

- layui 多选 checkbox 渲染问题由 ChatGPT 协助定位解决
- DeepSeek API V4 Pro 推理 token 不足问题由 ChatGPT 协助定位
