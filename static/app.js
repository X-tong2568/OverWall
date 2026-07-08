/**
 * OverWall 前端 —— 轮询 + 设置覆盖层 + 主题
 */
(function () {
  "use strict";
  var $ = function(s){return document.querySelector(s);};
  var $$ = function(s){return document.querySelectorAll(s);};
  var esc = function(str){var d=document.createElement("div");d.textContent=str;return d.innerHTML;};

  var domStatusDot = $("#statusDot");
  var domStatusText = $("#statusText");
  var domScoreCurrent = $("#scoreCurrent");
  var domScoreTarget = $("#scoreTarget");
  var domRingProgress = $("#ringProgress");
  var domLogContainer = $("#logContainer");
  var domFooterStatus = $("#footerStatus");
  var domBtnLogin = $("#btnLogin");
  var domBtnLogout = $("#btnLogout");
  var domLoginStatus = $("#loginStatus");
  var domAutoBar = $("#autoBar");
  var domModuleBar = $("#moduleBar");
  var domBtnStart = $("#btnStart");
  var domBtnPause = $("#btnPause");
  var domBtnStop = $("#btnStop");

  var lastLogTime = "", engineRunning = false, logCount = 0;
  var seenKeys = new Set();

  // ===== 轮询 =====
  function fetchStatus() {
    fetch("/api/status?since=" + encodeURIComponent(lastLogTime))
      .then(function(r){return r.json();})
      .then(function(resp){
        if (!resp || resp.code !== 200) return;
        var d = resp.data; if (!d) return;
        if (d.status) updateDashboard(d.status);
        if (d.logs) d.logs.forEach(function(e){
          var k = e.time + "|" + e.message;
          if (!seenKeys.has(k)) { seenKeys.add(k); addLog(e.time, e.level, e.message); }
        });
        if (d.logs && d.logs.length) lastLogTime = d.logs[d.logs.length-1].time;
      }).catch(function(){});
  }
  setInterval(fetchStatus, 1500);
  fetchStatus();

  function addLog(time, level, msg) {
    if (logCount === 0) { var p = domLogContainer.querySelector(".log-placeholder"); if (p) p.remove(); }
    var el = document.createElement("div"); el.className = "log-line";
    el.innerHTML = '<span class="log-time">'+esc(time)+'</span><span class="log-level '+esc(level)+'">'+esc(level.toUpperCase())+'</span><span class="log-msg">'+esc(msg)+'</span>';
    domLogContainer.appendChild(el); domLogContainer.scrollTop = domLogContainer.scrollHeight; logCount++;
    while (logCount > 200) { if (domLogContainer.firstChild) { domLogContainer.firstChild.remove(); logCount--; } }
  }

  function updateDashboard(data) {
    if (data.weekly_points !== undefined) domScoreCurrent.textContent = data.weekly_points;
    if (data.weekly_target !== undefined) domScoreTarget.textContent = data.weekly_target;
    if (data.weekly_points !== undefined && data.weekly_target > 0) {
      var c = 2 * Math.PI * 52, pct = Math.min(data.weekly_points/data.weekly_target, 1);
      domRingProgress.setAttribute("stroke-dasharray", c+" "+c);
      domRingProgress.setAttribute("stroke-dashoffset", c*(1-pct));
    }
    if (data.session_points !== undefined) $("#statSession").textContent = data.session_points;
    if (data.session_articles !== undefined) $("#statArticles").textContent = data.session_articles;
    if (data.session_videos !== undefined) $("#statVideos").textContent = data.session_videos;
    if (data.session_exercise_correct !== undefined) $("#statCorrect").textContent = data.session_exercise_correct;
    if (data.question_bank_total !== undefined) { $("#statBank").textContent = data.question_bank_total; $("#bankCount").textContent = data.question_bank_total; }
    updateUI(data.logged_in || data.running, data.running, data.paused);
  }

  function updateUI(loggedIn, running, paused) {
    engineRunning = running;
    $$(".btn-module").forEach(function(b){b.disabled=running;b.style.opacity=running?"0.5":"1";});
    domStatusDot.className = "status-dot";
    if (loggedIn) {
      domBtnLogin.classList.add("hidden"); domBtnLogout.classList.remove("hidden");
      domLoginStatus.textContent = "已登录"; domLoginStatus.className = "login-status-text logged-in";
      domAutoBar.style.display = "flex"; domModuleBar.style.display = "flex";
    } else {
      domBtnLogin.classList.remove("hidden"); domBtnLogout.classList.add("hidden");
      domLoginStatus.textContent = "未登录"; domLoginStatus.className = "login-status-text logged-out";
      domAutoBar.style.display = "none"; domModuleBar.style.display = "none";
    }
    if (running && !paused) {
      domStatusDot.classList.add("running"); domStatusText.textContent = "运行中";
      domBtnStart.classList.add("hidden"); domBtnPause.classList.remove("hidden"); domBtnStop.classList.remove("hidden");
      domBtnPause.textContent = "⏸ 暂停"; domFooterStatus.textContent = "引擎运行中";
    } else if (running && paused) {
      domStatusDot.classList.add("paused"); domStatusText.textContent = "已暂停";
      domBtnPause.textContent = "▶ 继续"; domFooterStatus.textContent = "引擎已暂停";
    } else {
      domStatusDot.classList.add("stopped"); domStatusText.textContent = "待机中";
      domBtnStart.classList.remove("hidden"); domBtnPause.classList.add("hidden"); domBtnStop.classList.add("hidden");
      domFooterStatus.textContent = loggedIn ? "就绪" : "请先登录";
    }
  }

  // ===== 按钮 =====
  domBtnLogin.addEventListener("click", function(){fetch("/api/login",{method:"POST"});lastLogTime="";seenKeys.clear();});
  domBtnLogout.addEventListener("click", function(){fetch("/api/stop",{method:"POST"});fetch("/api/logout",{method:"POST"});});
  domBtnStart.addEventListener("click", function(){fetch("/api/start",{method:"POST"});lastLogTime="";seenKeys.clear();});
  domBtnPause.addEventListener("click", function(){fetch("/api/pause",{method:"POST"});});
  domBtnStop.addEventListener("click", function(){fetch("/api/stop",{method:"POST"});});
  $("#clearLogBtn").addEventListener("click", function(){domLogContainer.innerHTML='<div class="log-placeholder">日志已清空...</div>';logCount=0;seenKeys.clear();});

  // ===== 模块按钮 =====
  $$(".btn-module").forEach(function(btn){
    btn.addEventListener("click", function(){
      if (engineRunning) { addLog("--:--:--","warn","任务执行中"); return; }
      var mod = btn.dataset.module;
      if (mod === "mock_exam") {
        if ($("#cfgHeadless") && $("#cfgHeadless").checked) { addLog("--:--:--","warn","无头模式禁用模拟考试"); return; }
        if (!confirm("模拟考试需要手动签名。确定？")) return;
        fetch("/api/exam_list",{method:"POST"}).then(function(r){return r.json();}).then(function(resp){
          if (resp.code===200 && resp.exams) showExamList(resp.exams);
        }); return;
      }
      runModule(mod);
    });
  });

  function runModule(mod, extra) {
    engineRunning = true;
    $$(".btn-module").forEach(function(b){b.disabled=true;b.style.opacity="0.5";});
    var url = "/api/run/"+mod; if (extra!==undefined) url += "?index="+extra;
    fetch(url,{method:"POST"}).then(function(r){return r.json();}).then(function(resp){
      if (resp.code !== 200) addLog("--:--:--","warn",resp.msg);
    });
  }

  function showExamList(exams) {
    var bar = $("#examListBar"); bar.innerHTML='<span style="font-size:12px;color:var(--muted);margin-right:8px;">选择考试:</span>'; bar.style.display="flex";
    exams.forEach(function(ex,i){
      var b=document.createElement("button");b.className="btn btn-sm btn-module";b.textContent=(i+1)+". "+ex.name.substring(0,20);
      b.title=ex.name;b.addEventListener("click",function(){bar.style.display="none";runModule("mock_exam",i);});bar.appendChild(b);
    });
    var c=document.createElement("button");c.className="btn btn-sm btn-ghost";c.textContent="取消";c.addEventListener("click",function(){bar.style.display="none";});bar.appendChild(c);
  }

  // ===== 设置覆盖层 =====
  $("#btnSettings").addEventListener("click", function(){$("#settingsOverlay").classList.remove("hidden");loadSettings();});
  $("#closeSettings").addEventListener("click", function(){$("#settingsOverlay").classList.add("hidden");});
  $("#saveSettingsBtn").addEventListener("click", function(){saveSettings();$("#settingsOverlay").classList.add("hidden");});
  $("#resetSettingsBtn").addEventListener("click", function(){
    if (!confirm("确定恢复默认？")) return;
    fetch("/api/config",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({strategy_order:["article","video","exercise"],weekly_target:30,headless:false,theme:themePresets.porcelain})}).then(function(){loadSettings();applyTheme(themePresets.porcelain);});
  });

  function loadSettings() {
    fetch("/api/config").then(function(r){return r.json();}).then(function(resp){
      var c=resp.data;if(!c)return;
      $("#cfgBaseUrl").value=c.base_url||"";$("#cfgUsername").value=c.username||"";$("#cfgPassword").value=c.password||"";
      $("#cfgApiKey").value=c.deepseek_api_key||"";$("#cfgApiBase").value=c.deepseek_base_url||"https://api.deepseek.com";
      $("#cfgModel").value=c.deepseek_model||"deepseek-chat";$("#cfgTarget").value=c.weekly_target||30;
      $("#cfgHeadless").checked=c.headless||false;
      // 按配置的策略顺序重排 UI
      var order=c.strategy_order||["article","video","exercise"];
      var ct=$("#strategyOrder");if(ct){
        var items=ct.querySelectorAll(".strategy-item");
        order.reverse().forEach(function(t){var el=ct.querySelector('[data-type="'+t+'"]');if(el)ct.insertBefore(el,ct.firstChild);});
      }
      var t=c.theme||{};if(t.accent)$("#colorAccent").value=t.accent;if(t.bg)$("#colorBg").value=t.bg;
      if(t.text)$("#colorText").value=t.text;if(t.surface)$("#colorSurface").value=t.surface;applyTheme(t);
    });
  }

  function saveSettings() {
    fetch("/api/config",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
      base_url:$("#cfgBaseUrl").value.trim(),username:$("#cfgUsername").value.trim(),password:$("#cfgPassword").value,
      deepseek_api_key:$("#cfgApiKey").value.trim(),deepseek_base_url:$("#cfgApiBase").value.trim()||"https://api.deepseek.com",
      deepseek_model:$("#cfgModel").value||"deepseek-chat",weekly_target:parseInt($("#cfgTarget").value)||30,
      headless:$("#cfgHeadless").checked,strategy_order:getStrategyOrder(),
      theme:{accent:$("#colorAccent").value,bg:$("#colorBg").value,text:$("#colorText").value,surface:$("#colorSurface").value}
    })}).then(function(){applyTheme({accent:$("#colorAccent").value,bg:$("#colorBg").value,text:$("#colorText").value,surface:$("#colorSurface").value});});
  }

  // ===== 策略拖拽 =====
  function initDrag(){
    var ct=$("#strategyOrder");if(!ct)return;var dr=null;
    ct.addEventListener("dragstart",function(e){dr=e.target.closest(".strategy-item");if(dr)dr.style.opacity="0.5";});
    ct.addEventListener("dragend",function(){if(dr)dr.style.opacity="1";dr=null;});
    ct.addEventListener("dragover",function(e){e.preventDefault();var it=e.target.closest(".strategy-item");if(!it||it===dr)return;ct.insertBefore(dr,e.clientY<it.getBoundingClientRect().top+it.offsetHeight/2?it:it.nextSibling);});
  }
  function getStrategyOrder(){return Array.from($$("#strategyOrder .strategy-item")).map(function(el){return el.dataset.type;});}
  initDrag();

  // ===== 主题 =====
  var themePresets = {
    porcelain:{accent:"#2563eb",bg:"#f6f7f9",text:"#1c1f26",surface:"#ffffff",border:"#e5e7eb"},
    graphite:{accent:"#b7ead4",bg:"#303438",text:"#eef5fb",surface:"#1a1d22",border:"#3a3d42"},
    ocean:{accent:"#0ea5e9",bg:"#f0f9ff",text:"#0c4a6e",surface:"#ffffff",border:"#bae6fd"},
    forest:{accent:"#059669",bg:"#f0fdf4",text:"#14532d",surface:"#ffffff",border:"#bbf7d0"}
  };
  $$(".theme-chip").forEach(function(ch){ch.addEventListener("click",function(){var t=themePresets[this.dataset.theme];if(!t)return;$$(".theme-chip").forEach(function(c){c.classList.remove("active");});this.classList.add("active");$("#colorAccent").value=t.accent;$("#colorBg").value=t.bg;$("#colorText").value=t.text;$("#colorSurface").value=t.surface;applyTheme(t);});});
  ["#colorAccent","#colorBg","#colorText","#colorSurface"].forEach(function(s){$(s).addEventListener("input",function(){$$(".theme-chip").forEach(function(c){c.classList.remove("active");});applyTheme({accent:$("#colorAccent").value,bg:$("#colorBg").value,text:$("#colorText").value,surface:$("#colorSurface").value});});});

  function applyTheme(t){
    if(!t)return;var r=document.documentElement.style;
    if(t.accent){r.setProperty("--accent",t.accent);r.setProperty("--accent-soft",t.accent+"20");}
    if(t.bg){r.setProperty("--bg",t.bg);document.body.style.background=t.bg;}
    if(t.text)r.setProperty("--text",t.text);
    if(t.surface)r.setProperty("--surface",t.surface);
    if(t.border)r.setProperty("--border",t.border);
    var d=t.bg?(parseInt(t.bg.slice(1,3),16)*0.299+parseInt(t.bg.slice(3,5),16)*0.587+parseInt(t.bg.slice(5,7),16)*0.114)/255<0.5:false;
    r.setProperty("color-scheme",d?"dark":"light");
    r.setProperty("--border-light",d?"rgba(255,255,255,0.08)":"#f0f1f3");
  }

  // ===== 题库 =====
  $("#exportBankBtn").addEventListener("click",function(){
    fetch("/api/bank/export").then(function(r){return r.json();}).then(function(resp){
      var b=new Blob([JSON.stringify(resp.data,null,2)],{type:"application/json"});
      var a=document.createElement("a");a.href=URL.createObjectURL(b);a.download="bank_"+new Date().toISOString().slice(0,10)+".json";a.click();URL.revokeObjectURL(a.href);
    });
  });
  $("#importBankBtn").addEventListener("click",function(){$("#importBankFile").click();});
  $("#importBankFile").addEventListener("change",function(){
    var f=this.files[0];if(!f)return;var r=new FileReader();
    r.onload=function(e){try{fetch("/api/bank/import",{method:"POST",headers:{"Content-Type":"application/json"},body:e.target.result});}catch(e2){}};r.readAsText(f);this.value="";
  });
  $("#clearBankBtn").addEventListener("click",function(){
    if(!confirm("确定清空题库？建议先导出备份。"))return;
    fetch("/api/bank/clear",{method:"POST"}).then(function(){$("#bankCount").textContent="0";$("#statBank").textContent="0";});
  });
  $("#viewBankBtn").addEventListener("click",function(){
    var v=$("#bankViewer");
    if(v.classList.contains("hidden")){
      fetch("/api/bank/export").then(function(r){return r.json();}).then(function(resp){
        var qs=resp.data?.questions||{};var es=Object.entries(qs);
        $("#bankCount").textContent=es.length;$("#statBank").textContent=es.length;
        v.innerHTML=es.length===0?'<div style="color:var(--muted);padding:8px;">题库为空</div>':es.slice(-30).reverse().map(function(e){
          var q=e[1];return'<div style="margin:4px 0;padding:6px 8px;border:1px solid var(--border-light);border-radius:4px;"><div style="color:var(--text);font-weight:500;">'+esc(q.question||'').substring(0,80)+'</div><div style="color:var(--muted);font-size:11px;">'+(q.options||[]).join(' | ')+'</div><div style="margin-top:2px;"><span style="color:var(--accent);font-weight:700;">答案: '+esc(q.answer||'待定')+'</span><span style="color:var(--muted);font-size:10px;margin-left:8px;">['+esc(q.source||'?')+'] '+esc(q.created_at||'')+'</span></div></div>';
        }).join('');
        v.classList.remove("hidden");
      });
    }else v.classList.add("hidden");
  });

  // ===== 初始化 =====
  applyTheme(themePresets.porcelain);
  loadSettings();
})();
