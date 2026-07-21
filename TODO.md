# OverWall 待办

> 2026-07-21 记录，2026-07-21 修复完成

## 1. ✅ 图文学习不跳过已学习文章

- **修复**：在 `JS_EXTRACT_CARDS` 中恢复 `is-learned` CSS class 检测，与 courseid 去重并存（双保险）
- **改动**：`executor.py` `_click_article_card`

## 2. ✅ 视频学习不向下滚动加载更多

- **修复**：PC 端 `scrollTop` 替换为 TouchEvent 触屏滑动模拟 + `scrollIntoView` 兜底
- **改动**：`executor.py` `_click_article_card` 滚动加载循环

## 3. ✅ 视频学习缺少更多导航 tab

- **实测发现**：平台积分页有 6 个任务项（推荐/集团课程/岗位课程/案例学习/每日练习/培训），没有"事故案例"
- **修复**：回退链增加「岗位课程」(tab=3)：集团课程 → 岗位课程 → 案例学习
- **改动**：`executor.py` `study_videos`、`engine.py` `_run_videos`、`main.py` 视频模块

## 4. ✅ 每日练习重置计数按钮优化

- **修复**：`<a>` 改为 `<button class="btn btn-ghost btn-sm">`，添加 `title` tooltip，移除 `e.preventDefault()`
- **改动**：`templates/index.html`、`static/app.js`

## 5. ✅ 集团课程二级/三级标签导航

- **实测发现**：视频课程下三级标签为 `.study-cats__item`：「全部」「安全警示视频」「交通违法微视频」；专业课程下无三级标签
- **修复**：新增 `_get_third_level_tags` / `_click_third_level_tag` 方法，`_do_study_loop` 增加三级标签遍历逻辑
- **改动**：`executor.py`
