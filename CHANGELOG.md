# Changelog

## v1.8.0 (2026-11-16)

- **平台新版学习中心适配**：平台把「推荐 / 集团课程 / 单位课程 / 案例学习」重写为统一的 `user-study/index.html?tab=1/2/3/4` 页面，旧卡片点击和触屏懒加载全部失效
  - 新增页面内接口取列表：推荐 `getStudyInfo`、集团/单位/事故案例 `getSourceCourse`，按 `is_learned / isLearned` 跳过已学，支持分页翻页
  - 仍然通过真实详情页学习：推荐 `detail-command.html`、课程 `courseDetail.html`，走平台自己的播放、倒计时、保存进度接口
  - 视频页自动点播放，PDF/图片/音频由平台自动计时；完成判断改为读 `.text-dura / .rest-time / .head-dura`
  - 保留内容 ID 全局去重、分类回退链、随机节流，降低触发风控的概率
  - 入口与卡片优先按真人路径点击：积分页 → 去学习 → 学习中心 tab → 分类 → 卡片；翻页后不在当前列表的卡片才回退 URL
  - 某分类没有未学内容时明确提示并结束，不再每 15 秒空转重试
- **每日练习适配**：平台把「刷新」按钮改名为「下一题」（`.ue-refresh-btn`），新增简答题（`.ue-essay-input`），错题标记改为 `.ue-option-correct / .ue-option-wrong`
  - 提交/下一题/简答题全部适配；填空题判定改为按接口 `wfAnswer` 判对错，不再乐观计分
  - 修复题干误抓页面顶部 `.title`（「每日练习」）导致 AI 拿到空题的问题
- **登录适配**：新版登录页账号框 `name=userCode`，登录必须点 `#tgit-app-login-btn-sub`，按 Enter 不再提交
- **中途停止修复**：停止任务时浏览器已关闭，但学习循环仍在访问页面，出现 `NoneType ... evaluate` 报错；手动模块停止后还会空等 15 秒，导致页面提示「上一个任务还在执行中」。现在手动模块使用局部 executor 引用、停止后立即 break，学习等待检测到页面关闭直接返回，实测停止后 worker 约 1 秒释放
- **AI 答题关键修复**：`query_ai` 请求体漏传 `messages` 字段，DeepSeek 返回 422，异常被吞后长期表现为「AI 返回空 → 默认选 A」。补上后单选/多选/判断/填空/简答均实测可用
- **实测验证**：完成推荐文章 58 秒计时并保存、每日练习 3 分（单选/判断题，AI 作答 + 平台判分 + 下一题）、单位课程详情页加载与已学判断、AI 四种题型调用

## v1.7.1 (2026-08-13)

- **题库内容锚定修复（选项乱序答错）**：平台会打乱同一道题的选项顺序，旧实现把字母答案按位置存库、匹配失败时静默按位置猜，导致乱序后「只会选之前的选项」、错误率飙升
  - `question_bank.py` 新增 `split_option_text()` 解析多层前缀（我们加的字母 + 平台图标字体 + 平台显示字母 + 内容），`letters_to_content()` 字母转内容
  - `record_answer` 入库前把字母答案锚定为选项纯内容；`lookup_question` 重写为内容匹配，**匹配不到返回 None 让 AI 现解，绝不按位置瞎猜**（删除位置回退）
  - 旧题库数据无需迁移，lookup 自动兼容（字母答案用存储时的选项列表还原内容）
- **提交判定内容化**：每日练习提交后按「内容对内容」判定对错，平台 `trueAnswer` 的字母坐标系未知，优先按平台显示字母还原内容，其次按位置字母
- **选项提取纯净化**：`_extract_options` / `_extract_options_for_question` 拆出平台显示字母 + 纯内容，喂给 AI 的选项文本不再含图标/双层标签，顺带提升 AI 准确率
- **AI token 预算提高**：单选 100→500、多选 800→1200、填空 800→1200、简答 1500→2000。thinking 模型的思考 token 也计入 max_tokens，预算太小会把答案截断导致空响应/半截答案
- **验证**：真实题库 42 条乱序回归 40/40 通过（2 条填空题内容答案跳过）+ 10 项单元测试通过

## v1.7.0 (2026-08-13)

- **AI 多服务商支持**：不再只支持 DeepSeek，改为 OpenAI 兼容协议一套代码覆盖 DeepSeek / OpenAI / Kimi（月之暗面）/ 通义千问（DashScope）/ 智谱 GLM / 硅基流动 + 自定义地址。设置页新增服务商下拉，切换自动填充默认 API 地址
- **模型自动检测**：设置页新增「检测模型」按钮，调用服务商 `/models` 接口拉取可用模型列表（双候选路径兼容各家差异），用户从检测结果自选或手动输入，不再硬编码预设模型
- **配置自动迁移**：旧 `deepseek_*` 配置键自动迁移为通用 `ai_provider / ai_key / ai_base_url / ai_model`，保存后旧键消失，老用户无感升级
- **DeepSeek 专属参数门控**：thinking / reasoning_effort 仅 DeepSeek 服务商发送（其他家会 400），`reasoning_content` 兜底逻辑对所有服务商通用保留
- **URL 规范化**：新增 `normalize_api_base()`，兼容 base 地址自带版本段的服务商（DashScope `compatible-mode/v1`、智谱 `paas/v4`）
- **服务端模型探测接口**：新增 `POST /api/models`，服务端代理请求避免浏览器 CORS，错误文案中文透传

## v1.6.4 (2026-07-25)

- **每日练习刷新修复**：`_solve_multiple_choice` 提交答案后无条件点击刷新按钮获取下一题，不再依赖平台自动跳转（平台已取消答对自动刷新行为）。与填空题 `_solve_fill_blank` 行为对齐。

## v1.6.3 (2026-07-25)

- **模拟考试诊断日志补全**：`do_mock_exam` 答题循环新增题干为空 / API Key 为空的诊断警告，与每日练习 `_solve_multiple_choice` 的诊断日志保持一致
- **API 调用路径对齐**：模拟考试和每日练习的 DeepSeek 调用统一使用 `query_deepseek` 同一路径

## v1.6.2 (2026-07-25)

- **DeepSeek 旧模型移除**：`deepseek-chat` / `deepseek-reasoner` 已于 2026-07-24 被 DeepSeek 官方停用，所有引用替换为 `deepseek-v4-flash`（新默认）和 `deepseek-v4-pro`
- **新增 V4 Flash 模型**：下拉选项加入 `deepseek-v4-flash`，置信度 45，定价 ¥1/¥2/¥0.02（输入/输出/缓存，每百万 Token）
- **定价表更新**：前端定价表、README 置信度表与实际 API 价格对齐
- **全量兜底替换**：`config.py`、`executor.py`、`ai_solver.py`、`static/app.js`、`question_bank.py` 共 7 处 `deepseek-chat` 默认值/兜底值全部替换

## v1.6.1 (2026-07-24)

- **答题只选 A 修复**：`query_deepseek` 的 `reasoning_content` 提取逻辑从仅多选扩展到单选+多选，V4/Pro 模型开启 thinking 后 `content` 为空时也能从推理文本中捞出答案字母
- **seen_ids 变量名修复**：`_click_article_card` 第 622 行 `seen_ids` → `self._seen_courseids`，修复 v1.6.0 重构遗漏导致的 NameError 崩溃
- **选择器兜底增强**：`_extract_options` 选项提取新增 `.option-item, .answer-item, li[class*="option"]` 兜底，题干提取新增 `h3, h4, .title, .subject-text` 等选择器，防止平台改版后取不到题目
- **诊断日志**：`_solve_multiple_choice` 新增题干为空 / API Key 为空时的明确警告

## v1.6.0 (2026-07-21)

- **前端分类按钮**：通用"刷图文/刷视频"拆分为 6 个具体分类按钮（图文推荐/专业课程、视频集团/单位/案例学习），点击直达对应分类
- **is-learned 已学跳过**：恢复 v1.4 的 `is-learned` CSS class 检测，与 courseid 去重双保险，日志 `[L]` 标记平台已学卡片
- **触屏滑动懒加载**：`TouchEvent` 触屏滑动模拟 + `scrollIntoView` 兜底，解决移动端页面 PC 滚动不触发懒加载
- **三级标签遍历**：`_get_third_level_tags` / `_click_third_level_tag` 自动发现并切换视频课程下的分类标签（安全警示视频、交通违法微视频等）
- **实例级 courseid 全局去重**：`self._seen_courseids` 跨方法/跨轮次持久，同一进程内所有分类共享去重
- **视频播放按钮增强**：基于实测 DOM（`.wechat-play-button` / `#myVideo`）重写 3 层兜底，`force=True` 绕过遮挡
- **单位课程**：实测确认平台名称为"单位课程"并修正匹配，`asyncio` 导入缺失修复
- **去事故案例重复**：实测事故案例与案例学习(tab=4) 100% courseid 重叠，合并为案例学习

## v1.5.1 (2026-07-20)

- **PyInstaller 打包浏览器路径修复**：`start_browser()` 新增 `executable_path` 直连策略绕过 Playwright channel 解析，`_ensure_playwright_browsers()` 在检测系统浏览器之前预置 `PLAYWRIGHT_BROWSERS_PATH` 防止回退到临时目录 `_MEI*`，`_find_system_browser()` 改为返回完整可执行文件路径
- **根因**：v1.4 新增 `_find_system_browser()` 检测到系统 Edge 后直接 return，跳过了 v1.3 原有的 `PLAYWRIGHT_BROWSERS_PATH` 设置，导致 channel 启动失败后回退到 `p.chromium.launch()` 时路径解析到 PyInstaller 临时目录

## v1.5 (2026-07-20)

- **超时 1h → 10h**：`_wait_study_done` 默认超时 36000 秒，适配长时长课程
- **每日练习提交按钮修复**：`_solve_multiple_choice` 删除 for 循环内阻断的 `return`，提交按钮恢复正常点击
- **视频模式 courseid 去重**：`_click_article_card` 重写，改用 `courseid` 属性做主去重键，替代不可靠的标题去重和无效的 `is-learned` class 检测。实测分析真实 DOM 结构后精确匹配
- **每日练习本地日计数**：`daily_state.json` 按日期记录当日得分，跨天自动清零，替代原来读平台周累计 `#dtStudyWeek` 的错误逻辑（周一 3 分后周二起永远跳过）
- **每日练习重置按钮**：前端新增「重置计数」链接 + `/api/reset_daily_exercise` 端点，手动容错

## v1.4 (2026-07-14)

- **推荐页已学习过滤**：检测 `is-learned` class 和 `course-card__learned-badge`，跳过已学习文章，不再重复点击
- **推荐回退专业课程**：推荐页全部已学习时自动回退到集团课程-专业课程(图文)，article 策略统一管理所有图文内容
- **视频策略独立**：`study_videos` 只刷集团课程-视频课程 → 案例学习，专业课程图文归 article 策略管，不再混入视频模块
- **通用刷课循环**：`_do_study_loop` + `_click_subtab` 提取为通用方法，支持任意页面+子tab组合
- **DOM 结构验证**：完整分析平台三级导航结构（一级tab/二级tab/三级tab + 课程卡片），所有选择器经过干跑测试验证

## v1.3 (2026-07-11)

- **浏览器多通道回退**：启动顺序 系统 Edge → 系统 Chrome → 自带 Chromium，Win10/11 自带 Edge 开箱即用
- **自动下载 Chromium**：无可用浏览器时自动从 Playwright CDN 下载（约 145MB），存到 exe 同目录持久化，仅首次需要
- **无头模式全支持**：Chrome / Edge / Chromium 均支持无头运行
- **打包依赖修复**：添加 playwright hidden imports，内置 PyInstaller hook 正确收集 driver 文件
- **启动预检查**：程序启动时提前检查/下载浏览器，避免登录时才等待
- **构建脚本**：`build.bat` 一键安装依赖 + 打包
- **v1.3 补丁 (2026-07-13)**：修复日志卡片末行截断、浏览器检测优化（有 Edge 跳过 Chromium 下载）、图文页 networkidle 超时兜底、手动模块改为循环执行直到停止

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

