"""
Playwright 执行层 —— 浏览器自动化：登录、刷图文、刷视频、答题
"""
import asyncio
import json
import os
import random
import re
import sys
import time
import zipfile
import io
from urllib.parse import quote
from playwright.async_api import async_playwright, Page, Browser

from question_bank import lookup_question, record_answer, split_option_text, letters_to_content
from ai_solver import query_ai, query_ai_fill, extract_correct_from_response

# PyInstaller 打包后数据存到 exe 同目录，避免写入临时文件夹
if getattr(sys, 'frozen', False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Playwright 浏览器版本（与 playwright 1.52 匹配）
_CHROMIUM_REVISION = "1169"
_FFMPEG_REVISION = "1011"

# ---- 平台新版页面路径（2026-11 改版）----
_STUDY_HUB = "/src/apps/app-aqhb/src/components/studentWeb/user-study/index.html"
_COURSE_DETAIL = "/src/apps/app-aqhb/src/components/studentWeb/courseDetail.html"
_RECOMMEND_DETAIL = "/src/apps/app-aqhb/src/components/studentWeb/user-study/detail-command.html"

# 学习中心 tab：key -> (页面 tab 参数, 平台 ctype)
_STUDY_TABS = {
    "recommend": (1, "recommend"),
    "group": (2, "group"),
    "unit": (3, "unit"),
    "accident": (4, "accident"),
}

# 学习任务分类：key -> (tab key, 分类名称；None 表示取接口返回的第一个分类)
_STUDY_CATEGORIES = {
    "recommend": ("recommend", None),
    "group_video": ("group", "视频课程"),
    "group_pro": ("group", "专业课程"),
    "unit": ("unit", None),
    "accident": ("accident", None),
}

# 积分页任务项 data-type 映射（用于一步步点击进入学习中心）
_STUDY_SCORE_TYPES = {
    "recommend": "study",
    "group": "study2",
    "unit": "study3",
    "accident": "study4",
}


def _to_int(value, default: int = 0) -> int:
    """安全转整数（平台 total 等字段偶尔是字符串）"""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _norm_text(text: str) -> str:
    """去掉空白，用于判断题干是否变化"""
    return re.sub(r"\s+", "", text or "")


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
        self._recommend_class_id: str | None = None  # 推荐页分类 ID（从页面请求里抓取）
        self._study_class_cache: dict = {}  # 学习中心分类列表缓存

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
        if not self.page:
            await self._warn("浏览器未启动，无法登录")
            return False

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

        # 填表（2026-11 改版：账号框 name=userCode，登录按钮 #tgit-app-login-btn-sub）
        # 新版按 Enter 不会提交，必须点按钮
        try:
            uname = self.page.locator('input[name="userCode"], input[type="text"]').first
            pwd = self.page.locator('input[name="password"], input[type="password"]').first
            await uname.click()
            await uname.fill(username)
            await pwd.click()
            await pwd.fill(password)
            await self._info(f"已填入 {username}，提交...")

            btn = self.page.locator('#tgit-app-login-btn-sub').first
            if await btn.count() == 0:
                btn = self.page.locator('button:has-text("确认")').first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
            else:
                await pwd.press("Enter")
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

        # 打印页面提示，便于排查账号密码/风控问题
        try:
            tips = await self.page.locator(".tipInfo, .layui-layer-msg, .sys-message").all_inner_texts()
            tips = [t.strip() for t in tips if t.strip()]
            if tips:
                await self._warn(f"登录页提示: {tips[:3]}")
        except Exception:
            pass

        await self._error("登录超时，检查账号密码")
        return False

    # ---- 通用 ----
    async def _wait_content(self):
        if not self.page:
            return
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
    # ---- 学习中心（2026-11 新版）----
    # 新版学习中心不再用「推荐/集团/单位/案例」+ 子tab 的旧结构，
    # 改成统一页面 user-study/index.html?tab=1/2/3/4 + 卡片列表分页。
    # 这里采用「页面内接口取列表 + 真实详情页学习」，既稳定又不绕过平台计时。

    async def _pace(self, min_sec: float = 1.0, max_sec: float = 2.0):
        """随机等待，降低请求频率，避免触发平台风控"""
        await asyncio.sleep(random.uniform(min_sec, max_sec))

    async def _open_study_hub(self, tab_key: str) -> bool:
        """打开学习中心对应 tab（recommend/group/unit/accident）

        推荐页 tab 会顺便抓取页面自己发出的 getStudyInfo 请求，
        从中拿到推荐分类 ID，避免写死平台参数。
        """
        if not self.page:
            return False
        tab_info = _STUDY_TABS.get(tab_key)
        if not tab_info:
            await self._warn(f"  未知学习分类: {tab_key}")
            return False
        tab_num = tab_info[0]
        base = self.cfg.get("base_url", "").rstrip("/")
        url = f"{base}{_STUDY_HUB}?tab={tab_num}"

        captured: dict = {}

        def on_request(req):
            """抓取推荐页的 getStudyInfo 请求体"""
            try:
                if tab_key == "recommend" and "getStudyInfo" in req.url and req.method == "POST":
                    body = req.post_data or ""
                    if body:
                        captured["body"] = json.loads(body)
            except Exception:
                pass

        self.page.on("request", on_request)
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await self._wait_content()
        except Exception as e:
            if self._running and self.page:
                await self._warn(f"  打开学习中心失败: {e}")
            return False
        finally:
            if self.page:
                self.page.remove_listener("request", on_request)

        body = captured.get("body") or {}
        if body.get("classId"):
            self._recommend_class_id = str(body["classId"])
        await self._pace(1.0, 2.0)
        return True

    async def _click_study_category(self, category: str):
        """点击集团课程下的二级分类（视频课程/专业课程），仅用于界面展示"""
        info = _STUDY_CATEGORIES.get(category)
        if not info or not info[1] or not self.page:
            return
        wanted = info[1]
        try:
            items = self.page.locator(".study-cats__item")
            for i in range(await items.count()):
                el = items.nth(i)
                text = (await el.text_content() or "").strip()
                if wanted in text:
                    cls = await el.get_attribute("class") or ""
                    if "active" not in cls:
                        await el.click()
                        await self._wait_content()
                    return
        except Exception:
            pass

    async def _ensure_study_tab(self, category: str) -> bool:
        """确保浏览器停在对应学习中心页面

        优先按真人路径：积分页 -> 点「去学习」-> 学习中心 tab -> 分类。
        点击失败时才回退直接 URL。
        """
        info = _STUDY_CATEGORIES.get(category)
        if not info or not self.page:
            return False
        tab_key = info[0]
        tab_info = _STUDY_TABS.get(tab_key)
        if not tab_info:
            return False
        tab_num = tab_info[0]
        marker = f"user-study/index.html?tab={tab_num}"

        # 已经在目标 tab：只切分类
        if marker in (self.page.url or ""):
            await self._click_study_category(category)
            return True

        # 1) 从积分页一步步点击进入
        base = self.cfg.get("base_url", "").rstrip("/")
        score_url = (f"{base}/src/apps/app-aqhb/src/components/studentWeb/"
                     "user-score/index.html?type=week")
        score_type = _STUDY_SCORE_TYPES.get(tab_key)
        try:
            await self.page.goto(score_url, wait_until="domcontentloaded", timeout=30000)
            await self._wait_content()
            btn = self.page.locator(f'li.task-item[data-type="{score_type}"] button').first
            if score_type and await btn.count() > 0:
                await self._info(f"  从积分页进入学习中心: {tab_key}")
                await btn.click()
                for _ in range(20):
                    await asyncio.sleep(0.5)
                    if "user-study/index.html" in (self.page.url or ""):
                        break
                await self._wait_content()
        except Exception as e:
            if self._running and self.page:
                await self._warn(f"  积分页进入失败: {e}")

        # 2) 还没到目标页面 → 回退直接 URL
        if marker not in (self.page.url or ""):
            if not await self._open_study_hub(tab_key):
                return False
        else:
            # 3) 目标 tab 没激活时点一下（从积分页进入通常已经激活）
            try:
                tab = self.page.locator(f'.study-tabs__item[data-tab="{tab_key}"]').first
                if await tab.count() > 0:
                    cls = await tab.get_attribute("class") or ""
                    if "active" not in cls:
                        await tab.click()
                        await self._wait_content()
            except Exception:
                pass

        await self._click_study_category(category)
        return True

    async def _fetch_json(self, path: str, method: str = "GET",
                          body: dict | None = None) -> dict | None:
        """在当前平台页面内调接口，自动带登录 Token

        path: 以 /api 开头的相对路径
        返回: 接口 JSON（失败返回 None）
        """
        if not self.page:
            return None
        try:
            result = await self.page.evaluate(
                """async ({path, method, body}) => {
                    let token = "";
                    try {
                        if (window.layui && layui.data) {
                            token = (layui.data("tgit") || {}).tgitToken || "";
                        }
                    } catch (e) {}
                    const opts = { method: method, headers: {} };
                    if (token) opts.headers["Tgittoken"] = token;
                    if (body !== null && body !== undefined) {
                        opts.headers["Content-Type"] = "application/json;charset=utf-8";
                        opts.body = JSON.stringify(body);
                    }
                    const resp = await fetch(path, opts);
                    const text = await resp.text();
                    try { return JSON.parse(text); } catch (e) { return {__raw: text.slice(0, 500)}; }
                }""",
                {"path": path, "method": method, "body": body},
            )
            return result if isinstance(result, dict) else None
        except Exception as e:
            if self._running and self.page:
                await self._warn(f"  接口调用失败 {path}: {e}")
            return None

    async def _get_recommend_class_id(self) -> str | None:
        """获取推荐页分类 ID（优先从页面请求抓，失败用兜底常量）"""
        if self._recommend_class_id:
            return self._recommend_class_id
        if await self._open_study_hub("recommend") and self._recommend_class_id:
            return self._recommend_class_id
        # 兜底：2026-11 实测推荐分类 ID
        self._recommend_class_id = "3504173599051354112"
        return self._recommend_class_id

    async def _get_study_categories(self, tab_key: str) -> list[dict]:
        """取学习中心某 tab 的分类列表（group/unit/accident）"""
        type_map = {"group": 1, "unit": 2, "accident": 3}
        type_num = type_map.get(tab_key)
        if not type_num:
            return []
        if tab_key in self._study_class_cache:
            return self._study_class_cache[tab_key]

        resp = await self._fetch_json(f"/api/aqhb/courseClass/list?type={type_num}")
        if not resp or not (str(resp.get("code")) == "2000" or resp.get("success")):
            # 页面上下文可能未就绪，打开学习中心后重试一次
            if await self._open_study_hub(tab_key):
                resp = await self._fetch_json(f"/api/aqhb/courseClass/list?type={type_num}")
        if not resp:
            return []
        data = resp.get("data") or []
        if isinstance(data, list):
            self._study_class_cache[tab_key] = data
            return data
        return []

    async def _resolve_study_category(self, category: str) -> tuple[str, str | None, str | None]:
        """解析学习任务分类，返回 (tab_key, class_id, 分类名称)"""
        info = _STUDY_CATEGORIES.get(category)
        if not info:
            return "", None, None
        tab_key, class_name = info
        if tab_key == "recommend":
            return "recommend", None, "推荐"

        cats = await self._get_study_categories(tab_key)
        if not cats:
            return tab_key, None, class_name

        chosen = None
        if class_name:
            for c in cats:
                if class_name in (c.get("wf_classification_name") or ""):
                    chosen = c
                    break
        if chosen is None:
            chosen = cats[0]
        return tab_key, str(chosen.get("id") or ""), (chosen.get("wf_classification_name") or class_name or "")

    async def _fetch_study_cards(self, category: str, page_num: int = 1,
                                 page_size: int = 10) -> tuple[list[dict], int]:
        """通过页面内接口取卡片列表，返回 (cards, total)

        cards 统一字段：kind/id/title/learned/ctype/type
        """
        info = _STUDY_CATEGORIES.get(category)
        if not info:
            return [], 0
        tab_key = info[0]

        if tab_key == "recommend":
            class_id = await self._get_recommend_class_id()
            body = {
                "classId": class_id, "orgId": "", "wfCourseName": "",
                "pageNum": page_num, "pageSize": page_size,
            }
            resp = await self._fetch_json(
                "/api/aqhb/study/studentFrontPag/getStudyInfo", "POST", body)
            if not resp or not (str(resp.get("code")) == "2000" or resp.get("success")):
                # 页面上下文失效：重开学习中心再试一次
                if await self._open_study_hub("recommend"):
                    body["classId"] = self._recommend_class_id or class_id
                    resp = await self._fetch_json(
                        "/api/aqhb/study/studentFrontPag/getStudyInfo", "POST", body)
            if not resp:
                return [], 0
            data = resp.get("data") or {}
            rows = data.get("rows") or []
            total = _to_int(data.get("total"))
            cards = []
            for row in rows:
                rid = row.get("id")
                if not rid:
                    continue
                cards.append({
                    "kind": "recommend",
                    "id": str(rid),
                    "title": (row.get("content_subject") or "").strip(),
                    "learned": str(row.get("is_learned")) in ("1", "True", "true"),
                    "ctype": "recommend",
                    "type": 1 if row.get("field8") else 0,
                })
            return cards, total

        # 集团课程 / 单位课程 / 事故案例
        tab_key, class_id, _class_label = await self._resolve_study_category(category)
        if not class_id:
            return [], 0
        body = {
            "returnAllData": True, "wfCourseClass": class_id, "wfSourse": "",
            "wfCourseName": "", "pageNum": page_num, "pageSize": page_size,
            "wfCourseStatus": "是",
        }
        resp = await self._fetch_json(
            "/api/aqhb/study/studentFrontPag/getSourceCourse", "POST", body)
        if not resp or not (str(resp.get("code")) == "2000" or resp.get("success")):
            if await self._open_study_hub(tab_key):
                # 分类缓存可能已失效，重新解析一次
                self._study_class_cache.pop(tab_key, None)
                _, class_id, _class_label = await self._resolve_study_category(category)
                body["wfCourseClass"] = class_id
                resp = await self._fetch_json(
                    "/api/aqhb/study/studentFrontPag/getSourceCourse", "POST", body)
        if not resp:
            return [], 0
        data = resp.get("data") or {}
        rows = data.get("rows") or []
        total = _to_int(data.get("total"))
        cards = []
        for row in rows:
            rid = row.get("id")
            if not rid:
                continue
            cards.append({
                "kind": "course",
                "id": str(rid),
                "title": (row.get("wfCourseName") or "").strip(),
                "learned": str(row.get("isLearned")) in ("1", "True", "true"),
                "ctype": tab_key,
                "type": 0,
                "hours": row.get("wfCourseHours") or "",
                "class_name": row.get("wfCourseClass") or "",
            })
        return cards, total

    async def _find_next_study_card(self, category: str,
                                    max_pages: int = 20) -> dict | None:
        """分页找第一张未学习且本次未处理过的卡片"""
        page_size = 10
        for page_num in range(1, max_pages + 1):
            cards, total = await self._fetch_study_cards(category, page_num, page_size)
            if not cards:
                return None
            for card in cards:
                if card["learned"]:
                    continue
                if not card["id"] or card["id"] in self._seen_courseids:
                    continue
                return card
            if len(cards) < page_size:
                break
            if total and page_num * page_size >= total:
                break
            await self._pace(0.8, 1.5)
        return None

    async def _open_study_card(self, card: dict) -> bool:
        """打开卡片详情页：优先点击页面上的真实卡片，找不到再回退 URL"""
        if not self.page:
            return False

        # 1) 优先 UI 点击，和真人操作一致
        try:
            if card["kind"] == "recommend":
                sel = f'li[data-id="{card["id"]}"]'
            else:
                sel = f'li.course-item[courseid="{card["id"]}"]'
            el = self.page.locator(sel).first
            if await el.count() > 0:
                await el.scroll_into_view_if_needed()
                await el.click()
                for _ in range(20):
                    await asyncio.sleep(0.5)
                    url_now = self.page.url or ""
                    if "detail-command.html" in url_now or "courseDetail.html" in url_now:
                        await self._wait_content()
                        return True
        except Exception as e:
            if self._running and self.page:
                await self._warn(f"  点击卡片失败: {e}")

        # 2) 卡片不在当前列表（翻页后的数据）→ 回退直接 URL
        base = self.cfg.get("base_url", "").rstrip("/")
        if card["kind"] == "recommend":
            title = quote(card.get("title") or "", safe="")
            url = (f"{base}{_RECOMMEND_DETAIL}?id={quote(card['id'])}"
                   f"&type={card.get('type', 0)}&title={title}&ctype=recommend")
        else:
            url = (f"{base}{_COURSE_DETAIL}?courseid={quote(card['id'])}"
                   f"&isStu=0&ctype={card.get('ctype', 'group')}")
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await self._wait_content()
            return True
        except Exception as e:
            if self._running and self.page:
                await self._warn(f"  打开学习内容失败: {e}")
            return False

    async def _is_study_completed(self) -> bool:
        """判断当前学习页是否已完成"""
        if not self.page:
            return False
        try:
            text = await self.page.evaluate(
                """() => {
                    const selectors = [".text-dura", ".rest-time", ".head-dura"];
                    return selectors.map(s => {
                        const el = document.querySelector(s);
                        return el ? (el.innerText || el.textContent || "").trim() : "";
                    }).join("|");
                }"""
            )
            return "已完成" in (text or "")
        except Exception:
            return False

    async def _start_learning_if_needed(self):
        """页面有视频才点播放；PDF/图片/音频由平台自动计时"""
        if not self.page:
            return
        try:
            has_video = await self.page.evaluate(
                """() => {
                    const visible = (el) => {
                        if (!el) return false;
                        const s = getComputedStyle(el);
                        const r = el.getBoundingClientRect();
                        return s.display !== "none" && s.visibility !== "hidden" &&
                               r.width > 0 && r.height > 0;
                    };
                    return visible(document.querySelector("#myVideo")) ||
                           visible(document.querySelector(".wechat-play-button")) ||
                           visible(document.querySelector("video"));
                }"""
            )
            if has_video:
                await self._click_play()
        except Exception:
            pass

    async def _learn_next_card(self, category: str, label: str) -> bool:
        """找一张未学卡片并完整学完，返回是否新增完成"""
        for _attempt in range(3):
            card = await self._find_next_study_card(category)
            if not card:
                return False
            cid = card["id"]
            self._seen_courseids.add(cid)
            await self._info(f"  打开[{label}]: {card['title'][:50]} (id={cid[:12]})")
            if not await self._open_study_card(card):
                continue
            if not self._running or not self.page:
                return False
            if await self._is_study_completed():
                await self._info("  平台已标记完成，跳过")
                continue
            await self._start_learning_if_needed()
            await self._info("  等待学习完成...")
            done = await self._wait_study_done()
            if done:
                await self._info("  ✓ 学习完成")
                return True
            if not self._running or not self.page:
                return False
            await self._warn("  学习未完成（超时或页面异常）")
            return False
        return False

    async def _learn_cards(self, category: str, count: int) -> int:
        """按分类学习 count 个内容，返回完成数量"""
        info = _STUDY_CATEGORIES.get(category)
        label = (info[1] if info and info[1] else
                 {"recommend": "推荐", "group": "集团课程", "unit": "单位课程",
                  "accident": "事故案例"}.get(category, category))
        # 先让浏览器停在对应学习中心页面，避免用户看到窗口“没动静”
        if not await self._ensure_study_tab(category):
            await self._warn(f"{label}: 无法打开学习中心页面")
            return 0
        points = 0
        empty_rounds = 0
        for i in range(count):
            if not self._running:
                break
            await self._info(f"{label} [{i+1}/{count}]")
            ok = await self._learn_next_card(category, label)
            if not self._running or not self.page:
                break
            if ok:
                points += 1
                empty_rounds = 0
                # 学完一个后回到学习中心，下一张优先用页面点击进入
                if self._running and self.page:
                    await self._ensure_study_tab(category)
            else:
                empty_rounds += 1
                if empty_rounds >= 2:
                    await self._warn(f"{label}: 没有可学习的未完成内容")
                    break
                await self._pace(3.0, 5.0)
            await self._pace(2.0, 3.5)
        return points

    # ---- 刷图文 ----
    async def study_articles(self, count: int = 5, category: str = "recommend") -> int:
        """刷图文：推荐页；推荐无可读时回退集团课程-专业课程"""
        if category == "recommend":
            await self._info("→ 图文: 推荐页")
            points = await self._learn_cards("recommend", count)
            if points == 0 and self._running:
                await self._info("→ 推荐页无可读，回退到集团课程-专业课程(图文)")
                points += await self._learn_cards("group_pro", count)
        elif category == "group_pro":
            await self._info("→ 图文: 集团课程-专业课程")
            points = await self._learn_cards("group_pro", count)
        else:
            points = await self._learn_cards(category, count)
        await self._info(f"图文结束: +{points}分")
        return points

    # ---- 刷视频 ----
    async def study_videos(self, tab: int = 2, count: int = 5) -> int:
        """刷视频：2=集团课程-视频课程，3=单位课程，4=事故案例"""
        category_map = {2: "group_video", 3: "unit", 4: "accident"}
        label_map = {2: "集团课程-视频课程", 3: "单位课程", 4: "事故案例"}
        category = category_map.get(tab)
        if not category:
            await self._warn(f"  未知视频分类 tab={tab}")
            return 0
        label = label_map[tab]
        await self._info(f"→ 视频: {label}")
        points = await self._learn_cards(category, count)
        await self._info(f"视频({label})结束: +{points}分")
        return points

    async def _click_play(self):
        """点击视频播放按钮 + JS video.play() 兜底

        实测平台播放器结构（wechat-video 自定义播放器）：
          .wechat-play-button (覆盖层大播放按钮) → 点击触发播放
          .wechat-control-btn.wechat-play-pause-btn (底部控制栏)
          video#myVideo (controls=false, 自定义控件)
        策略：force=True 绕过 Playwright actionability 检查（覆盖层可能拦截）
        """
        if not self.page:
            return
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
        """等待学习倒计时，显示进度，响应停止信号

        新版完成标志：.text-dura / .rest-time 显示「已完成」。
        视频页只有播放时才计时，剩余时间长时间不动会重试播放。
        """
        last_rest = ""
        last_change = time.time()
        last_retry = 0.0
        for sec in range(timeout):
            if not self._running:
                await self._info("  收到停止信号，中断等待")
                return False
            if not self.page:
                await self._info("  浏览器已关闭，停止等待")
                return False
            await asyncio.sleep(1)
            try:
                if await self._is_study_completed():
                    await self._info(f"  等待 {sec+1}秒后完成")
                    return True

                rest_text = await self.page.evaluate(
                    """() => {
                        const el = document.querySelector(".text-dura, .rest-time, .head-dura");
                        return el ? (el.innerText || el.textContent || "").trim() : "";
                    }"""
                ) or ""
                if rest_text != last_rest:
                    last_rest = rest_text
                    last_change = time.time()

                # 视频页倒计时长时间不动 → 可能暂停了，重试播放
                if (last_rest and time.time() - last_change > 30
                        and time.time() - last_retry > 60):
                    last_retry = time.time()
                    await self._info("  倒计时未变化，重试播放...")
                    await self._start_learning_if_needed()

                # 每15秒报告进度
                if (sec + 1) % 15 == 0:
                    suffix = f" 剩余:{last_rest}" if last_rest else ""
                    await self._info(f"  学习中... 已等{sec+1}秒{suffix}")
            except Exception as e:
                if not self._running or not self.page:
                    await self._info("  浏览器已关闭，停止等待")
                    return False
                await self._warn(f"  等待学习状态异常: {e}")
        await self._warn(f"  等待 {timeout}秒 超时")
        return False

    # ---- 每日练习 ----
    async def _wait_exercise_ready(self, timeout: int = 20) -> bool:
        """等待每日练习题目卡片出现"""
        if not self.page:
            return False
        for _ in range(max(1, timeout * 2)):
            if not self._running or not self.page:
                return False
            try:
                if await self.page.locator(".ue-question-card, .ue-no-data").count() > 0:
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.5)
        return False

    async def _click_next_question(self, old_title: str = "") -> bool:
        """点击「下一题」并等待题干变化

        新版每日练习按钮文字从「刷新」改为「下一题」，class=ue-refresh-btn。
        """
        if not self.page or not self._running:
            return False
        try:
            btn = self.page.locator(".ue-refresh-btn").first
            if await btn.count() == 0:
                btn = self.page.locator('button:has-text("下一题")').first
            if await btn.count() == 0:
                await self._warn("  未找到「下一题」按钮")
                return False
            await btn.click()
        except Exception as e:
            if self._running and self.page:
                await self._warn(f"  点击「下一题」失败: {e}")
            return False

        old_norm = _norm_text(old_title)
        for _ in range(30):
            await asyncio.sleep(0.5)
            try:
                if await self.page.locator(".ue-no-data").count() > 0:
                    return True
                title_el = self.page.locator(".ue-question-title").first
                if await title_el.count() > 0:
                    new_title = (await title_el.text_content() or "").strip()
                    if _norm_text(new_title) != old_norm:
                        return True
            except Exception:
                pass
        await self._warn("  等待下一题超时（题干未变化）")
        return False

    # ---- 每日练习 ----
    async def do_exercises(self, api_key: str = "", max_questions: int = 20) -> dict:
        base = self.cfg.get("base_url", "").rstrip("/")
        url = f"{base}/src/apps/app-aqhb/src/components/studentWeb/user-exercise/index.html?tab=2"
        result = {"points": 0, "correct": 0, "wrong": 0}

        await self._info("→ 每日练习")
        if not self.page:
            await self._warn("浏览器未启动，无法执行每日练习")
            return result

        # 本地日计数检查（替代原来读平台周累计 #dtStudyWeek 的错误逻辑）
        today_pts = self._get_today_exercise_points()
        if today_pts >= 3:
            await self._info(f"今日练习已获 {today_pts} 分（已达日上限），跳过")
            return result

        await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await self._wait_content()
        await self._wait_exercise_ready()
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
            for i in range(max_questions):
                if not self._running or not self.page:
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

                # 题目做完 / 加载失败
                try:
                    if await self.page.locator(".ue-no-data").count() > 0:
                        await self._info("  今日暂无练习题目，结束")
                        break
                except Exception:
                    pass

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

                # 用 JS 从页面提取选项
                q_text, opts = await self._extract_options()

                if opts:
                    advanced = await self._solve_multiple_choice(
                        q_text, opts, qtype, api_key, submit_holder, result)
                elif "填空" in qtype or "简答" in qtype:
                    advanced = await self._solve_fill_blank(
                        q_text, qtype, api_key, submit_holder, result)
                else:
                    await self._info(f"  题型: {qtype}，跳过")
                    advanced = await self._click_next_question(q_text)

                if not advanced:
                    await self._warn("  无法获取下一题，停止")
                    break
                await self._wait_exercise_ready()

        finally:
            self.page.remove_listener("response", on_resp)

        await self._info(f"每日练习: 对{result['correct']} 错{result['wrong']} 得{result['points']}分")
        return result

    async def _extract_options(self) -> tuple[str, list[dict]]:
        """用 JS 提取每日练习的题干和选项"""
        if not self.page:
            return "", []
        try:
            r = await self.page.evaluate("""() => {
                let question = '';

                // 清除旧标记
                document.querySelectorAll('[data-ow-opt]').forEach(el => el.removeAttribute('data-ow-opt'));

                // 每日练习选项（新版仍是 .ue-option-item）
                let items = document.querySelectorAll('.ue-option-item, .option-item, .answer-item, li[class*="option"]');

                // 兜底：radio input + label（防止平台再改版）
                if (items.length === 0) {
                    const scope = document.querySelector('.ue-question-card') || document.body;
                    const radios = scope.querySelectorAll('input[type="radio"]');
                    if (radios.length > 0) {
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

                // 题干：必须在题目卡片内按优先级找，避免误抓页面顶部的 .title（如「每日练习」）
                const card = document.querySelector('.ue-question-card');
                const scope = card || document;
                const selectors = [
                    '.ue-question-title',
                    '[class*="question-title"]',
                    '.question-title',
                    '.subject-text',
                    '.topic',
                    '.exam-question',
                    'h3',
                    'h4',
                    '.title',
                    '[class*="question"]'
                ];
                for (const sel of selectors) {
                    const el = scope.querySelector(sel);
                    if (el) {
                        question = el.innerText || el.textContent || '';
                        break;
                    }
                }

                return {question: question.trim(), options: opts};
            }""")

            question = r.get("question", "") if r else ""
            raw = r.get("options", []) if r else []

            options = []
            for i, opt in enumerate(raw):
                label = chr(ord("A") + i)
                el = self.page.locator(f'[data-ow-opt="{label}"]')
                # 拆出平台显示的字母 + 纯内容（内容锚定用，平台会打乱选项顺序）
                display_letter, content = split_option_text(opt["text"])
                options.append({
                    "element": el,
                    "text": opt["text"],              # 原始文本（含平台标签，用于日志）
                    "display_letter": display_letter,  # 平台显示的字母（若有）
                    "wfLineOption": label,            # 我们的位置字母
                    "wfLineContent": content,         # 纯内容（喂 AI / 入库锚定）
                })

            return question, options
        except Exception as e:
            await self._warn(f"  提取选项异常: {e}")
            return "", []

    # ---- AI 配置读取 ----
    def _ai_params(self) -> tuple:
        """统一读取 AI 配置：返回 (api_key, base_url, model, provider)"""
        provider = self.cfg.get("ai_provider", "deepseek")
        return (self.cfg.get("ai_key", ""), self.cfg.get("ai_base_url", ""),
                self.cfg.get("ai_model", ""), provider)

    # ---- 选择题解答 ----
    async def _solve_multiple_choice(self, q_text: str, opts: list, qtype: str,
                                     api_key: str, submit_holder: list, result: dict) -> bool:
        """解答选择题：题库 → AI → 点击 → 提交 → 入库 → 下一题"""
        if not self.page or not self._running:
            return False
        await self._info(f"  题型: {qtype} | 题干: {q_text[:60] if q_text else '(空)'}...")
        await self._info(f"  选项: {[o['text'][:20] for o in opts]}")

        # 诊断日志：帮助排查"只选A不调API"问题
        if not q_text:
            await self._warn("  ⚠ 题干为空！页面元素可能已改版，无法匹配题库也无法调API")
        if not api_key:
            await self._warn("  ⚠ API Key 为空！不会调用 AI，将默认选A")

        # 先登记题目到题库（答案待定）
        if q_text and opts:
            bank_key = record_answer(q_text, opts, "")  # 空答案 = 仅登记
            if bank_key:
                await self._info(f"  题目已登记: {bank_key[:12]}...")

        # 判断答案：题库 → AI
        answer = lookup_question(q_text, opts) if q_text else None
        if answer:
            await self._info(f"  题库命中 → {answer}")
        elif q_text and api_key:
            await self._info("  题库未命中，调用 AI...")
            ai_key, ai_base, ai_model, provider = self._ai_params()
            answer = query_ai(f"<p>{q_text}</p>", opts, ai_key, ai_base,
                              question_type=qtype, provider=provider, model=ai_model)
            if answer:
                await self._info(f"  AI → {answer}")
            else:
                await self._warn("  AI 返回空")

        if not answer:
            if not api_key:
                await self._warn("  未配置 AI API Key")
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

        # 提交（新版按钮 class=ue-submit-btn）
        submit_holder[0] = None
        submit_btn = self.page.locator(".ue-submit-btn").first
        if await submit_btn.count() == 0:
            submit_btn = self.page.locator('button:has-text("提交答案")').first
        if await submit_btn.count() > 0:
            if await submit_btn.get_attribute("disabled") is not None:
                await self._warn("  提交按钮已禁用")
            else:
                await submit_btn.click()
                await asyncio.sleep(2)
        else:
            await self._warn("  未找到「提交答案」按钮")

        # 提取正确答案（三路并行）
        correct = None
        is_correct = False

        # 方式1: submitAnswer API 响应（最权威）
        sd = submit_holder[0] or {}
        correct = extract_correct_from_response(sd)
        if sd.get("data"):
            d = sd["data"]
            # trueAnswer 字段 + wfAnswer 判断对错
            ta = d.get("trueAnswer", "")
            if ta and len(str(ta)) <= 2:
                correct = str(ta).strip().upper()
            # wfAnswer: "正确" or "错误"
            wf = d.get("wfAnswer", "")
            if wf == "正确":
                is_correct = True

        # 方式2: 检测"回答正确" toast（新版仍用 layer.msg）
        if not is_correct:
            await asyncio.sleep(0.5)
            try:
                toast = self.page.locator('.layui-layer-msg, .layui-layer-dialog:has-text("正确")')
                if await toast.count() > 0:
                    toast_text = await toast.text_content() or ""
                    if "正确" in toast_text:
                        is_correct = True
                        await self._info("  检测到'回答正确'弹窗")
            except Exception:
                pass

        # 方式3: 页面 DOM — 新版错题选项带 .ue-option-correct 类
        if not correct:
            try:
                correct = await self.page.evaluate("""() => {
                    const el = document.querySelector('.ue-option-item.ue-option-correct');
                    return el ? (el.getAttribute('data-option') || null) : null;
                }""")
            except Exception:
                pass

        # 记录结果 + 更新题库（内容锚定：平台会打乱选项顺序，字母不可靠）
        # 把「我们点的字母」还原成选项内容
        clicked_content = letters_to_content(answer, opts) if opts else ""

        # 内容相等判定（多选用集合比较，与顺序无关）
        def _same_content(a: str, b: str) -> bool:
            return bool(a) and bool(b) and set(a.split("||")) == set(b.split("||"))

        if correct:
            # trueAnswer 字母坐标系未知：优先按平台显示字母还原内容，其次按我们的位置字母
            correct_content = letters_to_content(str(correct), opts, prefer_display=True) if opts else str(correct)
            # 系统返回了正确答案 → 更新题库（入库前会再转一次内容锚定）
            if q_text and opts:
                record_answer(q_text, opts, correct_content, source="submitAnswer")
                await self._info(f"  题库已更新: {correct_content}")

            # 判定对错：能还原成内容就按内容比，否则退回字母比
            if _same_content(clicked_content, correct_content):
                result["correct"] += 1
                result["points"] += 1
                self._add_today_exercise_point()
                await self._info(f"  ✓ 答对! ({correct_content})")
            elif str(correct).strip().upper() == str(answer).strip().upper():
                result["correct"] += 1
                result["points"] += 1
                self._add_today_exercise_point()
                await self._info(f"  ✓ 答对! ({correct})")
            else:
                result["wrong"] += 1
                await self._warn(f"  ✗ 答错 选{clicked_content or answer} 正确{correct_content or correct}")
        elif is_correct:
            # toast/接口显示正确 → 我们点的内容就是正确答案
            if q_text and opts:
                record_answer(q_text, opts, clicked_content or answer, source="submitAnswer")
            result["correct"] += 1
            result["points"] += 1
            self._add_today_exercise_point()
            await self._info("  ✓ 答对! (wfAnswer=正确)")
        else:
            await self._warn("  无法确认答案，跳过入库")

        # 提交后点「下一题」获取新题（平台不会自动跳转）
        return await self._click_next_question(q_text)

    # ---- 填空题 / 简答题解答 ----
    async def _solve_fill_blank(self, q_text: str, qtype: str,
                                api_key: str, submit_holder: list, result: dict) -> bool:
        """解答填空题/简答题：AI 生成答案 → 填入 → 提交 → 入库 → 下一题"""
        if not self.page or not self._running:
            return False
        await self._info(f"  题型: {qtype} | 题目: {q_text[:80] if q_text else '(空)'}...")

        if not api_key:
            await self._warn("  填空题/简答题需要 AI API Key")
            return await self._click_next_question(q_text)

        is_essay = "简答" in (qtype or "")
        try:
            if is_essay:
                blank_count = 1
            else:
                blank_count = await self.page.evaluate(
                    """() => document.querySelectorAll('.ue-fill-input').length"""
                )
                if not blank_count:
                    blank_count = await self.page.evaluate(
                        """() => document.querySelectorAll(
                            '.ue-fill-list input, .ue-fill-item input, input[type="text"]').length"""
                    )
        except Exception:
            blank_count = 1
        if not blank_count:
            blank_count = 1

        await self._info(f"  检测到 {blank_count} 个空，调用 AI...")
        ai_key, ai_base, ai_model, provider = self._ai_params()
        answers = query_ai_fill(f"<p>{q_text}</p>", blank_count, ai_key, ai_base,
                                question_type=qtype or ("简答题" if is_essay else "填空题"),
                                provider=provider, model=ai_model)

        if not answers:
            await self._warn("  AI 填空返回空，跳过")
            return await self._click_next_question(q_text)

        await self._info(f"  AI → {answers}")

        # 填入答案
        try:
            if is_essay:
                box = self.page.locator(".ue-essay-input").first
                if await box.count() > 0:
                    await box.fill(answers[0])
            else:
                inputs = self.page.locator(".ue-fill-input")
                n = await inputs.count()
                if n == 0:
                    inputs = self.page.locator(
                        '.ue-fill-list input, .ue-fill-item input, input[type="text"]')
                    n = await inputs.count()
                for j, ans in enumerate(answers[:max(n, 1)]):
                    if j < n:
                        await inputs.nth(j).fill(ans)
            await self._info(f"  已填入 {len(answers)} 个答案")
        except Exception as e:
            await self._warn(f"  填入失败: {e}")

        await asyncio.sleep(0.5)

        # 提交
        submit_holder[0] = None
        submit_btn = self.page.locator(".ue-submit-btn").first
        if await submit_btn.count() == 0:
            submit_btn = self.page.locator('button:has-text("提交答案")').first
        if await submit_btn.count() > 0:
            if await submit_btn.get_attribute("disabled") is not None:
                await self._warn("  提交按钮已禁用")
                return await self._click_next_question(q_text)
            await submit_btn.click()
            await asyncio.sleep(2)
        else:
            await self._warn("  未找到「提交答案」按钮")
            return await self._click_next_question(q_text)

        # 从 API 响应判断对错
        sd = submit_holder[0] or {}
        d = sd.get("data") or {}
        wf = (d.get("wfAnswer") or "").strip()
        correct_text = str(d.get("trueAnswer") or "").strip()

        # 兜底：页面标记
        if not wf:
            try:
                wrong_n = await self.page.locator(".ue-fill-wrong, .ue-option-wrong, .ue-essay-correct-answer").count()
                right_n = await self.page.locator(".ue-fill-correct, .ue-option-correct").count()
                if right_n > 0 and wrong_n == 0:
                    wf = "正确"
                elif wrong_n > 0:
                    wf = "错误"
            except Exception:
                pass

        if wf == "正确":
            result["correct"] += 1
            result["points"] += 1
            self._add_today_exercise_point()
            if q_text:
                stored = correct_text or (answers[0] if answers else "")
                record_answer(q_text, [], stored, source="submitAnswer")
            await self._info("  ✓ 答对!")
        elif wf == "错误":
            result["wrong"] += 1
            await self._warn(f"  ✗ 答错，正确答案: {correct_text or '(未知)'}")
        else:
            await self._warn("  无法确认填空/简答结果，不计分")

        return await self._click_next_question(q_text)

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

    @property
    def running(self) -> bool:
        return self._running

    @running.setter
    def running(self, value: bool):
        self._running = value
