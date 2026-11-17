/* eslint-disable no-undef */
"use strict";

/**
 * 知译 PDF 2 -- Zotero 7/10 plugin.
 *
 * The plugin is a thin client of the local ZhiYi engine service
 * (`python -m zhiyi --serve`, default http://127.0.0.1:8893):
 *   POST /translate        → {status: "accepted", taskId}
 *   GET  /api/tasks        → running jobs, with percent and message
 *   GET  /api/history      → finished jobs, with fileList / filePaths
 *   GET  /translatedFile/… → result download (fallback when paths are not local)
 *   POST /api/test         → one-sentence check of the translation service
 *
 * The plugin ID, preference prefix and chrome namespace keep their 1.x names
 * ("pdf2rea") so that upgrading keeps every setting.
 */

(function () {
  const ADDON_ID = "pdf2rea@qoder.dev";
  const ADDON_REF = "pdf2rea";
  const PREF_PREFIX = "extensions.zotero.pdf2rea.";
  const PLUGIN_VERSION = "2.0.0";
  const DEFAULT_PORT = 8893;

  const rootURI = typeof _globalThis !== "undefined" && _globalThis.rootURI
    ? _globalThis.rootURI
    : `chrome://${ADDON_REF}/`;

  // ---------------------------------------------------------------- prefs --
  const DEFAULT_PREFS = {
    serverUrl: `http://127.0.0.1:${DEFAULT_PORT}`,
    runtimePython: "",                 // empty: ~/.zhiyi/venv/bin/python
    autoStart: true,
    sourceLang: "en",
    targetLang: "zh-CN",
    llmService: "google",               // google | openai
    llmPreset: "deepseek",
    llmBaseUrl: "https://api.deepseek.com/v1",
    llmModel: "deepseek-chat",
    llmApiKey: "",
    threadNum: 4,
    qps: 4,
    autoGlossary: true,
    glossaryPath: "",
    extraTerms: "",
    translateTables: true,
    translateCaptions: true,
    translateReferences: false,
    fontScale: "1.0",
    mono: true,
    dual: true,
    dualMode: "LR",
    "mono-open": false,
    "dual-open": false,
    rename: true,
    showProgress: true,
    lastSummary: ""
  };

  const PRESETS = {
    deepseek: { label: "DeepSeek", url: "https://api.deepseek.com/v1", model: "deepseek-chat" },
    siliconflow: { label: "硅基流动", url: "https://api.siliconflow.cn/v1", model: "deepseek-ai/DeepSeek-V3" },
    ark: { label: "火山方舟", url: "https://ark.cn-beijing.volces.com/api/v3", model: "deepseek-v3-250324" },
    dashscope: { label: "阿里云百炼", url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus" },
    zhipu: { label: "智谱", url: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4-flash" },
    openai: { label: "OpenAI", url: "https://api.openai.com/v1", model: "gpt-4o-mini" },
    ollama: { label: "Ollama（本机）", url: "http://localhost:11434/v1", model: "qwen2.5:7b" },
    custom: { label: "其他 OpenAI 兼容服务", url: "", model: "" }
  };

  function getPref(key, fallback = DEFAULT_PREFS[key]) {
    const value = Zotero.Prefs.get(PREF_PREFIX + key, true);
    return value === undefined || value === null || value === "" ? fallback : value;
  }

  function setPref(key, value) {
    Zotero.Prefs.set(PREF_PREFIX + key, value, true);
  }

  function flag(key) {
    const value = getPref(key);
    return value === true || value === "true";
  }

  function ensureDefaults() {
    Object.entries(DEFAULT_PREFS).forEach(([key, value]) => {
      if (Zotero.Prefs.get(PREF_PREFIX + key, true) === undefined) setPref(key, value);
    });
  }

  /** One-time import of 1.x settings that still mean something in 2.x. */
  function migrateFrom1x() {
    if (getPref("migratedTo2", false)) return;
    const oldService = String(Zotero.Prefs.get(PREF_PREFIX + "next_service", true) || "");
    let apis = [];
    try { apis = JSON.parse(Zotero.Prefs.get(PREF_PREFIX + "llmApis", true) || "[]") || []; } catch (_) { apis = []; }
    const active = apis.find(api => api && api.activate && api.service === oldService && api.apiKey)
      || apis.find(api => api && api.activate && api.apiKey);
    if (active) {
      const preset = Object.entries(PRESETS).find(([, p]) => p.url && active.apiUrl
        && active.apiUrl.replace(/\/+$/, "") === p.url) || (active.service === "deepseek" ? ["deepseek", PRESETS.deepseek] : null);
      setPref("llmService", "openai");
      setPref("llmPreset", preset ? preset[0] : "custom");
      setPref("llmBaseUrl", active.apiUrl || (preset ? preset[1].url : ""));
      setPref("llmModel", active.model || (preset ? preset[1].model : ""));
      setPref("llmApiKey", active.apiKey);
    }
    const oldUrl = String(Zotero.Prefs.get(PREF_PREFIX + "new_serverip", true) || "");
    if (oldUrl && !/:(8890|8891)\/?$/.test(oldUrl)) setPref("serverUrl", oldUrl.replace(/\/+$/, ""));
    const oldMode = Zotero.Prefs.get(PREF_PREFIX + "dualMode", true);
    if (oldMode !== "LR" && oldMode !== "TB") setPref("dualMode", "LR");
    setPref("migratedTo2", true);
  }

  // ------------------------------------------------------------ helpers ----
  const joinPath = (...parts) => PathUtils.join(...parts);
  const fileLeaf = (name) => String(name).split(/[\\/]/).pop();

  async function exists(path) {
    try { return await IOUtils.exists(path); } catch (_) { return false; }
  }

  function serverUrl() {
    return String(getPref("serverUrl")).trim().replace(/\/+$/, "");
  }

  function serverPort() {
    const match = serverUrl().match(/:(\d+)$/);
    return match ? Number(match[1]) : DEFAULT_PORT;
  }

  function isLocalServer() {
    return /^http:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$/.test(serverUrl());
  }

  function runtimePython() {
    const configured = String(getPref("runtimePython", "")).trim();
    if (configured) return configured.replace(/^~(?=\/)/, PathUtils.homeDir);
    return Services.appinfo.OS === "WINNT"
      ? joinPath(PathUtils.homeDir, ".zhiyi", "venv", "Scripts", "python.exe")
      : joinPath(PathUtils.homeDir, ".zhiyi", "venv", "bin", "python");
  }

  async function fetchJSON(url, options = {}, timeoutMs = 15000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(url, { cache: "no-store", ...options, signal: controller.signal });
      const text = await response.text();
      let payload;
      try { payload = JSON.parse(text); } catch (_) {
        throw new Error(text.slice(0, 200) || `HTTP ${response.status}`);
      }
      if (!response.ok || payload.status === "error") throw new Error(payload.message || `HTTP ${response.status}`);
      return payload;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("请求超时");
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }

  // ------------------------------------------------------------ service ----
  async function probeServer() {
    try {
      const health = await fetchJSON(`${serverUrl()}/health`, {}, 3000);
      return health.name === "zhiyi" ? health : null;
    } catch (_) {
      return null;
    }
  }

  async function runService(action) {
    const python = runtimePython();
    if (!(await exists(python))) {
      throw new Error(`没有找到翻译引擎（${python}）。请先运行安装脚本，详见设置 → 知译 PDF → 引擎与服务。`);
    }
    const { Subprocess } = ChromeUtils.importESModule("resource://gre/modules/Subprocess.sys.mjs");
    const proc = await Subprocess.call({
      command: python,
      arguments: ["-m", "zhiyi", "service", action, "--port", String(serverPort())],
      stderr: "stdout",
      environmentAppend: true,
      environment: { PYTHONIOENCODING: "utf-8" }
    });
    let output = "";
    let chunk;
    while ((chunk = await proc.stdout.readString())) output += chunk;
    await proc.wait();
    const last = output.trim().split("\n").pop() || "";
    try { return JSON.parse(last); } catch (_) {
      throw new Error(`引擎没有返回有效结果：${output.trim().slice(-300) || "（无输出）"}`);
    }
  }

  let startPromise = null;

  /** Make sure a current engine service answers; start it when allowed. */
  async function ensureService() {
    const health = await probeServer();
    if (health && health.version === PLUGIN_VERSION) return health;
    if (!isLocalServer() || !flag("autoStart")) {
      if (health) return health;
      throw new Error(`无法连接翻译服务 ${serverUrl()}。请在设置 → 知译 PDF → 引擎与服务中启动。`);
    }
    startPromise ??= runService("start").finally(() => { startPromise = null; });
    const result = await startPromise;
    if (!result.ok) throw new Error(result.message || "翻译服务启动失败");
    const ready = await probeServer();
    if (!ready) throw new Error("翻译服务已启动，但没有响应");
    return ready;
  }

  // ------------------------------------------------------------ request ----
  function buildConfig() {
    const service = getPref("llmService") === "openai" ? "openai" : "google";
    const config = {
      lang_in: String(getPref("sourceLang")),
      lang_out: String(getPref("targetLang")),
      service,
      threads: Number(getPref("threadNum")) || 4,
      qps: Number(getPref("qps")) || 4,
      auto_glossary: flag("autoGlossary"),
      glossary_path: String(getPref("glossaryPath", "")),
      extra_terms: String(getPref("extraTerms", "")),
      translate_tables: flag("translateTables"),
      translate_captions: flag("translateCaptions"),
      translate_references: flag("translateReferences"),
      font_scale: Number(getPref("fontScale")) || 1,
      mono: flag("mono"),
      dual: flag("dual"),
      dual_mode: getPref("dualMode") === "TB" ? "TB" : "LR"
    };
    if (service === "openai") {
      config.base_url = String(getPref("llmBaseUrl", "")).trim();
      config.model = String(getPref("llmModel", "")).trim();
      config.api_key = String(getPref("llmApiKey", "")).trim();
    }
    return config;
  }

  function validateConfig(config) {
    if (!config.mono && !config.dual) throw new Error("未选择输出：请在“排版与输出”中至少勾选一种结果。");
    if (config.service === "openai") {
      if (!config.base_url) throw new Error("请在“翻译服务”中填写 API 地址。");
      if (!config.model) throw new Error("请在“翻译服务”中填写模型名称。");
      if (!config.api_key && !/localhost|127\.0\.0\.1/.test(config.base_url)) {
        throw new Error("请在“翻译服务”中填写 API Key。");
      }
    }
  }

  // ---------------------------------------------------------------- jobs ---
  class Job {
    constructor(item) {
      this.item = item;
    }

    static async attachmentFor(item) {
      let attachment = null;
      if (item.isAttachment()) attachment = item;
      else if (item.isRegularItem?.()) attachment = await item.getBestAttachment();
      if (!attachment || attachment.attachmentContentType !== "application/pdf") {
        throw new Error("没有 PDF 附件");
      }
      const path = attachment.getFilePath();
      if (!path || !(await exists(path))) throw new Error("PDF 文件不在本机（可能尚未同步下载）");
      return { attachment, path };
    }

    static async toBase64(bytes) {
      const chunks = [];
      for (let index = 0; index < bytes.length; index += 8192) {
        chunks.push(String.fromCharCode.apply(null, bytes.subarray(index, index + 8192)));
      }
      return btoa(chunks.join(""));
    }

    async run(config, onProgress) {
      const { attachment, path } = await Job.attachmentFor(this.item);
      this.attachment = attachment;
      onProgress(0, "上传中");
      const bytes = await IOUtils.read(path);
      const body = { fileName: PathUtils.filename(path), fileContent: await Job.toBase64(bytes), config };
      const accepted = await fetchJSON(`${serverUrl()}/translate`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-ZhiYi-Plugin": PLUGIN_VERSION },
        body: JSON.stringify(body)
      }, 120000);
      const task = await this.wait(String(accepted.taskId || ""), onProgress);
      onProgress(100, "加入文献库");
      const imported = await this.importResults(task);
      return { task, imported };
    }

    async wait(taskId, onProgress) {
      if (!taskId) throw new Error("服务没有返回任务编号");
      const deadline = Date.now() + 4 * 60 * 60 * 1000;
      let misses = 0;
      while (Date.now() < deadline) {
        let record = null;
        try {
          const running = await fetchJSON(`${serverUrl()}/api/tasks`, {}, 10000);
          record = (running.tasks || []).find(task => task.taskId === taskId);
          if (!record) {
            const history = await fetchJSON(`${serverUrl()}/api/history`, {}, 10000);
            record = (history.history || []).find(task => task.taskId === taskId);
          }
          misses = 0;
        } catch (error) {
          if (++misses > 30) throw new Error(`与翻译服务的连接中断：${error.message}`);
        }
        if (record) {
          if (record.status === "failed") throw new Error(record.error || record.message || "翻译失败");
          if (record.status === "success") return record;
          onProgress(record.percent || 0, record.message || "");
        } else if (!misses) {
          throw new Error("翻译服务丢失了这个任务（服务可能被重启）");
        }
        await Zotero.Promise.delay(1000);
      }
      throw new Error("等待翻译超时");
    }

    static fileType(name) {
      if (/\.mono\.pdf$/i.test(name)) return "mono";
      if (/\.dual\.pdf$/i.test(name)) return "dual";
      return "pdf";
    }

    async importResults(task) {
      const names = (task.fileList || []).filter(name => /\.pdf$/i.test(name));
      if (!names.length) throw new Error("任务已结束，但没有生成 PDF");
      const parentItemID = this.attachment.parentItemID || undefined;
      const parent = parentItemID ? Zotero.Items.get(parentItemID) : null;
      const shortTitle = parent?.getField?.("shortTitle") || "";
      let imported = 0;
      for (const [index, name] of names.entries()) {
        const type = Job.fileType(name);
        const local = task.filePaths?.[index];
        let source = local && isLocalServer() && (await exists(local)) ? local : null;
        let temp = null;
        if (!source) {
          const response = await fetch(`${serverUrl()}/translatedFile/${encodeURIComponent(name)}`);
          if (!response.ok) throw new Error(`下载译文失败（HTTP ${response.status}）`);
          temp = joinPath(PathUtils.tempDir, `zhiyi-${Date.now()}-${fileLeaf(name)}`);
          await IOUtils.write(temp, new Uint8Array(await response.arrayBuffer()));
          source = temp;
        }
        try {
          const label = type === "mono" ? "译文" : type === "dual" ? "双语对照" : "PDF";
          const title = flag("rename") ? `${shortTitle ? shortTitle + " - " : ""}知译 ${label}` : fileLeaf(name);
          const attachment = await Zotero.Attachments.importFromFile({
            file: source,
            parentItemID,
            libraryID: this.attachment.libraryID,
            fileBaseName: fileLeaf(name).replace(/\.pdf$/i, ""),
            contentType: "application/pdf",
            title
          });
          if (!attachment?.id) throw new Error("Zotero 没有返回有效附件");
          imported += 1;
          if (flag(`${type}-open`)) {
            try { Zotero.Reader.open(attachment.id); } catch (error) { Zotero.logError(error); }
          }
        } finally {
          if (temp) { try { await IOUtils.remove(temp); } catch (error) { Zotero.logError(error); } }
        }
      }
      return imported;
    }
  }

  function summaryText(summary) {
    if (!summary) return "";
    const parts = [`${summary.units} 段`, `合并跨栏/跨页段落 ${summary.chained} 处`, `保留公式 ${summary.formulas} 处`];
    if (summary.shrunk) parts.push(`${summary.shrunk} 段单独缩小了字号`);
    if (summary.keptOriginal) parts.push(`${summary.keptOriginal} 处放不下而保留原文`);
    if (summary.failed) parts.push(`${summary.failed} 段翻译失败、保留原文`);
    parts.push(`用时 ${summary.seconds} 秒`);
    return parts.join("，");
  }

  class Progress {
    constructor(count) {
      this.window = null;
      this.rows = new Map();
      if (!flag("showProgress")) return;
      try {
        this.window = new Zotero.ProgressWindow({ closeOnClick: false });
        this.window.changeHeadline(`知译 PDF：${count} 个文件`);
        this.window.show();
      } catch (error) {
        Zotero.logError(error);
        this.window = null;
      }
    }

    row(job) {
      if (!this.window) return null;
      if (!this.rows.has(job)) {
        const title = job.item.getDisplayTitle?.() || "PDF";
        this.rows.set(job, new this.window.ItemProgress("chrome://zotero/skin/treeitem-attachment-pdf.png", title));
      }
      return this.rows.get(job);
    }

    update(job, percent, message) {
      const row = this.row(job);
      if (!row) return;
      row.setProgress(Math.max(1, Math.min(100, percent)));
      if (message) row.setText(`${job.item.getDisplayTitle?.() || "PDF"} · ${message}`);
    }

    done(job, summary) {
      const row = this.row(job);
      if (!row) return;
      row.setProgress(100);
      if (summary) row.setText(`${job.item.getDisplayTitle?.() || "PDF"} · ${summaryText(summary)}`);
    }

    fail(job, error) {
      const row = this.row(job);
      if (!row) return;
      row.setError();
      row.setText(`${job.item.getDisplayTitle?.() || "PDF"} · ${error.message || error}`);
    }

    finish(ok, failed) {
      if (!this.window) return;
      this.window.changeHeadline(failed ? `知译 PDF：成功 ${ok}，失败 ${failed}` : `知译 PDF：完成 ${ok} 个文件`);
      if (!failed) this.window.startCloseTimer(10000);
    }
  }

  const state = { running: false };

  async function translateSelected() {
    const win = Zotero.getMainWindow();
    if (state.running) {
      win.alert("知译 PDF：已有翻译任务在进行，请等待其完成。");
      return;
    }
    const items = win.ZoteroPane.getSelectedItems();
    if (!items.length) {
      win.alert("请先选中含 PDF 附件的条目或 PDF 附件。");
      return;
    }
    const jobs = [];
    const skipped = [];
    for (const item of items) {
      try {
        await Job.attachmentFor(item);
        jobs.push(new Job(item));
      } catch (error) {
        skipped.push(`${item.getDisplayTitle?.() || "条目"}：${error.message}`);
      }
    }
    if (!jobs.length) {
      win.alert(skipped.join("\n") || "没有可翻译的 PDF。");
      return;
    }
    const config = buildConfig();
    try {
      validateConfig(config);
      await ensureService();
    } catch (error) {
      win.alert(`知译 PDF：${error.message}`);
      return;
    }

    state.running = true;
    const progress = new Progress(jobs.length);
    let ok = 0;
    const errors = [];
    try {
      for (const job of jobs) {
        try {
          const { task } = await job.run(config, (percent, message) => progress.update(job, percent, message));
          ok += 1;
          progress.done(job, task.summary);
          setPref("lastSummary", `${new Date().toLocaleString()} · ${job.item.getDisplayTitle?.() || "PDF"}：${summaryText(task.summary)}`);
        } catch (error) {
          Zotero.logError(error);
          errors.push(`${job.item.getDisplayTitle?.() || "PDF"}：${error.message || error}`);
          progress.fail(job, error);
        }
      }
    } finally {
      state.running = false;
      progress.finish(ok, errors.length);
    }
    if ((errors.length && !flag("showProgress")) || skipped.length) {
      win.alert(["知译 PDF：", ...errors, ...skipped.map(s => `已跳过 ${s}`)].join("\n"));
    }
  }

  // ------------------------------------------------------------------ ui ---
  function registerMainWindow(win) {
    const document = win.document;
    const menu = document.getElementById("zotero-itemmenu");
    if (!menu || document.getElementById("pdf2rea-itemmenu")) return;
    const separator = document.createXULElement("menuseparator");
    separator.id = "pdf2rea-itemmenu-separator";
    const item = document.createXULElement("menuitem");
    item.id = "pdf2rea-itemmenu";
    item.setAttribute("label", "知译 PDF：翻译");
    item.classList.add("menuitem-iconic");
    item.setAttribute("image", `${rootURI}content/icons/favicon@0.5x.svg`);
    item.addEventListener("command", () => translateSelected().catch(error => {
      Zotero.logError(error);
      win.alert(`知译 PDF：${error.message || error}`);
    }));
    menu.appendChild(separator);
    menu.appendChild(item);
  }

  function registerPrefs(win) {
    const doc = win.document;
    const $ = (id) => doc.getElementById(id);
    const banner = $("pdf2rea-version");
    if (banner) banner.textContent = `知译 PDF ${PLUGIN_VERSION}`;

    // tabs
    const tabs = [...doc.querySelectorAll("[data-pdf2rea-tab]")];
    const show = (name) => {
      tabs.forEach(tab => tab.setAttribute("aria-selected", String(tab.dataset.pdf2reaTab === name)));
      doc.querySelectorAll("[data-pdf2rea-panel]").forEach(panel => {
        panel.hidden = panel.dataset.pdf2reaPanel !== name;
      });
    };
    tabs.forEach(tab => tab.addEventListener("click", () => show(tab.dataset.pdf2reaTab)));
    show("service");

    // translation service
    const serviceSelect = $("pdf2rea-llmService");
    const presetSelect = $("pdf2rea-llmPreset");
    const llmBox = $("pdf2rea-llm-fields");
    const syncService = () => { if (llmBox) llmBox.hidden = (serviceSelect?.value || getPref("llmService")) !== "openai"; };
    serviceSelect?.addEventListener("command", syncService);
    serviceSelect?.addEventListener("change", syncService);
    syncService();
    presetSelect?.addEventListener("change", () => {
      const preset = PRESETS[presetSelect.value];
      if (!preset || !preset.url) return;
      setPref("llmBaseUrl", preset.url);
      setPref("llmModel", preset.model);
      const url = $("pdf2rea-llmBaseUrl");
      const model = $("pdf2rea-llmModel");
      if (url) url.value = preset.url;
      if (model) model.value = preset.model;
    });

    const testOut = $("pdf2rea-test-result");
    $("pdf2rea-test-service")?.addEventListener("click", async () => {
      testOut.textContent = "正在测试…";
      try {
        const config = buildConfig();
        validateConfig({ ...config, mono: true });
        await ensureService();
        const reply = await fetchJSON(`${serverUrl()}/api/test`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ config })
        }, 120000);
        testOut.textContent = `可用 ✓  “${reply.sample}” → “${reply.translation}”`;
      } catch (error) {
        testOut.textContent = `不可用：${error.message}`;
      }
    });

    // engine & service
    const status = $("pdf2rea-service-status");
    const refresh = async () => {
      if (!status) return;
      const health = await probeServer();
      const python = runtimePython();
      const installed = await exists(python);
      status.dataset.state = health ? "ok" : "missing";
      status.textContent = health
        ? `服务运行中 · 引擎 ${health.version} · ${serverUrl()}${health.version !== PLUGIN_VERSION ? "（版本与插件不一致，翻译时会自动重启）" : ""}`
        : installed ? `服务未运行（引擎已安装：${python}）` : `引擎未安装（未找到 ${python}）`;
      const last = $("pdf2rea-last-summary");
      if (last) last.textContent = getPref("lastSummary", "") || "还没有翻译记录。";
    };
    $("pdf2rea-start-service")?.addEventListener("click", async () => {
      status.textContent = "正在启动…";
      try {
        const result = await runService("start");
        if (!result.ok) throw new Error(result.message || "启动失败");
      } catch (error) {
        status.dataset.state = "missing";
        status.textContent = error.message;
        return;
      }
      await refresh();
    });
    $("pdf2rea-stop-service")?.addEventListener("click", async () => {
      try { await runService("stop"); } catch (error) { Zotero.logError(error); }
      await refresh();
    });
    $("pdf2rea-refresh-service")?.addEventListener("click", refresh);
    refresh();
  }

  // --------------------------------------------------------------- hooks ---
  const hooks = {
    async onStartup(bootRootURI) {
      ensureDefaults();
      migrateFrom1x();
      try {
        await Zotero.PreferencePanes.register({
          id: "pdf2rea-preferences",
          pluginID: ADDON_ID,
          src: `${bootRootURI || rootURI}content/preferences.xhtml`,
          label: "知译 PDF",
          image: `${bootRootURI || rootURI}content/icons/favicon.svg`
        });
      } catch (error) {
        Zotero.logError(error);
      }
      Zotero.getMainWindows().forEach(registerMainWindow);
    },

    onMainWindowLoad(win) {
      registerMainWindow(win);
    },

    onMainWindowUnload() {},

    onShutdown() {
      Zotero.getMainWindows().forEach(win => {
        win.document.getElementById("pdf2rea-itemmenu")?.remove();
        win.document.getElementById("pdf2rea-itemmenu-separator")?.remove();
      });
      delete Zotero.pdf2rea;
    },

    onPrefsEvent(type, data) {
      if (type === "load") registerPrefs(data.window);
    }
  };

  Zotero.pdf2rea = {
    data: { config: { addonID: ADDON_ID, addonRef: ADDON_REF, pluginVersion: PLUGIN_VERSION, rootURI } },
    hooks,
    api: { translateSelected, buildConfig, validateConfig, ensureService, probeServer, runService, migrateFrom1x, Job, PRESETS }
  };
})();
