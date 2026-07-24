# Changelog

## v1.6.1 (2026-07-24)

- **答题只选 A 修复**：`query_deepseek` 的 `reasoning_content` 提取逻辑从仅多选扩展到单选+多选，V4/Pro 模型开启 thinking 后 `content` 为空时也能从推理文本中捞出答案字母
- **seen_ids 变量名修复**：`_click_article_card` 第 622 行 `seen_ids` → `self._seen_courseids`，修复 v1.6.0 重构遗漏导致的 NameError 崩溃
- **选择器兜底增强**：`_extract_options` 选项提取新增 `.option-item, .answer-item, li[class*="option"]` 兜底，题干提取新增 `h3, h4, .title, .subject-text` 等选择器，防止平台改版后取不到题目
- **诊断日志**：`_solve_multiple_choice` 新增题干为空 / API Key 为空时的明确警告

### Claude (AI Assistant) 的贡献
- **Bug 排查**：追踪整条答题链路定位两层故障（reasoning_content 单选丢失 + 变量名遗漏）
- **代码修复**：ai_solver.py reasoning_content 单选提取、executor.py seen_ids 修复 + 选择器兜底 + 诊断日志
- **文档更新**：README/CHANGELOG/关于弹窗/启动横幅同步 v1.6.1

## v1.6.0 (2026-07-21)

- **前端分类按钮**：通用"刷图文/刷视频"拆分为 6 个具体分类按钮（图文推荐/专业课程、视频集团/单位/案例学习），点击直达对应分类
- **is-learned 已学跳过**：恢复 v1.4 的 `is-learned` CSS class 检测，与 courseid 去重双保险，日志 `[L]` 标记平台已学卡片
- **触屏滑动懒加载**：`TouchEvent` 触屏滑动模拟 + `scrollIntoView` 兜底，解决移动端页面 PC 滚动不触发懒加载
- **三级标签遍历**：`_get_third_level_tags` / `_click_third_level_tag` 自动发现并切换视频课程下的分类标签（安全警示视频、交通违法微视频等）
- **实例级 courseid 全局去重**：`self._seen_courseids` 跨方法/跨轮次持久，同一进程内所有分类共享去重
- **视频播放按钮增强**：基于实测 DOM（`.wechat-play-button` / `#myVideo`）重写 3 层兜底，`force=True` 绕过遮挡
- **单位课程**：实测确认平台名称为"单位课程"并修正匹配，`asyncio` 导入缺失修复
- **去事故案例重复**：实测事故案例与案例学习(tab=4) 100% courseid 重叠，合并为案例学习

### Claude (AI Assistant) 的贡献
- **5 项 Bug 修复**：TODO.md 全清，涉及 is-learned、触屏滚动、分类导航、三级标签、播放按钮
- **实测驱动开发**：多次运行脚本登录平台 dump 真实 DOM，精确定位选择器和数据结构
- **架构改进**：实例级去重、分类按钮化、回退链优化
- **文档更新**：TODO.md 完结、CHANGELOG/关于弹窗同步 v1.6.0

## v1.5.1 (2026-07-20)

- **PyInstaller 打包浏览器路径修复**：`start_browser()` 新增 `executable_path` 直连策略绕过 Playwright channel 解析，`_ensure_playwright_browsers()` 在检测系统浏览器之前预置 `PLAYWRIGHT_BROWSERS_PATH` 防止回退到临时目录 `_MEI*`，`_find_system_browser()` 改为返回完整可执行文件路径
- **根因**：v1.4 新增 `_find_system_browser()` 检测到系统 Edge 后直接 return，跳过了 v1.3 原有的 `PLAYWRIGHT_BROWSERS_PATH` 设置，导致 channel 启动失败后回退到 `p.chromium.launch()` 时路径解析到 PyInstaller 临时目录

### Claude (AI Assistant) 的贡献
- **Bug 修复**：诊断 PyInstaller 临时目录路径问题，三函数重构（`_find_system_browser` 返回值扩展、`_ensure_playwright_browsers` 环境变量预置、`start_browser` 三策略启动链）
- **文档更新**：README/CHANGELOG/关于弹窗版本号同步到 v1.5.1

## v1.5 (2026-07-20)

- **超时 1h → 10h**：`_wait_study_done` 默认超时 36000 秒，适配长时长课程
- **每日练习提交按钮修复**：`_solve_multiple_choice` 删除 for 循环内阻断的 `return`，提交按钮恢复正常点击
- **视频模式 courseid 去重**：`_click_article_card` 重写，改用 `courseid` 属性做主去重键，替代不可靠的标题去重和无效的 `is-learned` class 检测。实测分析真实 DOM 结构后精确匹配
- **每日练习本地日计数**：`daily_state.json` 按日期记录当日得分，跨天自动清零，替代原来读平台周累计 `#dtStudyWeek` 的错误逻辑（周一 3 分后周二起永远跳过）
- **每日练习重置按钮**：前端新增「重置计数」链接 + `/api/reset_daily_exercise` 端点，手动容错

### Claude (AI Assistant) 的贡献
- **Bug 修复**：`_solve_multiple_choice` 提交按钮阻断、`_wait_study_done` 超时 3600→36000
- **代码重构**：`_click_article_card` courseid 去重重写、每日练习本地日计数系统（6 个新方法）
- **诊断分析**：Playwright 直连平台页面提取真实 DOM 结构，定位 `li.course-item` + `courseid` 属性
- **文档更新**：README v1.5 章节、版本演进表、关于弹窗版本号同步

## v1.4 (2026-07-14)

- **推荐页已学习过滤**：检测 `is-learned` class 和 `course-card__learned-badge`，跳过已学习文章，不再重复点击
- **推荐回退专业课程**：推荐页全部已学习时自动回退到集团课程-专业课程(图文)，article 策略统一管理所有图文内容
- **视频策略独立**：`study_videos` 只刷集团课程-视频课程 → 案例学习，专业课程图文归 article 策略管，不再混入视频模块
- **通用刷课循环**：`_do_study_loop` + `_click_subtab` 提取为通用方法，支持任意页面+子tab组合
- **DOM 结构验证**：完整分析平台三级导航结构（一级tab/二级tab/三级tab + 课程卡片），所有选择器经过干跑测试验证

### Claude (AI Assistant) 的贡献
- **代码实现**：`_click_article_card` is-learned 过滤、`study_videos` 重构 + `_do_study_loop` + `_click_subtab`、`study_articles` 推荐回退专业课程
- **配置系统**：`jituan_priority` 配置项 + 设置UI + 加载/保存/重置

## v1.3 (2026-07-11)

- **浏览器多通道回退**：启动顺序 系统 Edge → 系统 Chrome → 自带 Chromium，Win10/11 自带 Edge 开箱即用
- **自动下载 Chromium**：无可用浏览器时自动从 Playwright CDN 下载（约 145MB），存到 exe 同目录持久化，仅首次需要
- **无头模式全支持**：Chrome / Edge / Chromium 均支持无头运行
- **打包依赖修复**：添加 playwright hidden imports，内置 PyInstaller hook 正确收集 driver 文件
- **启动预检查**：程序启动时提前检查/下载浏览器，避免登录时才等待
- **构建脚本**：`build.bat` 一键安装依赖 + 打包
- **v1.3 补丁 (2026-07-13)**：修复日志卡片末行截断、浏览器检测优化（有 Edge 跳过 Chromium 下载）、图文页 networkidle 超时兜底、手动模块改为循环执行直到停止

### Claude (AI Assistant) 的贡献
- **代码实现**：executor.py 多通道回退 + `_ensure_playwright_browsers()` 自动下载函数
- **打包修复**：OverWall.spec hidden imports + build.bat 构建脚本
- **文档更新**：README v1.3 章节，模板版本号同步

## v1.2 (2026-07-09)

- **双栏仪表盘 UI**：全屏填满布局，左栏（积分+控制+日志），右栏（运行状态+题库管理），响应式双断点
- **设置页面重排**：双栏卡片布局，与主界面一致风格，底部 sticky 保存栏
- **无头模式修复**：手动模块（图文/视频/每日练习）加 `start_browser()` 重连检查，修复 browser closed 报错
- **无头模式模拟考试禁用**：按钮自动置灰 + tooltip + 后端拦截，三层兜底
- **仪表盘指标同步**：手动模块完成后更新 `_status_snapshot`，5 项统计实时刷新
- **题库数量修复**：导入/清空后同步更新快照，解决轮询覆盖为 0
- **停止/登录竞态修复**：worker 线程异步清理浏览器，`_logged_in` 双重判断，前端登录响应处理
- **题库查看面板**：自适应高度撑满右栏，内部滚动不撑页面
- **关于弹窗**：footer 点击"关于"弹窗查看版本、功能、技术栈信息
- **网页图标**：`favicon.ico` 生效

### XTong 的贡献
- **测试反馈**：发现无头模式手动按钮报错、仪表盘指标不更新、停止后无法登录、题库查看窗口溢出等 bug
- **UI 设计**：提出全屏仪表盘布局需求、模拟考试无头模式禁用交互、关于弹窗需求

### Claude (AI Assistant) 的贡献
- **UI 重构**：单列窄卡片 → 双栏仪表盘，CSS Grid 布局，设置页面重排
- **Bug 修复**：无头模式 browser closed、题库数量轮询覆盖、停止登录竞态、仪表盘指标不更新
- **代码实现**：`start_browser()` 重连、`_status_snapshot` 状态同步、三层无头拦截
- **文档更新**：记忆库新增 1 条，README v1.2 章节

## v1.1 (2026-07-09)

- Win10/11 exe 打包 + 圆角图标 + 系统兼容性说明

## v1.0 (2026-07-09)

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

### Claude (AI Assistant) 的贡献
- **架构设计**：Flask + Playwright + 轮询架构，工作线程管理
- **代码实现**：全模块开发（登录/答题/图文/视频/考试/题库/UI）
- **调试优化**：多选 DOM 定位，AI 提示词优化，token 配置调整
