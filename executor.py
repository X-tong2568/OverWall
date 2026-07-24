"""
Playwright 执行层 —— 浏览器自动化：登录、刷图文、刷视频、答题
"""
import asyncio
import json
import os
import re
import sys
import time
import zipfile
import io
from playwright.async_api import async_playwright, Page, Browser

from question_bank import lookup_question, record_answer
from ai_solver import query_deepseek, query_deepseek_fill, extract_correct_from_response

# PyInstaller 打包后数据存到 exe 同目录，避免写入临时文件夹
if getattr(sys, 'frozen', False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Playwright 浏览器版本（与 playwright 1.52 匹配）
_CHROMIUM_REVISION = "1169"
_FFMPEG_REVISION = "1011"


def _find_system_browser() -> tuple[str | None, str | None]:
    """检测系统浏览器，返回 (名称, 可执行文件完整路径)，未找到返回 (None, None)"""
    import shutil
    import platform

    # Windows: 查 PATH + 常见安装路径
    if platform.system() == "Windows":
        candidates = [
            ("Edge", "msedge", [
                os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
                os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            ]),
            ("Chrome", "chrome", [
                os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            ]),
        ]
        for name, exe_name, paths in candidates:
            # 先查 PATH
            found = shutil.which(exe_name)
            if found and os.path.exists(found):
                return name, found
            # 再查固定路径
            for p in paths:
                if os.path.exists(p):
                    return name, p
    else:
        # Linux/macOS: 只用 PATH
        for name, exe in [("Chrome", "google-chrome-stable"),
                          ("Chrome", "google-chrome"),
                          ("Edge", "microsoft-edge")]:
            found = shutil.which(exe)
            if found:
                return name, found

    return None, None


def _ensure_playwright_browsers(log_fn=None, force_download: bool = False) -> bool:
    """
    确保 Playwright Chromium 浏览器可用
    查找优先级：
      1. 系统已安装 Edge/Chrome (非 force_download) → 跳过下载
      2. exe 同目录/playwright-browsers（打包环境持久化目录）
      3. 系统全局 %LOCALAPPDATA%/ms-playwright（开发环境/全局安装）
      4. 都没有 → 自动下载到 exe 同目录
    force_download=True: 跳过系统浏览器检测，强制检查/下载 Chromium（用于系统浏览器启动失败后的回退）
    返回 True 表示浏览器已就绪
    """
    log = log_fn or (lambda msg: None)

    # 确定持久化浏览器目录（打包环境用 exe 同目录，开发环境用全局缓存）
    if getattr(sys, 'frozen', False):
        local_root = os.path.join(os.path.dirname(sys.executable), 'playwright-browsers')
    else:
        local_root = None
    global_cache = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'ms-playwright')

    # 始终设置 PLAYWRIGHT_BROWSERS_PATH，防止 Playwright 内部解析到 PyInstaller 临时目录
    persistent_dir = local_root or global_cache
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = persistent_dir

    # 非强制下载时，检测到系统浏览器则跳过（start_browser 策略1/2 会用）
    if not force_download:
        sys_name, sys_path = _find_system_browser()
        if sys_name:
            log(f"检测到系统 {sys_name}，跳过 Chromium 下载")
            return True

    # 候选目录列表：先本地后全局
    candidates = []
    if local_root:
        candidates.append(local_root)
    candidates.append(global_cache)

    chromium_exe = None
    found_dir = None
    for d in candidates:
        exe_path = os.path.join(d, f'chromium-{_CHROMIUM_REVISION}', 'chrome-win', 'chrome.exe')
        if os.path.exists(exe_path):
            chromium_exe = exe_path
            found_dir = d
            break

    if chromium_exe:
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = found_dir
        return True

    # 没找到 → 下载到本地持久化目录
    download_dir = persistent_dir
    log("未检测到系统浏览器，正在下载 Chromium（首次运行需约 145MB，请耐心等待）...")
    try:
        import requests as _requests

        # 下载 chromium
        chromium_url = (
            f"https://cdn.playwright.dev/dbazure/download/playwright/builds/"
            f"chromium/{_CHROMIUM_REVISION}/chromium-win64.zip"
        )
        log(f"下载 Chromium v{_CHROMIUM_REVISION}...")
        resp = _requests.get(chromium_url, timeout=600, stream=True)
        resp.raise_for_status()

        # 流式下载 + 解压
        total_size = int(resp.headers.get('content-length', 0))
        downloaded = 0
        chunks = []
        for chunk in resp.iter_content(chunk_size=8192):
            chunks.append(chunk)
            downloaded += len(chunk)
            if total_size and downloaded % (total_size // 10 + 1) < 8192:
                pct = downloaded * 100 // total_size
                log(f"  Chromium 下载进度: {pct}%")

        log("正在解压 Chromium...")
        with zipfile.ZipFile(io.BytesIO(b''.join(chunks))) as zf:
            extract_dir = os.path.join(download_dir, f'chromium-{_CHROMIUM_REVISION}')
            os.makedirs(extract_dir, exist_ok=True)
            zf.extractall(extract_dir)
        log("Chromium 安装完成")

        # 下载 ffmpeg（视频播放需要）
        ffmpeg_dir = os.path.join(download_dir, f'ffmpeg-{_FFMPEG_REVISION}')
        if not os.path.exists(ffmpeg_dir):
            ffmpeg_url = (
                f"https://cdn.playwright.dev/dbazure/download/playwright/builds/"
                f"ffmpeg/{_FFMPEG_REVISION}/ffmpeg-win64.zip"
            )
            log("下载 ffmpeg 编解码器...")
            resp = _requests.get(ffmpeg_url, timeout=120)
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                os.makedirs(ffmpeg_dir, exist_ok=True)
                zf.extractall(ffmpeg_dir)
            log("ffmpeg 安装完成")

        return True
    except Exception as e:
        log(f"浏览器下载失败: {e}")
        return False


class TaskExecutor:
    """自动化任务执行器"""

    def __init__(self, config: dict, log_callback=None):
        self.cfg = config
        self.log = log_callback or (lambda lvl, msg: None)
        self.browser: Browser | None = None
        self.page: Page | None = None
        self._running = False
        self._login_done = False
        self._seen_courseids: set = set()  # 实例级 courseid 去重（跨轮次持久，避免刷重复视频）

    # ---- 日志 ----
    async def _info(self, msg: str):
        await self.log("info", msg)

    async def _warn(self, msg: str):
        await self.log("warn", msg)

    async def _error(self, msg: str):
        await self.log("error", msg)

    # ---- 浏览器生命周期 ----
    async def start_browser(self) -> Page | None:
        """启动浏览器，按优先级尝试：系统 Chrome → 系统 Edge → 自带 Chromium"""
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
            headless = self.cfg.get("headless", False)
            common_args = [
                "--disable-blink-features=AutomationControlled",
                "--autoplay-policy=no-user-gesture-required",
                "--mute-audio",
            ]
            launch_opts = {
                "headless": headless,
                "slow_mo": self.cfg.get("browser_slow_mo", 300),
                "args": common_args,
            }

            launched = False

            # 策略1: 用 executable_path 直连系统浏览器（绕过 Playwright channel 解析，
            # 避免 PyInstaller 打包后 channel 找不到浏览器）
            sys_name, sys_path = _find_system_browser()
            if sys_path:
                try:
                    self.browser = await p.chromium.launch(
                        executable_path=sys_path, **launch_opts
                    )
                    await self._info(f"使用系统 {sys_name}（{sys_path}）")
                    launched = True
                except Exception as e:
                    await self._warn(f"系统 {sys_name} 启动失败: {e}")

            # 策略2: channel 方式回退（开发环境或 executable_path 失败后尝试）
            if not launched:
                for channel, label in [("msedge", "系统 Edge"), ("chrome", "系统 Chrome")]:
                    try:
                        self.browser = await p.chromium.launch(
                            channel=channel, **launch_opts
                        )
                        await self._info(f"使用 {label}（channel）")
                        launched = True
                        break
                    except Exception:
                        continue

            # 策略3: 强制下载 Chromium 到持久化目录（系统浏览器已在前两步失败）
            if not launched:
                await self._warn("未找到可用浏览器，正在自动下载 Chromium...")
                if _ensure_playwright_browsers(
                    lambda msg: asyncio.ensure_future(self._info(msg)),
                    force_download=True
                ):
                    # 下载完成后查找 chromium 可执行文件
                    chromium_exe = None
                    browsers_root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
                    if browsers_root:
                        candidate = os.path.join(
                            browsers_root,
                            f"chromium-{_CHROMIUM_REVISION}",
                            "chrome-win",
                            "chrome.exe",
                        )
                        if os.path.exists(candidate):
                            chromium_exe = candidate

                    if chromium_exe:
                        try:
                            self.browser = await p.chromium.launch(
                                executable_path=chromium_exe, **launch_opts
                            )
                            await self._info("使用自带 Chromium（已自动安装）")
                            launched = True
                        except Exception as e2:
                            await self._error(f"Chromium 启动失败: {e2}")
                            return None
                    else:
                        await self._error("Chromium 下载后未找到可执行文件，请手动安装 Chrome 或 Edge")
                        return None
                else:
                    await self._error("Chromium 下载失败，请手动安装 Chrome 或 Edge")
                    return None

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
        try:
            await self.page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass  # 视频页/长连接页可能永远达不到 networkidle，忽略继续
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
        """刷图文学习：从积分页进入推荐，推荐刷完自动回退专业课程(图文)"""
        points = 0
        await self._info("→ 图文: 推荐页")

        # 从积分页进入
        if await self._goto_study_from_score("推荐"):
            await self._dump_page("article")
            for i in range(count):
                if not self._running:
                    break
                await self._info(f"图文 [{i+1}/{count}]: 点击文章卡片...")
                clicked = await self._click_article_card()
                if not clicked:
                    await self._warn("图文: 未找到可点击的文章")
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

        # 推荐全部已学习 → 回退到集团课程-专业课程(图文)
        if points == 0 and self._running:
            await self._info("→ 推荐页无可读，回退到集团课程-专业课程(图文)")
            points += await self._do_study_loop("集团课程", "专业课程", count,
                                                 click_play=False)

        await self._info(f"图文结束: +{points}分")
        return points

    async def _click_article_card(self) -> bool:
        """找到并点击课程卡片（实例级 courseid 去重 + 触屏滚动加载 + is-learned 检测）

        实测 DOM 结构（视频课程页）：
        <li class="course-item" courseid="HUIccW5aemEWCPh">
          <span class="info-item _name">课程标题</span>
          <span class="info-item _hours">0.1 课程时长</span>
        </li>
        使用 self._seen_courseids 实例级去重，同一进程内所有分类共享，避免跨轮重复。
        """

        # 卡片选择器（实测：视频课程页用 li.course-item，图文页用 li.course-card--hero 等）
        CARD_SELECTORS = (
            'li.course-item, li.course-card--hero, li.course-card--list, '
            'li.course-card--text, li.course-card, li[class*="course-card"], '
            'li[class*="course-item"], div.course-card, div[class*="course-card"]'
        )
        # JS：提取卡片信息（courseid 优先做去重键，标题备用）
        JS_EXTRACT_CARDS = f"""(seen) => {{
            const cards = document.querySelectorAll('{CARD_SELECTORS}');
            const results = [];
            for (let i = 0; i < cards.length; i++) {{
                const card = cards[i];
                // 去重键：courseid 属性 > data-id 属性 > 规范化标题
                const cid = card.getAttribute('courseid') || card.getAttribute('data-id') || '';
                // 标题：实测 span.info-item._name / span._name
                let titleEl = card.querySelector('._name, .course-title, .card-title, [class*="course-title"], [class*="card-title"]');
                if (!titleEl) {{
                    const candidates = card.querySelectorAll('[class*="title"], [class*="_name"]');
                    for (const c of candidates) {{
                        const cn = c.className || '';
                        if (!/(status|badge|tag|label|mark|complete|finish|learned|done)/i.test(cn)) {{
                            titleEl = c; break;
                        }}
                    }}
                }}
                if (!titleEl) titleEl = card;
                let title = (titleEl.innerText || titleEl.textContent || '').replace(/\\s+/g, ' ').trim();
                // 平台标记：已学习卡片带 is-learned CSS class（兼容直接标记和父级标记）
                const isLearned = card.classList.contains('is-learned') ||
                                  !!card.querySelector('.is-learned') ||
                                  !!card.closest('.is-learned');
                // 去重判断：courseid 优先，标题回退，is-learned 兜底
                const dedupKey = cid || title;
                const alreadySeen = isLearned ||
                                    (cid && seen.includes(cid)) ||
                                    (!cid && title && seen.includes(title));
                results.push({{index: i, title: title, courseid: cid, dedupKey: dedupKey, alreadySeen: alreadySeen, isLearned: isLearned}});
            }}
            return results;
        }}"""

        try:
            # 滚动直到找到未看过的卡片
            for attempt in range(10):
                cards_info = await self.page.evaluate(JS_EXTRACT_CARDS, list(self._seen_courseids))

                first_unseen = None
                for ci in cards_info:
                    if not ci["alreadySeen"] and ci["dedupKey"]:
                        first_unseen = ci
                        break

                if first_unseen:
                    break  # 找到了

                # 没找到 → 模拟移动端触屏滑动加载更多
                # 页面本质是手机网页，PC 端 scrollTop 赋值不会触发懒加载
                # 需要用 TouchEvent 派发触屏滑动事件
                prev = await self.page.evaluate(
                    f"() => document.querySelectorAll('{CARD_SELECTORS}').length")
                await self.page.evaluate("""() => {
                    const el = document.querySelector('.study-content, main') || document.scrollingElement || document.body;
                    const vh = window.innerHeight;
                    // 模拟手指从屏幕下方 75% 向上滑到 25%
                    const startY = vh * 0.75;
                    const endY = vh * 0.25;
                    const midX = window.innerWidth / 2;
                    // 派发 touch 事件链
                    el.dispatchEvent(new TouchEvent('touchstart', {
                        touches: [{clientX: midX, clientY: startY, identifier: 0}],
                        bubbles: true, cancelable: true
                    }));
                    el.dispatchEvent(new TouchEvent('touchmove', {
                        touches: [{clientX: midX, clientY: endY, identifier: 0}],
                        bubbles: true, cancelable: true
                    }));
                    el.dispatchEvent(new TouchEvent('touchend', {
                        changedTouches: [{clientX: midX, clientY: endY, identifier: 0}],
                        bubbles: true, cancelable: true
                    }));
                    // scrollIntoView 兜底：把最后一个卡片滚到视野内触发 IntersectionObserver
                    const cards = document.querySelectorAll('li.course-item, li[class*="course-card"], div[class*="course-card"]');
                    if (cards.length > 0) {
                        cards[cards.length - 1].scrollIntoView({block: 'end', behavior: 'instant'});
                    }
                }""")
                await asyncio.sleep(1.5)  # 多等 0.5s 给懒加载响应时间
                now = await self.page.evaluate(
                    f"() => document.querySelectorAll('{CARD_SELECTORS}').length")
                if now == prev:
                    break  # 没新内容了

            # 重新提取最新卡片列表
            cards_info = await self.page.evaluate(JS_EXTRACT_CARDS, list(self._seen_courseids))

            if not cards_info:
                await self._warn("  页面无课程卡片")
                return False

            # 日志：列出所有卡片状态
            for ci in cards_info:
                if ci.get("isLearned"):
                    marker = "L"  # L = 平台标记已学习 (is-learned)
                elif ci["alreadySeen"]:
                    marker = "V"  # V = 本地去重已看过
                else:
                    marker = "N"  # N = 新卡片
                cid_str = f" [{ci['courseid'][:12]}]" if ci['courseid'] else ""
                await self._info(f"    [{marker}]{cid_str} {ci['title'][:60]}")

            # 找第一个未看过的
            for ci in cards_info:
                if ci["alreadySeen"]:
                    continue
                if not ci["dedupKey"]:
                    continue
                # 用 Playwright locator 点击
                card = self.page.locator(CARD_SELECTORS).nth(ci["index"])
                if await card.count() > 0:
                    await card.scroll_into_view_if_needed()
                    await asyncio.sleep(0.3)
                    await card.click()
                    await self._info(f"  点击: {ci['title'][:50]}")
                    self._seen_courseids.add(ci["dedupKey"])
                    await asyncio.sleep(1)
                    return True

            await self._warn(f"  所有{len(cards_info)}个卡片均已看过")
        except Exception as e:
            await self._warn(f"  点击卡片异常: {e}")
        return False

    # ---- 刷视频 ----
    async def study_videos(self, tab: int = 2, count: int = 5) -> int:
        """刷视频学习：集团课程(视频课程) / 单位课程 / 案例学习(含事故案例)"""
        points = 0
        tab_name = {2: "集团课程", 3: "单位课程", 4: "案例学习"}.get(tab, f"tab{tab}")

        if tab == 2:
            # 集团课程：只刷视频课程子tab（事故案例与案例学习tab=4内容重复，不在此回退）
            points += await self._do_study_loop(tab_name, "视频课程", count,
                                                 click_play=True)
        else:
            # 单位课程 / 案例学习：无二级导航，直接刷
            points += await self._do_study_loop(tab_name, None, count, click_play=True)

        await self._info(f"视频({tab_name})结束: +{points}分")
        return points

    async def _do_study_loop(self, score_tab: str, subtab: str | None,
                              count: int, click_play: bool) -> int:
        """通用刷课循环：积分页进入 -> 切子tab -> 遍历三级标签 -> 点卡片 -> [播放] -> 等倒计时"""
        points = 0
        label = subtab if subtab else score_tab
        await self._info(f"→ {label}")

        if not await self._goto_study_from_score(score_tab):
            await self._warn(f"{label}: 无法从积分页进入")
            return 0

        # 切子 tab（如「专业课程」「视频课程」）
        if subtab:
            await self._click_subtab(subtab)
            # 发现三级分类标签（如视频课程下的「安全警示视频」「交通违法微视频」）
            third_tags = await self._get_third_level_tags(subtab)
            if third_tags:
                await self._info(f"  三级标签({len(third_tags)}): {third_tags}")
        else:
            third_tags = []

        await self._dump_page(f"study_{label}")
        tag_idx = 0  # 当前三级标签索引
        for i in range(count):
            if not self._running:
                break
            await self._info(f"{label} [{i+1}/{count}]: 点击卡片...")
            clicked = await self._click_article_card()
            if not clicked:
                # 当前标签无卡片 → 尝试切下一个三级标签
                if third_tags and tag_idx + 1 < len(third_tags):
                    tag_idx += 1
                    await self._click_third_level_tag(third_tags[tag_idx])
                    await self._info(f"  切三级标签 [{tag_idx+1}/{len(third_tags)}]: {third_tags[tag_idx]}")
                    # 重试点击卡片（新标签下）
                    clicked = await self._click_article_card()
                if not clicked:
                    await self._warn(f"{label}: 未找到可点击的课程")
                    break
            await self._wait_content()
            if click_play:
                await self._click_play()
            await self._info("  等待学习倒计时...")
            done = await self._wait_study_done()
            if not self._running:
                break
            if done:
                points += 1
                await self._info(f"  ✓ 完成 +1 (累计{points})")
            else:
                await self._warn("  等待超时")
            if not self._running:
                break
            # 回到积分页重新进入（确保页面状态干净）
            if not await self._goto_study_from_score(score_tab):
                break
            if subtab:
                await self._click_subtab(subtab)
                # 恢复当前三级标签选择
                if third_tags and tag_idx < len(third_tags):
                    await self._click_third_level_tag(third_tags[tag_idx])

        await self._info(f"{label}结束: +{points}分")
        return points

    async def _get_third_level_tags(self, parent_subtab: str) -> list[str]:
        """发现当前页面三级分类标签（排除二级导航和'全部'筛选器）

        实测结构：集团课程 → 视频课程 → [全部, 安全警示视频, 交通违法微视频]
        三级标签与二级导航共用 .study-cats__item class
        """
        KNOWN_SUBTABS = {"视频课程", "专业课程", "图文课程"}
        try:
            tags = await self.page.evaluate("""(knownNames) => {
                const items = document.querySelectorAll('.study-cats__item');
                const results = [];
                for (const el of items) {
                    const text = (el.innerText || el.textContent || '').trim();
                    // 排除二级导航名称、排除"全部"、排除空文本
                    if (!text || text === '全部' || knownNames.includes(text)) continue;
                    // 排除过短的文本（可能是图标）
                    if (text.length < 2) continue;
                    results.push(text);
                }
                return results;
            }""", list(KNOWN_SUBTABS))
            return tags if tags else []
        except Exception as e:
            await self._warn(f"  发现三级标签异常: {e}")
            return []

    async def _click_third_level_tag(self, tag_name: str):
        """点击三级分类标签（如安全警示视频、交通违法微视频）"""
        await self._info(f"    点击三级标签: {tag_name}")
        for sel in [
            f'.study-cats__item:has-text("{tag_name}")',
            f'.study-tabs__item:has-text("{tag_name}")',
            f'span:has-text("{tag_name}")',
        ]:
            el = self.page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.click()
                await asyncio.sleep(1)
                return
        await self._warn(f"    未找到三级标签: {tag_name}")

    async def _click_study_tab(self, tab_name: str) -> bool:
        """点击页面顶部一级 study-tabs 标签（如事故案例 data-tab="accident"）

        与 _click_subtab 不同：_click_subtab 操作 .study-cats__item（二级导航），
        本方法操作 .study-tabs__item（一级页面 tab，切换整个内容区）
        返回 True 表示点击成功
        """
        await self._info(f"  切换到 study-tab: {tab_name}")
        for sel in [
            f'.study-tabs__item:has-text("{tab_name}")',
            f'[data-tab]:has-text("{tab_name}")',
            f'div:has-text("{tab_name}")',
        ]:
            el = self.page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.click()
                await asyncio.sleep(1.5)
                await self._wait_content()
                return True
        await self._warn(f"  未找到 study-tab: {tab_name}")
        return False

    async def _click_subtab(self, subtab_name: str):
        """在集团课程页面点击二级导航（视频课程/专业课程）"""
        await self._info(f"  切换到: {subtab_name}")
        for sel in [
            f'.study-cats__item:has-text("{subtab_name}")',
            f'span:has-text("{subtab_name}")',
            f'div:has-text("{subtab_name}")',
        ]:
            el = self.page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.click()
                await asyncio.sleep(1.5)
                return
        await self._warn(f"  未找到子tab: {subtab_name}")

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
        """点击视频播放按钮 + JS video.play() 兜底

        实测平台播放器结构（wechat-video 自定义播放器）：
          .wechat-play-button (覆盖层大播放按钮) → 点击触发播放
          .wechat-control-btn.wechat-play-pause-btn (底部控制栏)
          video#myVideo (controls=false, 自定义控件)
        策略：force=True 绕过 Playwright actionability 检查（覆盖层可能拦截）
        """
        # 策略1：覆盖层大播放按钮（最可靠的入口）
        for sel in [
            ".wechat-play-button",              # 实测：覆盖层播放按钮
            ".wechat-control-btn.wechat-play-pause-btn",  # 实测：底部控制栏
            "#myVideo",                         # 实测：video 元素 id
            ".fa-play",                         # Font Awesome 图标
            ".vjs-big-play-button",             # Video.js（备用）
            ".plyr__play-button",               # Plyr（备用）
        ]:
            try:
                el = self.page.locator(sel).first
                if await el.count() > 0:
                    # force=True：跳过可见性/遮挡检查，直接点击
                    await el.click(force=True)
                    await self._info(f"  已点击播放 [{sel}]")
                    await asyncio.sleep(1)
                    return
            except Exception:
                continue

        # 策略2：点击 <video> 元素自身
        try:
            video_el = self.page.locator("video").first
            if await video_el.count() > 0:
                await video_el.click(force=True)
                await self._info("  已点击 <video> 元素")
                await asyncio.sleep(1)
                return
        except Exception:
            pass

        # 策略3：JS video.play() 终极兜底
        try:
            played = await self.page.evaluate("""() => {
                let count = 0;
                document.querySelectorAll('video').forEach(v => {
                    try { v.muted = true; v.play(); count++; } catch(e) {}
                });
                // iframe 内的 video
                document.querySelectorAll('iframe').forEach(iframe => {
                    try {
                        const vids = iframe.contentDocument?.querySelectorAll('video');
                        if (vids) vids.forEach(v => {
                            try { v.muted = true; v.play(); count++; } catch(e) {}
                        });
                    } catch(e) {}
                });
                return count;
            }""")
            if played > 0:
                await self._info(f"  JS video.play() 启动了 {played} 个视频（已静音）")
                return
        except Exception:
            pass

        await self._warn("  未找到播放按钮，也未找到 video 元素")

    async def _wait_study_done(self, timeout: int = 36000) -> bool:
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

        # 本地日计数检查（替代原来读平台周累计 #dtStudyWeek 的错误逻辑）
        today_pts = self._get_today_exercise_points()
        if today_pts >= 3:
            await self._info(f"今日练习已获 {today_pts} 分（已达日上限），跳过")
            return result

        await self.page.goto(url, wait_until="networkidle", timeout=30000)
        await self._wait_content()
        await self._dump_page("exercise")

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
                # 每5题用本地日计数兜底检查
                if i > 0 and i % 5 == 0:
                    today_pts = self._get_today_exercise_points()
                    if today_pts >= 3:
                        await self._info(f"每日练习已达上限({today_pts}分)，停止")
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

                // 策略1: .ue-option-item（每日练习）+ 兜底选择器
                let items = document.querySelectorAll('.ue-option-item, .option-item, .answer-item, li[class*="option"]');

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

                // 题干：多选择器兜底，防止平台改版后取不到题目文本
                const titleEl = document.querySelector(
                    '.ue-question-title, .ques-item-tip .content, [class*="question-title"], ' +
                    '.question-title, .subject-text, .exam-question, .topic, ' +
                    'h3, h4, .title, [class*="question"]'
                );
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
        await self._info(f"  题型: {qtype} | 题干: {q_text[:60] if q_text else '(空)'}...")
        await self._info(f"  选项: {[o['text'][:20] for o in opts]}")

        # 诊断日志：帮助排查"只选A不调API"问题
        if not q_text:
            await self._warn("  ⚠ 题干为空！页面元素可能已改版，无法匹配题库也无法调API")
        if not api_key:
            await self._warn("  ⚠ API Key 为空！不会调用 DeepSeek，将默认选A")

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
                self._add_today_exercise_point()
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
            self._add_today_exercise_point()
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
        self._add_today_exercise_point()

        # 刷新
        refresh_btn = self.page.locator('button:has-text("刷新")').first
        if await refresh_btn.count() > 0:
            await refresh_btn.click()
        await asyncio.sleep(2)

    # ---- 每日练习本地日计数（替代平台周累计检查） ----
    # 平台 #dtStudyWeek 是周累计值，不能用来判断当日是否已达上限
    # 本地 daily_state.json 按日期记录当日得分，跨天自动清零

    @staticmethod
    def _daily_state_path() -> str:
        """日计数文件路径（与 question_bank.json 同级）"""
        import sys as _sys
        if getattr(_sys, 'frozen', False):
            return os.path.join(os.path.dirname(_sys.executable), "daily_state.json")
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "daily_state.json")

    @staticmethod
    def _load_daily_state() -> dict:
        """读取日计数文件，日期变了自动归零"""
        import datetime as _dt
        today = _dt.date.today().isoformat()
        path = TaskExecutor._daily_state_path()
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and data.get("date") == today:
                    return data
        except (json.JSONDecodeError, IOError):
            pass
        return {"date": today, "exercise_points": 0}

    @staticmethod
    def _save_daily_state(data: dict):
        """写入日计数文件"""
        path = TaskExecutor._daily_state_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _get_today_exercise_points(self) -> int:
        """获取当日已获得的练习积分"""
        state = self._load_daily_state()
        return state.get("exercise_points", 0)

    def _add_today_exercise_point(self):
        """答对一题 +1 分，写入文件"""
        state = self._load_daily_state()
        state["exercise_points"] = state.get("exercise_points", 0) + 1
        self._save_daily_state(state)

    def reset_daily_exercise(self):
        """公开方法：手动清除当日计数（容错）"""
        import datetime as _dt
        today = _dt.date.today().isoformat()
        self._save_daily_state({"date": today, "exercise_points": 0})
        self._info("  每日练习计数已重置")

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
