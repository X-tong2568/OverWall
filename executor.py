"""
Playwright 执行层 —— 浏览器自动化：登录、刷图文、刷视频、答题
"""
import asyncio
import json
import os
import re
import sys
import time
from playwright.async_api import async_playwright, Page, Browser

from question_bank import lookup_question, record_answer
from ai_solver import query_deepseek, query_deepseek_fill, extract_correct_from_response

# PyInstaller 打包后数据存到 exe 同目录，避免写入临时文件夹
if getattr(sys, 'frozen', False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DEBUG_DIR = os.path.join(_BASE_DIR, "debug_screenshots")


class TaskExecutor:
    """自动化任务执行器"""

    def __init__(self, config: dict, log_callback=None):
        self.cfg = config
        self.log = log_callback or (lambda lvl, msg: None)
        self.browser: Browser | None = None
        self.page: Page | None = None
        self._running = False
        self._login_done = False

    # ---- 日志 ----
    async def _info(self, msg: str):
        await self.log("info", msg)

    async def _warn(self, msg: str):
        await self.log("warn", msg)

    async def _error(self, msg: str):
        await self.log("error", msg)

    # ---- 调试截图 + AI 分析 ----
    async def _screenshot(self, name: str):
        if not self.page:
            return
        try:
            os.makedirs(DEBUG_DIR, exist_ok=True)
            path = os.path.join(DEBUG_DIR, f"{name}_{int(time.time())}.png")
            await self.page.screenshot(path=path, full_page=True)
            await self._info(f"截图: {path}")
        except Exception:
            pass

    async def _screenshot_and_analyze(self, name: str, prompt: str = ""):
        """截图并用 Ollama 分析页面内容"""
        if not self.page:
            return
        try:
            os.makedirs(DEBUG_DIR, exist_ok=True)
            path = os.path.join(DEBUG_DIR, f"{name}_{int(time.time())}.png")
            await self.page.screenshot(path=path, full_page=False)
            await self._info(f"截图+分析: {name}")

            if not prompt:
                prompt = "列出页面所有可见按钮、链接、可点击元素的文字和CSS类名。描述页面布局。"

            import subprocess
            result = subprocess.run(
                [r"E:\python3.13 install\python.exe",
                 r"C:\Users\As\.claude\skills\local-multimodal\vision.py",
                 path, "--prompt", prompt],
                capture_output=True, text=True, timeout=60
            )
            analysis = result.stdout.strip() or result.stderr.strip()
            # 只输出前500字
            if analysis:
                lines = analysis.split("\n")
                for line in lines[:15]:
                    if line.strip():
                        await self._info(f"  [AI分析] {line.strip()[:120]}")
        except Exception as e:
            await self._warn(f"截图分析失败: {e}")

    # ---- 浏览器生命周期 ----
    async def start_browser(self) -> Page | None:
        """启动浏览器（支持视频编码）"""
        try:
            if self.browser and self.browser.is_connected():
                if not self.page or getattr(self.page, '_closed', True):
                    contexts = self.browser.contexts
                    if contexts:
                        pages = contexts[0].pages
                        if pages:
                            self.page = pages[0]
                        else:
                            self.page = await contexts[0].new_page()
                    else:
                        ctx = await self.browser.new_context(viewport={"width": 1366, "height": 768})
                        self.page = await ctx.new_page()
                await self._info("浏览器已复用")
                return self.page

            p = await async_playwright().start()
            # 优先用系统 Chrome（支持 H.264 视频），没有则用自带 Chromium
            launch_opts = {
                "headless": self.cfg.get("headless", False),
                "slow_mo": self.cfg.get("browser_slow_mo", 300),
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--autoplay-policy=no-user-gesture-required",
                    "--mute-audio",
                ],
            }
            try:
                self.browser = await p.chromium.launch(channel="chrome", **launch_opts)
                await self._info("使用系统 Chrome 浏览器")
            except Exception:
                self.browser = await p.chromium.launch(**launch_opts)
                await self._info("使用自带 Chromium（视频可能无法播放，但计时仍有效）")
            context = await self.browser.new_context(viewport={"width": 1366, "height": 768})
            self.page = await context.new_page()
            return self.page
        except Exception as e:
            await self._error(f"浏览器启动失败: {e}")
            return None

    async def stop_browser(self):
        if self.browser:
            await self.browser.close()
            self.browser = None
            self.page = None

    # ---- 登录 ----
    async def login(self) -> bool:
        """全自动登录：直接到登录页 → 填表 → 等跳转"""
        base = self.cfg.get("base_url", "").rstrip("/")
        login_url = f"{base}/src/apps/app-aqhb/src/portal/login/login.html"

        # 已登录过则跳过
        if getattr(self, '_login_done', False):
            await self._info("已登录，跳过")
            return True

        username = self.cfg.get("username", "")
        password = self.cfg.get("password", "")
        if not username or not password:
            await self._info("未配置账号密码，请在设置中填写后重试")
            return False

        await self._info(f"正在登录: {username}...")
        await self.page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)

        # 如果自动跳转离开了登录页 = 已有有效会话
        if "login" not in self.page.url:
            await self._info("已有有效会话，自动登录！")
            self._login_done = True
            return True

        # 填表
        try:
            uname = self.page.locator('input[type="text"]').first
            pwd = self.page.locator('input[type="password"]').first
            await uname.click()
            await uname.fill(username)
            await pwd.click()
            await pwd.fill(password)
            await self._info(f"已填入 {username}，提交...")

            # 优先按键 Enter
            await pwd.press("Enter")
            await asyncio.sleep(1)

            # Enter 无效则点按钮
            if "login" in self.page.url:
                for sel in ['button:has-text("登")', 'button[type="submit"]', 'button']:
                    btn = self.page.locator(sel).first
                    if await btn.count() > 0 and await btn.is_visible():
                        await btn.click()
                        await asyncio.sleep(1)
                        break
        except Exception as e:
            await self._warn(f"填表异常: {e}")

        # 等跳转
        for i in range(30):
            await asyncio.sleep(1)
            url = self.page.url
            if "login" not in url or "index.html" in url:
                await self._info("登录成功！")
                self._login_done = True
                return True

        await self._error("登录超时，检查账号密码")
        return False

    # ---- 通用 ----
    async def _wait_content(self):
        await self.page.wait_for_load_state("networkidle", timeout=10000)
        await asyncio.sleep(2)

    async def _dump_page(self, tag: str):
        """转储页面元素（含 HTML 结构）"""
        if not self.page:
            return
        await self._info(f"[{tag}] === 页面转储 ===")
        try:
            # body 文本
            body = (await self.page.text_content("body") or "").strip()[:300].replace("\n", " ")
            await self._info(f"[{tag}] body: {body}")

            # iframe 检测
            frames = self.page.frames
            await self._info(f"[{tag}] frame总数: {len(frames)}")
            for fi, f in enumerate(frames):
                if fi > 0:
                    await self._info(f"[{tag}] frame{fi} url: {f.url[:120]}")

            # 按钮
            btns = self.page.locator("button")
            n = await btns.count()
            if n > 0:
                texts = [(await btns.nth(j).text_content() or "").strip()[:30] for j in range(min(n, 10))]
                await self._info(f"[{tag}] buttons({n}): {texts}")

            # 用 JS 提取页面中所有 class 含有关键词的元素的 outerHTML（前200字）
            html_sample = await self.page.evaluate("""() => {
                const keywords = ['study', 'learn', 'score', 'course', 'article', 'exercise', 'exam', 'question', 'option', 'radio', 'choice', 'content', 'list', 'item', 'card'];
                const results = [];
                for (const kw of keywords) {
                    const els = document.querySelectorAll('[class*="' + kw + '" i], [id*="' + kw + '" i]');
                    for (const el of els) {
                        if (results.length >= 5) break;
                        const html = el.outerHTML || '';
                        if (html.length > 10) {
                            results.push(html.substring(0, 200));
                        }
                    }
                    if (results.length >= 5) break;
                }
                return results;
            }""")
            if html_sample:
                for idx, sample in enumerate(html_sample[:5]):
                    await self._info(f"[{tag}] HTML片段{idx}: {sample[:200]}")
        except Exception as e:
            await self._warn(f"[{tag}] dump异常: {e}")

    async def _click_btn_by_text(self, text: str) -> bool:
        """点击包含指定文字的按钮"""
        btn = self.page.locator(f'button:has-text("{text}")').first
        if await btn.count() > 0 and await btn.is_visible():
            await btn.click()
            await asyncio.sleep(0.5)
            return True
        return False

    # ---- 从积分页进入学习 ----
    async def _goto_study_from_score(self, tab_name: str) -> bool:
        """从积分页点击对应模块的'去学习'按钮进入"""
        base = self.cfg.get("base_url", "").rstrip("/")
        score_url = f"{base}/src/apps/app-aqhb/src/components/studentWeb/user-score/index.html?type=week"

        await self._info(f"  导航到积分页...")
        await self.page.goto(score_url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2)

        # 读取本周积分并推送到状态
        try:
            pts_el = self.page.locator("#weekLearningPoints")
            if await pts_el.count() > 0:
                pts = int((await pts_el.text_content() or "0").strip())
                await self.log("status", {"weekly_points": pts})
                await self._info(f"  本周积分: {pts}")
        except Exception:
            pass

        try:
            items = self.page.locator('li.task-item')
            cnt = await items.count()
            await self._info(f"  找到 {cnt} 个任务项")

            for i in range(cnt):
                item = items.nth(i)
                name_el = item.locator('.task-item__name')
                if await name_el.count() > 0:
                    name = (await name_el.text_content() or "").strip()
                    btn = item.locator('button:has-text("去学习")')
                    has_btn = await btn.count() > 0
                    await self._info(f"    [{i}] {name} {'[有按钮]' if has_btn else ''}")
                    if tab_name in name and has_btn:
                        await self._info(f"  → 点击: {name}")
                        await btn.click()
                        await asyncio.sleep(3)
                        await self.page.evaluate("window.scrollTo(0, 500)")
                        await asyncio.sleep(1)
                        await self._wait_content()
                        await asyncio.sleep(2)
                        return True
            await self._warn(f"  在{ cnt }个任务项中未找到'{tab_name}'")
        except Exception as e:
            await self._warn(f"  从积分页进入失败: {e}")
        return False

    # ---- 刷图文 ----
    async def study_articles(self, count: int = 5) -> int:
        """刷图文学习：从积分页进入推荐"""
        points = 0
        await self._info("→ 图文: 推荐页")

        # 从积分页进入
        if not await self._goto_study_from_score("推荐"):
            await self._warn("图文: 无法从积分页进入")
            return 0

        await self._dump_page("article")
        await self._screenshot("article_page")

        seen = set()
        for i in range(count):
            if not self._running:
                break
            await self._info(f"图文 [{i+1}/{count}]: 点击文章卡片...")
            clicked = await self._click_article_card(seen)
            if not clicked:
                await self._warn("图文: 未找到可点击的文章")
                await self._screenshot("article_no_card")
                break
            await self._wait_content()
            await self._info("  等待学习倒计时...")
            done = await self._wait_study_done()
            if not self._running:
                break
            if done:
                points += 1
                await self._info(f"  ✓ 图文完成 +1 (累计{points})")
            else:
                await self._warn("  等待超时")
            if not self._running:
                break
            if not await self._goto_study_from_score("推荐"):
                break

        await self._info(f"图文结束: +{points}分")
        return points

    async def _click_article_card(self, seen_titles: set = None) -> bool:
        """找到并点击课程卡片（滚动加载更多 + 去重）"""
        if seen_titles is None:
            seen_titles = set()
        try:
            # 滚动直到找到未看过的卡片
            for attempt in range(10):
                # 检查是否有未看过的卡片
                first_unseen = await self.page.evaluate("""(seen) => {
                    const cards = document.querySelectorAll('li.course-card--hero, li.course-card--list, li.course-card--text, li.course-item');
                    for (const card of cards) {
                        const titleEl = card.querySelector('[class*="title"], [class*="_name"]') || card;
                        const title = (titleEl.innerText || '').trim();
                        if (title && !seen.includes(title)) return title;
                    }
                    return '';
                }""", list(seen_titles))

                if first_unseen:
                    break  # 找到了，退出滚动循环

                # 没找到 → 滚动加载更多
                prev = await self.page.evaluate("""
                    () => document.querySelectorAll('li.course-card--hero, li.course-card--list, li.course-card--text, li.course-item').length
                """)
                await self.page.evaluate("""() => {
                    const el = document.querySelector('.study-content, main') || document.scrollingElement || document.body;
                    el.scrollTop = el.scrollHeight;
                    window.scrollTo(0, document.body.scrollHeight);
                }""")
                await asyncio.sleep(1)
                now = await self.page.evaluate("""
                    () => document.querySelectorAll('li.course-card--hero, li.course-card--list, li.course-card--text, li.course-item').length
                """)
                if now == prev:
                    break  # 没新内容了

            # 用 JS 找所有卡片和标题
            cards_info = await self.page.evaluate("""() => {
                const cards = document.querySelectorAll('li.course-card--hero, li.course-card--list, li.course-card--text, li.course-item');
                return Array.from(cards).map((card, i) => {
                    const titleEl = card.querySelector('[class*="title"], [class*="_name"]') || card;
                    return {index: i, title: (titleEl.innerText || titleEl.textContent || '').trim()};
                });
            }""")

            if not cards_info:
                await self._warn("  页面无课程卡片")
                return False

            # 找第一个没看过的
            for ci in cards_info:
                if ci["title"] and ci["title"] not in seen_titles:
                    # 用 Playwright locator 点击（比 JS click 可靠）
                    card = self.page.locator('li.course-card--hero, li.course-card--list, li.course-card--text, li.course-item').nth(ci["index"])
                    if await card.count() > 0:
                        await card.scroll_into_view_if_needed()
                        await asyncio.sleep(0.3)
                        await card.click()
                        await self._info(f"  点击: {ci['title'][:50]}")
                        seen_titles.add(ci["title"])
                        await asyncio.sleep(1)
                        return True

            await self._warn(f"  所有{len(cards_info)}个卡片均已看过")
        except Exception as e:
            await self._warn(f"  点击卡片异常: {e}")
        return False

    # ---- 刷视频 ----
    async def study_videos(self, tab: int = 2, count: int = 5) -> int:
        """刷视频学习：从积分页进入集团课程或案例学习"""
        points = 0
        tab_name = {2: "集团课程", 4: "案例学习"}.get(tab, f"tab{tab}")

        await self._info(f"→ 视频({tab_name})")

        if not await self._goto_study_from_score(tab_name):
            await self._warn(f"视频({tab_name}): 无法从积分页进入")
            return 0

        await self._dump_page(f"video{tab}")
        await self._screenshot(f"video_tab{tab}")

        seen = set()  # 去重
        for i in range(count):
            if not self._running:
                break
            await self._info(f"视频 [{i+1}/{count}]: 点击视频卡片...")
            clicked = await self._click_article_card(seen)
            if not clicked:
                await self._warn(f"视频({tab_name}): 未找到可点击的视频")
                await self._screenshot(f"video{tab}_no_card")
                break
            await self._wait_content()
            await self._click_play()
            await self._info("  等待学习倒计时...")
            done = await self._wait_study_done()
            if not self._running:
                break
            if done:
                points += 1
                await self._info(f"  ✓ 视频完成 +1 (累计{points})")
            else:
                await self._warn("  等待超时")
            if not self._running:
                break
            if not await self._goto_study_from_score(tab_name):
                break

        await self._info(f"视频({tab_name})结束: +{points}分")
        return points

    async def _click_study_btn(self) -> bool:
        """在当前页找'去学习'按钮并点击"""
        for sel in [
            ':text("去学习")',
            'button:has-text("去学习")',
            'a:has-text("去学习")',
            '[class*="go-study"]',
        ]:
            try:
                el = self.page.locator(sel).first
                if await el.count() > 0 and await el.is_visible(timeout=1000):
                    await el.click()
                    await asyncio.sleep(1)
                    return True
            except Exception:
                continue
        return False

    async def _click_play(self):
        """点击视频播放按钮"""
        for sel in [".wechat-play-button", ".fa-play", '[class*="play"]']:
            el = self.page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.click()
                await self._info("  已点击播放")
                return

    async def _wait_study_done(self, timeout: int = 3600) -> bool:
        """等待学习倒计时，显示进度，响应停止信号"""
        for sec in range(timeout):
            if not self._running:
                await self._info("  收到停止信号，中断等待")
                return False
            await asyncio.sleep(1)
            try:
                body = await self.page.text_content("body") or ""
                if "已完成" in body:
                    await self._info(f"  等待 {sec+1}秒后完成")
                    return True
                # 每15秒报告进度
                if (sec + 1) % 15 == 0:
                    # 尝试读取剩余时间
                    rest_text = ""
                    try:
                        rest_el = self.page.locator(".text-dura, .rest-time, [class*='rest']").first
                        if await rest_el.count() > 0:
                            rest_text = " 剩余:" + ((await rest_el.text_content()) or "").strip()
                    except Exception:
                        pass
                    await self._info(f"  学习中... 已等{sec+1}秒{rest_text}")
            except Exception:
                pass
        await self._warn(f"  等待 {timeout}秒 超时")
        return False

    # ---- 每日练习 ----
    async def do_exercises(self, api_key: str = "") -> dict:
        base = self.cfg.get("base_url", "").rstrip("/")
        url = f"{base}/src/apps/app-aqhb/src/components/studentWeb/user-exercise/index.html?tab=2"
        result = {"points": 0, "correct": 0, "wrong": 0}

        await self._info("→ 每日练习")

        # 先检查每日积分是否已满
        daily_limit = await self._check_daily_exercise_limit()
        if daily_limit >= 3:
            await self._info(f"每日练习已获 {daily_limit} 分（已达上限），跳过")
            return result

        await self.page.goto(url, wait_until="networkidle", timeout=30000)
        await self._wait_content()
        await self._dump_page("exercise")
        await self._screenshot("exercise_page")

        # 拦截 submitAnswer（用列表包装避免闭包引用问题）
        submit_holder: list = [None]

        async def on_resp(response):
            if "submitanswer" in response.url.lower():
                try:
                    data = await response.json()
                    submit_holder[0] = data
                    await self._info(f"  [API] submitAnswer: {json.dumps(data, ensure_ascii=False)[:300]}")
                except Exception:
                    pass

        self.page.on("response", on_resp)

        try:
            for i in range(20):
                if not self._running:
                    break
                if result["points"] >= 3:
                    await self._info("今日练习已满3分（本地计数）")
                    break
                # 每5题检查一次score页面
                if i > 0 and i % 5 == 0:
                    dl = await self._check_daily_exercise_limit()
                    if dl >= 3:
                        await self._info(f"每日练习已达上限({dl}分)，停止")
                        break

                submit_holder[0] = None  # 清空上次响应
                await self._info(f"题目 [{i+1}]: 提取题目...")

                # 检查题型
                qtype = ""
                try:
                    type_el = self.page.locator('.ue-question-type')
                    if await type_el.count() > 0:
                        qtype = (await type_el.text_content() or "").strip()
                except Exception:
                    pass

                # 用 JS 从页面提取选项元素
                q_text, opts = await self._extract_options()

                if opts:
                    await self._solve_multiple_choice(q_text, opts, qtype, api_key, submit_holder, result)
                elif "填空" in qtype:
                    await self._solve_fill_blank(q_text, qtype, api_key, submit_holder, result)
                else:
                    await self._info(f"  题型: {qtype}，跳过")
                    refresh_btn = self.page.locator('button:has-text("刷新")').first
                    if await refresh_btn.count() > 0:
                        await refresh_btn.click()
                        await asyncio.sleep(2)
                        continue

                # 已在上方 if/elif 分支中处理，这里不需要额外代码

        finally:
            self.page.remove_listener("response", on_resp)

        await self._info(f"每日练习: 对{result['correct']} 错{result['wrong']} 得{result['points']}分")
        return result

    async def _extract_options(self) -> tuple[str, list[dict]]:
        """用 JS 提取题目和选项（兼容每日练习 + 模拟考试）"""
        try:
            r = await self.page.evaluate("""() => {
                let question = '';
                const body = document.body;

                // 清除旧标记
                document.querySelectorAll('[data-ow-opt]').forEach(el => el.removeAttribute('data-ow-opt'));

                // 策略1: .ue-option-item（每日练习）
                let items = document.querySelectorAll('.ue-option-item');

                // 策略2: radio input + label（模拟考试）— 找每个题目的选项
                if (items.length === 0) {
                    // 考试页：所有题目一起显示，取第一个可见题目的选项
                    const firstQ = document.querySelector('.question-item, .ques-item, [class*="question-item"]');
                    const scope = firstQ || body;
                    const radios = scope.querySelectorAll('input[type="radio"]');
                    if (radios.length > 0) {
                        // 将 radio 的父元素标记为选项
                        const seen = new Set();
                        const opts = [];
                        radios.forEach(radio => {
                            const wrapper = radio.closest('.option-item, .radio-box, [class*="option"], [class*="radio"]') || radio.parentElement;
                            if (!seen.has(wrapper)) {
                                seen.add(wrapper);
                                opts.push(wrapper);
                            }
                        });
                        items = opts;
                    }
                }

                const labels = ['A','B','C','D','E','F'];
                const opts = [];
                items.forEach((el, i) => {
                    if (i < 6) {
                        el.setAttribute('data-ow-opt', labels[i]);
                        const text = (el.innerText || el.textContent || '').trim().replace(/\\n/g, ' ');
                        opts.push({text: text});
                    }
                });

                // 题干
                const titleEl = document.querySelector('.ue-question-title, .ques-item-tip .content, [class*="question-title"]');
                if (titleEl) {
                    question = titleEl.innerText || titleEl.textContent || '';
                }

                return {question: question.trim(), options: opts};
            }""")

            question = r.get("question", "") if r else ""
            raw = r.get("options", []) if r else []

            options = []
            for i, opt in enumerate(raw):
                label = chr(ord("A") + i)
                el = self.page.locator(f'[data-ow-opt="{label}"]')
                options.append({
                    "element": el,
                    "text": opt["text"],
                    "wfLineOption": label,
                    "wfLineContent": opt["text"],
                })

            return question, options
        except Exception as e:
            await self._warn(f"  提取选项异常: {e}")
            return "", []

    # ---- 选择题解答 ----
    async def _solve_multiple_choice(self, q_text: str, opts: list, qtype: str,
                                     api_key: str, submit_holder: list, result: dict):
        """解答选择题：题库 → DeepSeek → 点击 → 提交 → 入库 → 刷新"""
        await self._info(f"  题型: {qtype} | 题干: {q_text[:60]}...")
        await self._info(f"  选项: {[o['text'][:20] for o in opts]}")

        # 先登记题目到题库（答案待定）
        bank_key = ""
        if q_text and opts:
            bank_key = record_answer(q_text, opts, "")  # 空答案 = 仅登记
            if bank_key:
                await self._info(f"  题目已登记: {bank_key[:12]}...")

        # 判断答案：题库 → DeepSeek
        answer = lookup_question(q_text, opts) if q_text else None
        if answer:
            await self._info(f"  题库命中 → {answer}")
        elif q_text and api_key:
            await self._info("  题库未命中，调用 DeepSeek...")
            answer = query_deepseek(f"<p>{q_text}</p>", opts, api_key,
                                    self.cfg.get("deepseek_base_url", "https://api.deepseek.com"),
                                    question_type=qtype, model=self.cfg.get("deepseek_model", "deepseek-chat"))
            if answer:
                await self._info(f"  DeepSeek → {answer}")
            else:
                await self._warn("  DeepSeek 返回空")

        if not answer:
            if not api_key:
                await self._warn("  未配置 DeepSeek API Key")
            answer = "A"
            await self._warn(f"  默认选{answer}")

        # 点击选项（支持多选：ABD → 逐项点击A、B、D）
        is_multi_select = "多选" in (qtype or "")
        letters_to_click = list(answer.upper()) if is_multi_select and len(answer) > 1 else [answer.upper()]
        for letter in letters_to_click:
            if 'A' <= letter <= 'F':
                idx = ord(letter) - ord("A")
                if idx < len(opts):
                    await self._info(f"  点击 {letter}: {opts[idx]['text'][:40]}")
                    try:
                        await opts[idx]["element"].click()
                        await asyncio.sleep(0.1)
                    except Exception:
                        await self._warn(f"  点击选项 {letter} 失败")
            return
        await asyncio.sleep(0.5)

        # 提交
        submit_btn = self.page.locator('button:has-text("提交答案")').first
        if await submit_btn.count() > 0:
            if await submit_btn.get_attribute("disabled") is not None:
                await self._warn("  提交按钮已禁用")
                return
            await submit_btn.click()
            await asyncio.sleep(2)

        # 提取正确答案（三路并行）
        correct = None
        is_correct = False

        # 方式1: submitAnswer API 响应（最权威）
        sd = submit_holder[0] or {}
        correct = extract_correct_from_response(sd)
        if not correct and sd.get("data"):
            d = sd["data"]
            # trueAnswer 字段 + wfAnswer 判断对错
            ta = d.get("trueAnswer", "")
            if ta and len(str(ta)) <= 2:
                correct = str(ta).strip().upper()
            # wfAnswer: "正确" or "错误"
            wf = d.get("wfAnswer", "")
            if wf == "正确":
                is_correct = True

        # 方式2: 检测"回答正确" toast
        if not is_correct:
            await asyncio.sleep(1)
            try:
                toast = self.page.locator('.layui-layer-msg, .layui-layer-dialog:has-text("正确")')
                if await toast.count() > 0:
                    toast_text = await toast.text_content() or ""
                    if "正确" in toast_text:
                        is_correct = True
                        await self._info("  检测到'回答正确'弹窗")
            except Exception:
                pass

        # 方式3: 页面DOM — 找绿色高亮选项（正确选项）
        if not correct:
            try:
                correct = await self.page.evaluate("""() => {
                    const items = document.querySelectorAll('.ue-option-item');
                    for (const el of items) {
                        const style = el.getAttribute('style') || '';
                        const classList = el.className || '';
                        // 绿色边框/背景 = 正确答案
                        if (style.includes('green') || style.includes('rgb(0, 128') || style.includes('#4caf50') ||
                            classList.includes('correct') || classList.includes('right') || classList.includes('success')) {
                            const t = el.innerText || '';
                            const m = t.match(/^([A-F])/);
                            if (m) return m[1];
                        }
                    }
                    return null;
                }""")
            except Exception:
                pass

        # 记录结果 + 更新题库
        if correct:
            # ✅ 系统返回了正确答案 → 更新题库
            if q_text and opts:
                record_answer(q_text, opts, correct, source="submitAnswer")
                await self._info(f"  题库已更新: {correct}")

            if correct.upper() == answer.upper():
                result["correct"] += 1
                result["points"] += 1
                await self._info(f"  ✓ 答对! ({correct})")
            else:
                result["wrong"] += 1
                await self._warn(f"  ✗ 答错 选{answer} 正确{correct}")
        elif is_correct:
            # toast显示正确 → 用DeepSeek答案入库
            if q_text and opts:
                record_answer(q_text, opts, answer, source="submitAnswer")
            result["correct"] += 1
            result["points"] += 1
            await self._info(f"  ✓ 答对! (toast)")
        else:
            await self._warn(f"  无法确认答案，跳过入库")
            # 不记分，等系统确认

        # 如果答错，需要手动点刷新（答对会自动刷新）
        if not is_correct and correct and correct.upper() != answer.upper():
            await self._info("  答错，点击刷新获取下一题...")
            refresh_btn = self.page.locator('button:has-text("刷新")').first
            if await refresh_btn.count() > 0:
                await refresh_btn.click()
        await asyncio.sleep(2)

    # ---- 填空题解答 ----
    async def _solve_fill_blank(self, q_text: str, qtype: str,
                                api_key: str, submit_holder: list, result: dict):
        """解答填空题：DeepSeek 生成答案 → 填入 → 提交 → 入库 → 刷新"""
        await self._info(f"  题型: {qtype} | 题目: {q_text[:80]}...")

        if not api_key:
            await self._warn("  填空题需要 DeepSeek API Key")
            refresh_btn = self.page.locator('button:has-text("刷新")').first
            if await refresh_btn.count() > 0:
                await refresh_btn.click()
            return

        # 找填空输入框
        try:
            blank_count = await self.page.evaluate("""() => {
                const inputs = document.querySelectorAll('.ue-fill-list input, .ue-fill-item input, input[type="text"]');
                return inputs.length;
            }""")
        except Exception:
            blank_count = 1

        if blank_count == 0:
            blank_count = 1

        await self._info(f"  检测到 {blank_count} 个空，调用 DeepSeek...")
        answers = query_deepseek_fill(f"<p>{q_text}</p>", blank_count, api_key,
                                      self.cfg.get("deepseek_base_url", "https://api.deepseek.com"),
                                      question_type=qtype, model=self.cfg.get("deepseek_model", "deepseek-chat"))

        if not answers:
            await self._warn("  DeepSeek 填空返回空，跳过")
            refresh_btn = self.page.locator('button:has-text("刷新")').first
            if await refresh_btn.count() > 0:
                await refresh_btn.click()
            return

        await self._info(f"  DeepSeek → {answers}")

        # 填入答案
        try:
            for j, ans in enumerate(answers):
                await self.page.evaluate(f"""(idx, text) => {{
                    const inputs = document.querySelectorAll('.ue-fill-list input, .ue-fill-item input, input[type="text"]');
                    if (inputs[idx]) {{ inputs[idx].value = text; inputs[idx].dispatchEvent(new Event('input')); }}
                }}""", j, ans)
            await self._info(f"  已填入 {len(answers)} 个答案")
        except Exception as e:
            await self._warn(f"  填入失败: {e}")

        await asyncio.sleep(0.5)

        # 提交
        submit_btn = self.page.locator('button:has-text("提交答案")').first
        if await submit_btn.count() > 0:
            if await submit_btn.get_attribute("disabled") is not None:
                await self._warn("  提交按钮已禁用")
                return
            await submit_btn.click()
            await asyncio.sleep(2)

        # 从 API 响应提取正确答案
        # 从 API 响应提取
        correct = extract_correct_from_response(submit_holder[0] or {})
        if correct:
            await self._info(f"  正确答案: {correct}")
        else:
            await self._info("  填空题已提交（答案未知）")

        result["correct"] += 1  # 乐观计分
        result["points"] += 1

        # 刷新
        refresh_btn = self.page.locator('button:has-text("刷新")').first
        if await refresh_btn.count() > 0:
            await refresh_btn.click()
        await asyncio.sleep(2)

    # ---- 检查每日练习积分上限 ----
    async def _check_daily_exercise_limit(self) -> int:
        """从积分页检查每日练习已获分数"""
        base = self.cfg.get("base_url", "").rstrip("/")
        try:
            score_url = f"{base}/src/apps/app-aqhb/src/components/studentWeb/user-score/index.html?type=week"
            await self.page.goto(score_url, wait_until="networkidle", timeout=15000)
            await asyncio.sleep(2)
            el = self.page.locator("#dtStudyWeek")
            if await el.count() > 0:
                text = (await el.text_content() or "").strip()
                # "已领 3 分" → 提取数字
                import re as _re
                m = _re.search(r"(\d+)", text)
                if m:
                    return int(m.group(1))
        except Exception:
            pass
        return 0

    # ---- 获取周积分 ----
    async def get_weekly_points(self) -> int:
        base = self.cfg.get("base_url", "").rstrip("/")
        try:
            await self.page.goto(f"{base}/src/apps/app-aqhb/src/portal/index.html",
                                 wait_until="networkidle", timeout=15000)
            await self._wait_content()
            el = self.page.locator("#weekLearningPoints")
            if await el.count() > 0:
                val = int((await el.text_content() or "0").strip())
                await self._info(f"当前周积分: {val}")
                return val
        except Exception:
            pass
        return 0

    # ---- 模拟考试 ----
    async def do_mock_exam(self, api_key: str = "", exam_index: int = 0) -> dict:
        """模拟考试：列出考试 → 选择指定考试 → 答题 → 提交试卷"""
        base = self.cfg.get("base_url", "").rstrip("/")
        url = f"{base}/src/apps/app-aqhb/src/components/studentWeb/user-exercise/index.html"
        result = {"points": 0, "correct": 0, "total": 0}

        await self._info("→ 模拟考试")
        await self.page.goto(url, wait_until="networkidle", timeout=30000)
        await self._wait_content()

        mock_tab = self.page.locator('.tab[data-type="mock"]').first
        if await mock_tab.count() > 0:
            await mock_tab.click()
            await self._wait_content()
            await self._info("  已切换到模拟考试")

        # 列出可用考试
        exams = await self.page.evaluate("""() => {
            const items = document.querySelectorAll('li.startexam');
            return Array.from(items).map(item => ({
                name: (item.querySelector('._name')?.innerText||'').trim(),
                btn: !!item.querySelector('.ue-start-exam')
            }));
        }""")
        if exams:
            await self._info(f"  共{len(exams)}场考试: {[e['name'][:20] for e in exams[:5]]}")

        # 按用户选择的索引
        if exam_index and exam_index < len(exams):
            chosen = exams[exam_index]
        else:
            chosen = exams[0]
        await self._info(f"  → 选择: {chosen['name'][:50]}")

        start_btn = self.page.locator('.ue-start-exam').nth(exam_index if exam_index else 0)
        if await start_btn.count() == 0:
            await self._warn("未找到开始考试按钮")
            return result
        await start_btn.click()
        await self._wait_content()
        await self._screenshot("mock_exam_page")

        # 展开所有折叠的题型区域
        await self.page.evaluate("""() => {
            document.querySelectorAll('button._on, button[ques_type], .layui-btn._on').forEach(btn => btn.click());
        }""")
        await asyncio.sleep(1)

        # 找所有题目容器
        q_containers = await self.page.evaluate("""() => {
            return document.querySelectorAll('.ques-item').length;
        }""")

        await self._info(f"  共 {q_containers} 道题")
        skip_streak = 0  # 连续跳过计数，达到阈值就停止

        for qi in range(q_containers):
            if not self._running:
                break
            if skip_streak >= 10:  # 连续10题无法回答就停
                await self._info(f"  连续{skip_streak}题无法回答，停止答题")
                break

            # 静默滚动到当前题目（不触发动画）
            await self.page.evaluate(f"""(idx) => {{
                const items = document.querySelectorAll('.ques-item');
                if (items[idx]) {{
                    items[idx].scrollIntoView({{block: 'center', behavior: 'instant'}});
                }}
            }}""", qi)
            await asyncio.sleep(0.1)

            # 提取当前题目的选项
            q_text, opts = await self._extract_options_for_question(qi)

            if opts:
                # ---- 选择题/判断题（含多选检测） ----
                qtype = await self.page.evaluate(f"""(idx) => {{
                    const items = document.querySelectorAll('.ques-item');
                    const box = items[idx]?.closest('.ques-box');
                    if (!box) return '单选';
                    if (box.classList.contains('multiple-box')) return '多选';
                    if (box.classList.contains('judge-box')) return '判断';
                    if (box.classList.contains('fill-box')) return '填空';
                    if (box.classList.contains('write-box')) return '简答';
                    return '单选';
                }}""", qi)
                is_multi = qtype == '多选'
                is_judge = qtype == '判断'
                qtype_label = "多选题" if is_multi else ("判断题" if is_judge else "")
                await self._info(f"  题[{qi+1}]{'(多选)' if is_multi else ''}: {q_text[:40]}...")
                answer = lookup_question(q_text, opts) if q_text else None
                if answer:
                    await self._info(f"    题库→{answer}")
                elif q_text and api_key:
                    answer = query_deepseek(f"<p>{q_text}</p>", opts, api_key,
                                            self.cfg.get("deepseek_base_url", "https://api.deepseek.com"),
                                            question_type=qtype_label,
                                            model=self.cfg.get("deepseek_model", "deepseek-chat"))
                    if answer:
                        await self._info(f"    DeepSeek→{answer}")
                if is_multi:
                    ans_letters = (answer or "ABCD").upper()
                    indices = [ord(c) - ord('A') for c in ans_letters if 'A' <= c <= 'F']
                    # 专家建议：逐个重新查询 + 返回详细调试信息
                    debug = await self.page.evaluate("""([idx, idxs]) => {
                        const items = document.querySelectorAll('.ques-item');
                        const qEl = items[idx];
                        if (!qEl) return {ok: false, reason: 'no question'};

                        // 记录点击前状态
                        const beforeChecked = [...qEl.querySelectorAll('input[type=checkbox]')].map(x => x.checked);
                        const beforeClass = [...qEl.querySelectorAll('.layui-form-checkbox')].map(x => x.className);

                        // 逐个点击（每次重新查询，layui 重渲染后旧引用失效）
                        const clicked = [];
                        for (const i of idxs) {
                            const cbList = qEl.querySelectorAll('.layui-form-checkbox');
                            const cb = cbList[i];
                            if (cb) {
                                cb.click();
                                clicked.push(i);
                            }
                        }

                        // 记录点击后状态
                        const afterChecked = [...qEl.querySelectorAll('input[type=checkbox]')].map(x => x.checked);
                        const afterClass = [...qEl.querySelectorAll('.layui-form-checkbox')].map(x => x.className);

                        return {
                            total: qEl.querySelectorAll('.layui-form-checkbox').length,
                            clicked: clicked,
                            beforeChecked: beforeChecked,
                            afterChecked: afterChecked,
                            beforeClass: beforeClass,
                            afterClass: afterClass,
                            htmlSample: qEl.innerHTML.substring(0, 500)
                        };
                    }""", [qi, indices])

                    await self._info(f"    多选debug: total={debug.get('total')} clicked={debug.get('clicked')}")
                    await self._info(f"    before: {debug.get('beforeChecked')} | after: {debug.get('afterChecked')}")
                    await self._info(f"    class: {[c[-30:] for c in (debug.get('afterClass') or [])]}")
                else:
                    answer = answer or "A"
                    for a in answer.upper():
                        if 'A' <= a <= 'F':
                            try:
                                el = self.page.locator(f'[data-ow-opt="{a}"]').first
                                if await el.count() > 0:
                                    await el.click()
                                    await asyncio.sleep(0.1)
                            except Exception:
                                pass
                result["total"] += 1
                result["correct"] += 1
                if q_text:
                    record_answer(q_text, opts, answer, source=self.cfg.get('deepseek_model', 'deepseek-chat'))
                skip_streak = 0
            else:
                # ---- 填空题 ----
                blanks = await self.page.evaluate(f"""(idx) => {{
                    const items = document.querySelectorAll('.ques-item');
                    const qEl = items[idx];
                    if (!qEl) return 0;
                    return qEl.querySelectorAll('input.layui-text, input[type="text"], textarea.layui-textarea, textarea').length;
                }}""", qi)

                if blanks > 0 and api_key and q_text:
                    # 检测题型：textarea = 简答, input = 填空
                    has_textarea = await self.page.evaluate(f"""(idx) => {{
                        const items = document.querySelectorAll('.ques-item');
                        return items[idx]?.querySelectorAll('textarea').length || 0;
                    }}""", qi)
                    fill_type = "简答题" if has_textarea else "填空题"
                    await self._info(f"  题[{qi+1}] {fill_type}: {q_text[:40]}...")
                    answers = query_deepseek_fill(f"<p>{q_text}</p>", blanks, api_key,
                                                  self.cfg.get("deepseek_base_url", "https://api.deepseek.com"),
                                                  question_type=fill_type, model=self.cfg.get("deepseek_model", "deepseek-chat"))
                    if answers:
                        await self._info(f"    DeepSeek→{answers}")
                        # 逐个填入（用列表参数避免多参数报错）
                        for bi, ans in enumerate(answers[:blanks]):
                            await self.page.evaluate("""([idx, blankIdx, text]) => {
                                const items = document.querySelectorAll('.ques-item');
                                const inputs = items[idx].querySelectorAll('input.layui-text, input[type="text"], textarea.layui-textarea, textarea');
                                if (inputs[blankIdx]) {
                                    inputs[blankIdx].value = text;
                                    inputs[blankIdx].dispatchEvent(new Event('input', {bubbles: true}));
                                    inputs[blankIdx].dispatchEvent(new Event('change', {bubbles: true}));
                                }
                            }""", [qi, bi, ans])
                        result["total"] += 1
                        result["correct"] += 1
                        # 录题库
                        if q_text:
                            record_answer(q_text, [], answers[0] if len(answers) == 1 else "||".join(answers), source=self.cfg.get('deepseek_model', 'deepseek-chat'))
                        skip_streak = 0
                    else:
                        # 重试一次
                        await asyncio.sleep(1)
                        answers = query_deepseek_fill(f"<p>{q_text}</p>", blanks, api_key,
                                                      self.cfg.get("deepseek_base_url", "https://api.deepseek.com"),
                                                      question_type=fill_type,
                                                      model=self.cfg.get("deepseek_model", "deepseek-chat"))
                        if answers:
                            await self._info(f"    重试成功→{answers}")
                            for bi, ans in enumerate(answers[:blanks]):
                                await self.page.evaluate("""([idx, blankIdx, text]) => {
                                    const items = document.querySelectorAll('.ques-item');
                                    const inputs = items[idx].querySelectorAll('input.layui-text, input[type="text"], textarea.layui-textarea, textarea');
                                    if (inputs[blankIdx]) {
                                        inputs[blankIdx].value = text;
                                        inputs[blankIdx].dispatchEvent(new Event('input', {bubbles: true}));
                                    }
                                }""", [qi, bi, ans])
                            result["total"] += 1
                            result["correct"] += 1
                            skip_streak = 0
                        else:
                            skip_streak += 1
                            await self._warn(f"  题[{qi+1}] DeepSeek填空失败(已重试)")
                else:
                    skip_streak += 1
                    await self._warn(f"  题[{qi+1}] 无选项无输入，跳过")

            await asyncio.sleep(0.3)  # 放慢

        # 不自动提交，提示用户手动提交
        await self._info(f"  === 全部 {result['total']} 题已回答 ===")
        await self._info("  ⚠ 请在浏览器中手动提交试卷 + 签名")
        result["points"] = result["correct"]
        await self._info(f"模拟考试: {result['correct']}/{result['total']}, 预计+{result['points']}分")
        return result

    async def _extract_options_for_question(self, qi: int) -> tuple[str, list[dict]]:
        """提取第 qi 道题的题干和选项（考试页专用）"""
        try:
            r = await self.page.evaluate("""(idx) => {
                const items = document.querySelectorAll('.ques-item');
                const qEl = items[idx];
                if (!qEl) return {question: '', options: []};

                // 清除所有旧标记
                document.querySelectorAll('[data-ow-opt]').forEach(el => el.removeAttribute('data-ow-opt'));

                // 找 .radio-box 元素，根据题型取不同的点击目标
                const radioBoxes = qEl.querySelectorAll('.radio-box');
                const firstInput = qEl.querySelector('input');
                const isCheckbox = firstInput && firstInput.type === 'checkbox';
                const labels = ['A','B','C','D','E','F'];
                const result = [];
                radioBoxes.forEach((box, i) => {
                    if (i < 6) {
                        // checkbox → .layui-form-checkbox, radio → .layui-form-radio
                        const clickTarget = isCheckbox
                            ? box.querySelector('.layui-form-checkbox')
                            : box.querySelector('.layui-form-radio');
                        if (clickTarget) {
                            clickTarget.setAttribute('data-ow-opt', labels[i]);
                        }  // 只标记实际点击目标，不标记外层 box
                        const text = (box.innerText || box.textContent || '').trim().replace(/\\\\n/g, ' ');
                        result.push({text: text});
                    }
                });

                // 题干
                const contentEl = qEl.querySelector('.ques-item-tip .content, .content');
                const question = contentEl ? (contentEl.innerText || '').trim() : '';

                return {question, options: result};
            }""", qi)

            question = r.get("question", "") if r else ""
            raw = r.get("options", []) if r else []

            opts = []
            for i, opt in enumerate(raw):
                label = chr(ord("A") + i)
                el = self.page.locator(f'[data-ow-opt="{label}"]')
                opts.append({"element": el, "text": opt["text"],
                             "wfLineOption": label, "wfLineContent": opt["text"]})

            return question, opts
        except Exception as e:
            await self._warn(f"  提取题{qi}异常: {e}")
            return "", []

    @property
    def running(self) -> bool:
        return self._running

    @running.setter
    def running(self, value: bool):
        self._running = value
