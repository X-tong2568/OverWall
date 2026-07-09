"""
OverWall 主程序 —— Flask 服务 + 状态轮询
提供 Web UI + API 接口
"""
import json
import os
import sys
import threading
from datetime import datetime

from flask import Flask, render_template, request, jsonify

from config import load_config, save_config
from engine import OverWallEngine
from question_bank import load_bank, save_bank, bank_stats

# PyInstaller 打包后模板/静态文件路径处理
import sys as _sys
if getattr(_sys, 'frozen', False):
    _base = _sys._MEIPASS
else:
    _base = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=os.path.join(_base, 'templates'),
            static_folder=os.path.join(_base, 'static'))

# ---- 全局状态 ----
engine: OverWallEngine | None = None
engine_thread: threading.Thread | None = None
_executor = None
_worker_loop = None  # 持久事件循环（所有 Playwright 操作共用）
_task_queue: list = []  # 任务队列
_worker_busy: bool = False

_log_buffer: list[dict] = []
MAX_LOG_BUFFER = 200
_logged_in: bool = False

_status_snapshot: dict = {
    "running": False, "paused": False, "logged_in": False,
    "weekly_points": 0, "weekly_target": 30,
    "session_points": 0, "session_articles": 0,
    "session_videos": 0, "session_exercise_correct": 0,
    "session_exercise_wrong": 0, "question_bank_total": 0,
    "username": "",
}


def _worker_thread():
    """后台工作线程——所有 Playwright 操作都在此线程执行"""
    global _worker_loop, _task_queue, _worker_busy
    import asyncio as _asyncio
    _worker_loop = _asyncio.new_event_loop()
    _asyncio.set_event_loop(_worker_loop)

    async def _process():
        global _task_queue, _worker_busy
        while True:
            if _task_queue:
                _worker_busy = True
                task = _task_queue.pop(0)
                try:
                    await task()
                except Exception as e:
                    _add_log("error", f"任务异常: {e}")
                _worker_busy = False
            await _asyncio.sleep(0.2)

    _worker_loop.run_until_complete(_process())
    _worker_loop.close()

# 启动后台工作线程
_background_thread = threading.Thread(target=_worker_thread, daemon=True)
_background_thread.start()


def _add_log(level: str, message: str):
    _log_buffer.append({"time": datetime.now().strftime("%H:%M:%S"),
                        "level": level, "message": message})
    if len(_log_buffer) > MAX_LOG_BUFFER:
        _log_buffer[:] = _log_buffer[-MAX_LOG_BUFFER:]


async def _log_callback(level: str, message):
    if level == "status" and isinstance(message, dict):
        _status_snapshot.update(message)
    else:
        _add_log(level, str(message))


async def _status_callback(status: dict):
    _status_snapshot.update(status)
    _status_snapshot["question_bank_total"] = bank_stats()["total"]


# ---- Flask 路由 ----
@app.route("/")
def index():
    """主页面"""
    return render_template("index.html")


# ---- 配置接口 ----
@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    """配置读写"""
    if request.method == "POST":
        data = request.get_json()
        if data:
            current = load_config()
            for key, value in data.items():
                if key in current and isinstance(current[key], dict) and isinstance(value, dict):
                    current[key].update(value)
                else:
                    current[key] = value
            save_config(current)
        return jsonify({"code": 200, "msg": "ok"})
    cfg = load_config()
    return jsonify({"code": 200, "data": cfg})


# ---- 状态轮询接口（前端每秒调用） ----
@app.route("/api/status")
def api_status():
    """返回当前引擎状态 + 增量日志"""
    # 只返回上次轮询之后的增量日志
    since = request.args.get("since", "")
    new_logs = []
    if since:
        for entry in _log_buffer:
            if entry["time"] > since:
                new_logs.append(entry)
    else:
        new_logs = _log_buffer[-30:]  # 首次返回最近30条

    return jsonify({
        "code": 200,
        "data": {
            "status": dict(_status_snapshot),
            "logs": new_logs,
        },
    })


def _submit_task(coro_factory, module_name: str):
    """向工作线程提交协程任务（单任务排队）"""
    global _worker_loop, _worker_busy
    if _worker_busy:
        return jsonify({"code": 400, "msg": "上一个任务还在执行中"})
    if not _worker_loop or _worker_loop.is_closed():
        return jsonify({"code": 500, "msg": "工作线程未就绪"})

    _worker_busy = True
    _status_snapshot["running"] = True
    _add_log("info", f"===== {module_name} =====")

    import asyncio as _asyncio
    async def wrapper():
        global _worker_busy
        try:
            await coro_factory()
        except Exception as e:
            _add_log("error", f"{module_name} 异常: {e}")
        finally:
            _status_snapshot["running"] = False
            _worker_busy = False
            _add_log("info", f"===== {module_name} 完毕 =====")

    _asyncio.run_coroutine_threadsafe(wrapper(), _worker_loop)
    return jsonify({"code": 200, "msg": f"已启动: {module_name}"})


# ---- 登录 ----
@app.route("/api/login", methods=["POST"])
def api_login():
    global _executor, _logged_in, _worker_loop
    # 已登录且 executor 存活 → 拒绝；stop 后 executor 可能残留但 _logged_in 已置 False → 允许
    if _executor is not None and _logged_in:
        return jsonify({"code": 400, "msg": "已登录，请先注销"})

    cfg = load_config()
    from executor import TaskExecutor
    _executor = TaskExecutor(cfg, log_callback=_log_callback)
    _status_snapshot["weekly_target"] = cfg.get("weekly_target", 30)

    async def _do_login():
        global _logged_in
        _log_buffer.clear()
        _executor.running = True
        page = await _executor.start_browser()
        if not page:
            _add_log("error", "浏览器启动失败")
            return
        ok = await _executor.login()
        if ok:
            _logged_in = True
            _status_snapshot["logged_in"] = True
            _status_snapshot["username"] = cfg.get("username", "")
            pts = await _executor.get_weekly_points()
            _status_snapshot["weekly_points"] = pts
            _status_snapshot["question_bank_total"] = bank_stats()["total"]
            _add_log("info", f"登录成功！当前周积分: {pts}")
        else:
            _add_log("error", "登录失败")

    return _submit_task(_do_login, "登录")


@app.route("/api/logout", methods=["POST"])
def api_logout():
    global _executor, _logged_in
    async def _do():
        if _executor:
            await _executor.stop_browser()
    if _executor:
        _submit_task(_do, "登出")
        _executor = None
    _logged_in = False
    _status_snapshot["logged_in"] = False
    _status_snapshot["username"] = ""
    _status_snapshot["running"] = False
    _add_log("info", "已注销")
    return jsonify({"code": 200, "msg": "已注销"})


# ---- 自动刷分 ----
@app.route("/api/start", methods=["POST"])
def api_start():
    global engine, _executor
    if not _executor or not _logged_in:
        return jsonify({"code": 400, "msg": "请先登录"})

    cfg = load_config()
    _status_snapshot["paused"] = False
    _status_snapshot["session_points"] = 0
    _status_snapshot["session_articles"] = 0
    _status_snapshot["session_videos"] = 0
    _status_snapshot["session_exercise_correct"] = 0

    async def _do_auto():
        global engine
        engine = OverWallEngine(cfg, log_callback=_log_callback, status_callback=_status_callback)
        engine.executor = _executor
        engine.executor.running = True
        await engine.run()
        _add_log("info", "===== 自动刷分结束 =====")

    return _submit_task(_do_auto, "自动刷分")


@app.route("/api/pause", methods=["POST"])
def api_pause():
    if engine and engine.running:
        if engine._paused:
            engine.resume()
            _status_snapshot["paused"] = False
        else:
            engine.pause()
            _status_snapshot["paused"] = True
    return jsonify({"code": 200, "msg": "ok"})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    global engine, _executor, _logged_in
    if engine:
        engine.stop()
    if _executor:
        _executor.running = False
    _status_snapshot["running"] = False
    _status_snapshot["paused"] = False
    _logged_in = False
    _status_snapshot["logged_in"] = False

    # 在 worker 线程关闭浏览器 + 清理 executor（避免 Flask 线程直接置 None 导致竞态）
    async def _cleanup():
        global _executor
        if _executor:
            try:
                await _executor.stop_browser()
            except Exception:
                pass
            _executor = None

    if _worker_loop and not _worker_loop.is_closed():
        import asyncio as _asyncio
        _asyncio.run_coroutine_threadsafe(_cleanup(), _worker_loop)
    else:
        _executor = None

    _add_log("info", "已停止")
    return jsonify({"code": 200, "msg": "已停止"})

# ---- 获取考试列表 ----
@app.route("/api/exam_list", methods=["POST"])
def api_exam_list():
    global _executor
    if not _executor or not _logged_in:
        return jsonify({"code": 400, "msg": "请先登录"})
    _add_log("info", "正在获取考试列表...")
    return jsonify({"code": 200, "exams": [
        {"name": "救援队模拟考试"},
        {"name": "综合单位取证(再培训)模拟考试"},
        {"name": "危化单位取证(再培训)模拟考试"},
        {"name": "冶炼单位取证(再培训)模拟考试"},
        {"name": "矿山单位取证(再培训)模拟考试"}
    ]})

# ---- 手动模块执行（工作线程执行） ----
@app.route("/api/run/<module>", methods=["POST"])
def api_run_module(module: str):
    global _executor
    if not _executor or not _logged_in:
        return jsonify({"code": 400, "msg": "请先登录"})

    cfg = load_config()
    api_key = cfg.get("deepseek_api_key", "")
    if _executor: _executor.cfg = cfg  # refresh config
    exam_index = request.args.get("index", 0, type=int)

    async def _do():
        _executor.running = True
        try:
            # 确保浏览器连接有效（手动模块login和执行分两次任务，headless下可能断开）
            page = await _executor.start_browser()
            if not page:
                _add_log("error", "浏览器未连接，请重新登录")
                return
            # 无头模式下模拟考试不可用（需可视化交互确认签名）
            if module == "mock_exam" and cfg.get("headless"):
                _add_log("warn", "无头模式下模拟考试不可用，请关闭无头模式后重试")
                return
            if module == "exercise":
                res = await _executor.do_exercises(api_key)
                _add_log("info", f"每日练习: 对{res['correct']} 得{res['points']}分")
                _status_snapshot["session_exercise_correct"] += res.get("correct", 0)
                _status_snapshot["session_exercise_wrong"] += res.get("wrong", 0)
                _status_snapshot["session_points"] += res.get("points", 0)
            elif module == "mock_exam":
                res = await _executor.do_mock_exam(api_key, exam_index or 0)
                _add_log("info", f"模拟考试: {res['correct']}/{res['total']}")
            elif module == "article":
                res = await _executor.study_articles(5)
                _add_log("info", f"图文: +{res}分")
                _status_snapshot["session_articles"] += res
                _status_snapshot["session_points"] += res
            elif module == "video":
                res = await _executor.study_videos(tab=2, count=3)
                res2 = await _executor.study_videos(tab=4, count=3) if res < 3 else 0
                _add_log("info", f"视频: +{res+res2}分")
                _status_snapshot["session_videos"] += (res + res2)
                _status_snapshot["session_points"] += (res + res2)
        except Exception as e:
            _add_log("error", f"执行异常: {e}")
        # 检查 executor 是否被 stop 清除了
        if _executor and _executor.page and not getattr(_executor.page, '_closed', True):
            try:
                pts = await _executor.get_weekly_points()
                _status_snapshot["weekly_points"] = pts
                _add_log("info", f"当前周积分: {pts}")
            except Exception:
                pass

    return _submit_task(_do, module)

# ---- 题库接口 ----
@app.route("/api/bank")
def api_bank():
    return jsonify({"code": 200, "data": bank_stats()})


@app.route("/api/bank/export")
def api_bank_export():
    return jsonify({"code": 200, "data": load_bank()})


@app.route("/api/bank/clear", methods=["POST"])
def api_bank_clear():
    """清空题库"""
    save_bank({"version": "1.0", "questions": {}})
    _status_snapshot["question_bank_total"] = 0
    _add_log("info", "题库已清空")
    return jsonify({"code": 200, "msg": "题库已清空"})


@app.route("/api/bank/import", methods=["POST"])
def api_bank_import():
    data = request.get_json()
    if not data or "questions" not in data:
        return jsonify({"code": 400, "msg": "无效的题库数据"})
    current = load_bank()
    new_count = 0
    for key, value in data["questions"].items():
        if key not in current["questions"]:
            current["questions"][key] = value
            new_count += 1
    save_bank(current)
    _status_snapshot["question_bank_total"] = bank_stats()["total"]
    return jsonify({"code": 200, "msg": f"导入成功，新增 {new_count} 题"})


# ---- 启动入口 ----
if __name__ == "__main__":
    port = 15888
    print(f"""
╔══════════════════════════════════════╗
║        OverWall 刷分系统 v1.0         ║
║  打开浏览器访问:                      ║
║  http://127.0.0.1:{port}              ║
╚══════════════════════════════════════╝
    """)

    # 检查 API 配置
    api_key = load_config().get("deepseek_api_key", "")
    if not api_key:
        print("[!] 未配置 DeepSeek API Key，答题模块将不可用")
        print("[!] 可通过图文学习和视频学习获取积分")
        print("[!] 请在设置页面填写 API Key 后使用答题功能\n")

    # 自动打开浏览器
    import webbrowser
    webbrowser.open(f"http://127.0.0.1:{port}")

    # 检查 Playwright 浏览器
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            pass
    except Exception:
        print("[!] 正在安装 Chromium...")
        os.system(f'"{sys.executable}" -m playwright install chromium')
        print("[OK] Chromium 安装完成")

    # Flask 内置服务器
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
