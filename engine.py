"""
调度引擎 —— 策略决策、任务队列、状态管理
根据配置的策略优先级自动执行刷分任务，通过 WebSocket 推送实时状态
"""
import asyncio
from datetime import datetime
from executor import TaskExecutor


class OverWallEngine:
    """OverWall 核心调度引擎"""

    def __init__(self, config: dict, log_callback=None, status_callback=None):
        """
        config: 完整配置
        log_callback: async (level, message) -> None
        status_callback: async (status_dict) -> None  推送完整状态到前端
        """
        self.cfg = config
        self.executor = TaskExecutor(config, log_callback=log_callback)
        self._log = log_callback or (lambda lvl, msg: None)
        self._status = status_callback or (lambda s: None)

        # 运行状态
        self._running = False
        self._paused = False
        self._stop_requested = False

        # 积分追踪
        self.weekly_points = 0
        self.weekly_target = config.get("weekly_target", 30)
        self.today_exercise_points = 0
        self.session_points = 0       # 本次运行获得的总分
        self.session_articles = 0     # 本次运行图文数
        self.session_videos = 0       # 本次运行视频数
        self.session_exercise_correct = 0
        self.session_exercise_wrong = 0

        # 任务日志（前端展示用）
        self.task_history: list[dict] = []

        self._page = None

    async def _info(self, msg: str):
        self._add_history("info", msg)
        await self._log("info", msg)

    async def _warn(self, msg: str):
        self._add_history("warn", msg)
        await self._log("warn", msg)

    async def _error(self, msg: str):
        self._add_history("error", msg)
        await self._log("error", msg)

    def _add_history(self, level: str, msg: str):
        """添加日志到历史记录"""
        self.task_history.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "message": msg,
        })
        # 只保留最近 200 条
        if len(self.task_history) > 200:
            self.task_history = self.task_history[-200:]

    async def _push_status(self):
        """推送当前完整状态到前端"""
        from question_bank import bank_stats as _bank_stats
        bank = _bank_stats()

        await self._status({
            "running": self._running,
            "paused": self._paused,
            "weekly_points": self.weekly_points,
            "weekly_target": self.weekly_target,
            "session_points": self.session_points,
            "session_articles": self.session_articles,
            "session_videos": self.session_videos,
            "session_exercise_correct": self.session_exercise_correct,
            "session_exercise_wrong": self.session_exercise_wrong,
            "question_bank_total": bank["total"],
            "task_history": self.task_history[-50:],  # 只推最近50条
        })

    # ---- 主循环 ----
    async def run(self):
        """
        主运行循环 —— 按策略优先级自动执行
        策略优先级: exercise(每日练习) > article(图文) > video(视频)
        """
        self._running = True
        self._stop_requested = False
        self._paused = False

        await self._info("===== OverWall 刷分引擎启动 =====")
        await self._push_status()

        # 1. 启动浏览器
        await self._info("正在启动浏览器...")
        page = await self.executor.start_browser()
        if not page:
            await self._error("浏览器启动失败，引擎中止")
            self._running = False
            await self._push_status()
            return
        self._page = page

        # ⚠️ 关键：激活 executor 的运行状态
        self.executor.running = True

        try:
            # 2. 登录（先验证Cookie，失效再走登录流程）
            await self._info("步骤1: 验证登录状态...")
            logged_in = await self.executor.login()
            if not logged_in:
                await self._error("登录失败 — 请在浏览器窗口中手动完成登录")
                return
            await self._info("登录成功，已进入平台首页")

            # 3. 获取当前周积分
            self.weekly_points = await self.executor.get_weekly_points()
            self.session_points = 0
            await self._push_status()

            # 4. 主循环
            strategy = self.cfg.get("strategy_order", ["exercise", "article", "video"])
            api_key = self.cfg.get("deepseek_api_key", "")

            while self._running and not self._stop_requested:
                while self._paused and not self._stop_requested:
                    await asyncio.sleep(1)
                if self._stop_requested:
                    break

                # 每轮刷新周积分
                pts = await self.executor.get_weekly_points()
                if pts > self.weekly_points:
                    self.weekly_points = pts

                if self.weekly_points >= self.weekly_target:
                    await self._info(f"🎉 已达目标: {self.weekly_points}/{self.weekly_target}")
                    break

                remaining = self.weekly_target - self.weekly_points
                await self._info(f"积分 {self.weekly_points}/{self.weekly_target}，还需 {remaining}")

                action_taken = False
                for task_type in strategy:
                    if not self._running or self._stop_requested or self.weekly_points >= self.weekly_target:
                        break

                    if task_type == "exercise":
                        action_taken |= await self._run_exercise(api_key)
                    elif task_type == "mock_exam":
                        action_taken |= await self._run_mock_exam(api_key)
                    elif task_type == "article":
                        action_taken |= await self._run_articles(remaining)
                    elif task_type == "video":
                        action_taken |= await self._run_videos(remaining)

                    if action_taken:
                        break

                if not action_taken:
                    await self._warn("无可执行任务，30秒后重试")
                    await asyncio.sleep(30)

        except Exception as e:
            await self._error(f"引擎运行异常: {e}")
        finally:
            await self.executor.stop_browser()
            self._running = False
            await self._info("===== OverWall 刷分引擎已停止 =====")
            await self._push_status()

    async def _run_exercise(self, api_key: str) -> bool:
        """执行每日练习，返回是否成功获得积分"""
        needed = self.weekly_target - self.weekly_points
        if needed <= 0:
            return False

        await self._info("→ 执行策略: 每日练习")
        result = await self.executor.do_exercises(api_key)
        self.session_exercise_correct += result["correct"]
        self.session_exercise_wrong += result["wrong"]
        self.session_points += result["points"]
        self.weekly_points += result["points"]
        await self._push_status()
        return result["points"] > 0

    async def _run_mock_exam(self, api_key: str) -> bool:
        """执行模拟考试，返回是否成功获得积分"""
        await self._info("→ 执行策略: 模拟考试")
        result = await self.executor.do_mock_exam(api_key)
        self.session_exercise_correct += result["correct"]
        self.session_points += result["points"]
        self.weekly_points += result["points"]
        await self._push_status()
        return result["points"] > 0

    async def _run_articles(self, remaining: int) -> bool:
        """执行图文学习，每次刷5个"""
        if remaining <= 0:
            return False
        count = min(5, remaining)
        await self._info(f"→ 执行策略: 图文学习 ({count}个)")
        points = await self.executor.study_articles(count)
        self.session_articles += points
        self.session_points += points
        self.weekly_points += points
        await self._push_status()
        return points > 0

    async def _run_videos(self, remaining: int) -> bool:
        """执行视频学习，每次刷3个（比图文慢）"""
        if remaining <= 0:
            return False
        count = min(3, remaining)
        await self._info(f"→ 执行策略: 视频学习 ({count}个)")

        # 回退链：集团课程 → 单位课程 → 案例学习
        points = await self.executor.study_videos(tab=2, count=count)
        if points == 0:
            points = await self.executor.study_videos(tab=3, count=count)
        if points == 0:
            points = await self.executor.study_videos(tab=4, count=count)

        self.session_videos += points
        self.session_points += points
        self.weekly_points += points
        await self._push_status()
        return points > 0

    # ---- 控制接口 ----
    def pause(self):
        """暂停引擎"""
        self._paused = True
        if self.executor:
            self.executor.running = False

    def resume(self):
        """恢复引擎"""
        self._paused = False
        if self.executor:
            self.executor.running = True

    def stop(self):
        """停止引擎"""
        self._stop_requested = True
        self._running = False
        if self.executor:
            self.executor.running = False

    @property
    def running(self) -> bool:
        return self._running
