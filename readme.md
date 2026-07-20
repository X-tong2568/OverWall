# OverWall - 你有张良计 我有过墙梯

> v1.5.1 — 某企业安全积分自动化脚本

## 功能

| 模块 | 说明 |
|------|------|
| 每日练习 | AI 答题 + 本地题库缓存，日限 3 分自动停止 |
| 模拟考试 | 手动触发，支持单选/多选/判断/填空/简答，需手动签名 |
| 图文学习 | 推荐页自动点击 → 跳过已学习 → 无新文时回退专业课程 |
| 视频学习 | 集团课程(视频课程) → 案例学习，纯视频内容 |
| 自动刷分 | 按策略优先级自动循环执行，推荐刷完自动回退 |

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

前往 [Releases](https://github.com/X-tong2568/OverWall/releases) 下载最新 `OverWall_Win11.exe`，双击运行，浏览器自动打开 `http://127.0.0.1:15888`

### 方式二：源码运行

源码运行无需手动安装浏览器，首次使用会自动下载 Chromium 到系统缓存目录：

```bash
pip install -r requirements.txt
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
| `build.bat` | PyInstaller 一键打包脚本 |
| `CHANGELOG.md` | 版本更新详情 |
| `daily_state.json` | 每日练习本地日计数（跨天自动清零） |
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
| v1.5.1 | 2026-07-20 | PyInstaller 打包浏览器路径修复（executable_path 直连 + PLAYWRIGHT_BROWSERS_PATH 预置） |
| v1.5 | 2026-07-20 | 超时10h + 提交按钮修复 + 视频courseid去重 + 每日练习本地日计数 |
| v1.4 | 2026-07-14 | 推荐页已学习过滤 + 集团课程专业课程 + 推荐回退专业课程 + 子tab优先级配置 |
| v1.3 | 2026-07-11 | Edge 浏览器回退 + 自动下载 Chromium + 打包依赖修复 |
| v1.2 | 2026-07-09 | 双栏仪表盘 UI 重构 + 无头模式修复 + 手动模块状态同步 + 关于弹窗 |
| v1.1 | 2026-07-09 | Win10/11 exe 打包 + 圆角图标 + 系统兼容性说明 |
| v1.0 | 2026-07-09 | 首版发布：每日练习/图文/视频/模拟考试 + AI答题 + 题库系统 |

完整更新记录见 [CHANGELOG.md](CHANGELOG.md)

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
