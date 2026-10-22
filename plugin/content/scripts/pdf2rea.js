/* eslint-disable no-undef */
"use strict";

/**
 * 知译 PDF -- Zotero 7/10 plugin.
 *
 * Protocol-compatible with zotero-pdf2zh 4.1.7 (same POST body, same asyncJob
 * polling, same /translatedFile download, same attachment naming), verified by
 * reading the installed bundle:
 *   .../Zotero/Profiles/&lt;profile&gt;/extensions/pdf2zh@guaguastandup.com.xpi
 *     content/scripts/pdf2zh.js  (8435 lines, esbuild bundle, module paths intact)
 * and the server that answers it (~/.pdf2rea/server/server.py, utils/config.py).
 *
 * What this plugin adds on top of pdf2zh:
 *   - it writes <server>/config/pdf2rea.json + pdf2rea_glossary.csv before every job,
 *     which the bundled Python patch layer reads inside the engine process;
 *   - it installs / removes that patch layer in the engine venv through a .pth file,
 *     so babeldoc and server.py are never edited on disk;
 *   - it reads back <server>/config/pdf2rea.status.json after a job, so the UI can
 *     show how many fixes actually fired instead of assuming they did.
 */

(function () {
  const ADDON_ID = "pdf2rea@qoder.dev";
  const ADDON_REF = "pdf2rea";
  const PREF_PREFIX = "extensions.zotero.pdf2rea.";
  const PLUGIN_VERSION = "1.1.1";

  const rootURI = typeof _globalThis !== "undefined" && _globalThis.rootURI
    ? _globalThis.rootURI
    : `chrome://${ADDON_REF}/`;

  // ---------------------------------------------------------------- i18n ---
  const STRINGS = {
    "menu-title": { en: "ZhiYi PDF", zh: "知译 PDF" },
    "menu-translate": { en: "Translate PDF", zh: "翻译 PDF" },
    "menu-crop": { en: "Crop margins", zh: "裁剪白边" },
    "menu-compare": { en: "Bilingual comparison", zh: "双语对照" },
    "menu-crop-compare": { en: "Crop + compare", zh: "裁剪并对照" },
    "menu-engine": { en: "Translation diagnostics…", zh: "翻译诊断…" },
    "prefs-title": { en: "ZhiYi PDF", zh: "知译 PDF" },

    "engine-not-installed": {
      en: "Enhancement component not installed", zh: "增强组件未安装"
    },
    "engine-installed": {
      en: "Enhancement component installed", zh: "增强组件已安装"
    },
    "engine-install-done": {
      en: `Patch layer written into %S. It activates on the very next translation; no service restart is needed, because the server spawns a fresh engine process per job.`,
      zh: "增强组件已更新，下一次翻译生效。安装位置：%S"
    },
    "engine-install-failed": {
      en: "Could not install the patch layer: %S", zh: "组件安装失败：%S"
    },
    "engine-remove-done": {
      en: "Removed. babeldoc behaviour is back to stock.", zh: "增强组件已移除。下一次翻译使用基础引擎。"
    },
    "engine-not-run-yet": {
      en: "Installed, but no translation has run since. Translate a paper, then open this panel to see which fixes fired.",
      zh: "组件已就绪。完成一次翻译后，可在这里查看处理记录。"
    },
    "engine-status-head": {
      en: "Last run %1 · %2 process(es) · %3 patches active",
      zh: "上次翻译 %1 · %2 个进程 · %3 项组件已加载"
    },
    "engine-fix1": {
      en: "Cross-page paragraphs: %1 / %2 complete paragraphs translated jointly; %3 context fallbacks.",
      zh: "跨页段落：完整合译 %1 / %2 次；回退到分段上下文 %3 次。"
    },
    "engine-fix2": {
      en: "Terminology: %1 glossaries / %2 terms enforced, %3 violations repaired, %4 numeric style fixes.",
      zh: "术语一致：注入 %1 份术语表 / %2 条术语，纠正 %3 处违例，统一 %4 处数字写法。"
    },
    "engine-fix3": {
      en: "Tables: %1 table blocks entered translation, %2 cell paragraphs created.",
      zh: "表格：%1 个表格块进入翻译流程，生成 %2 个单元格段落。"
    },
    "engine-fix4": {
      en: "References and symbols: %1 runs demoted from formula to text, %2 corner marks recovered, %3 genuine formulas preserved.",
      zh: "引用与符号：%1 处由公式降级为可译正文，回收 %2 处角标段落，保留 %3 处真实公式。"
    },
    "engine-errors": {
      en: "Warnings: %1", zh: "警告 %1 条：%2"
    },

    "job-start": { en: "ZhiYi PDF: queued %1 file(s)", zh: "知译 PDF：已排队 %1 个文件" },
    "job-running": { en: "Translating %1…", zh: "正在翻译 %1…" },
    "job-done": { en: "ZhiYi PDF finished: %1 ok, %2 failed", zh: "知译 PDF 完成：成功 %1，失败 %2" },
    "job-failed": { en: "ZhiYi PDF failed: %1", zh: "知译 PDF 失败：%1" },
    "no-selection": {
      en: "Select one or more items that have a PDF attachment.",
      zh: "请先选中至少一个含 PDF 附件的条目。"
    },
    "no-server": {
      en: "The translation service at %1 is unreachable. Start ~/.pdf2rea/server, then set its address in Preferences → ZhiYi PDF.",
      zh: "无法连接 %1。请启动 ~/.pdf2rea/server，并在“设置 → 知译 PDF”里填写地址。"
    },
    "config-write-failed": {
      en: "Could not write the enhancement config to %1: %2. The job will run with whatever settings the server already has.",
      zh: "无法把增强配置写入 %1：%2。本次任务将沿用服务器上的旧配置。"
    }
  };

  function localeIsZh() {
    const locale = (Zotero.locale || "en").toLowerCase();
    return locale.startsWith("zh");
  }

  function t(key, ...args) {
    const entry = STRINGS[key];
    if (!entry) {
      return key;
    }
    let text = localeIsZh() ? entry.zh : entry.en;
    args.forEach((value, index) => {
      text = text.replace(new RegExp(`%${index + 1}(?:S)?(?![0-9])`, "g"), () => String(value));
    });
    if (args.length) text = text.replace(/%S/g, () => String(args[0]));
    return text;
  }

  // ---------------------------------------------------------------- prefs --
  function getPref(key, fallback) {
    const value = Zotero.Prefs.get(PREF_PREFIX + key, true);
    return value === undefined || value === null || value === "" ? fallback : value;
  }

  function setPref(key, value) {
    Zotero.Prefs.set(PREF_PREFIX + key, value, true);
  }

  const DEFAULT_PREFS = {
    new_serverip: "http://127.0.0.1:8891",
    serverDir: "",
    engine: "pdf2zh_next",
    service: "siliconflowfree",
    next_service: "siliconflowfree",
    sourceLang: "en",
    targetLang: "zh-CN",
    qps: 10,
    threadNum: 4,
    poolSize: 0,
    skipLastPages: 0,
    dualMode: "LR",
    mono: true,
    dual: true,
    noMono: false,
    noDual: false,
    noWatermark: true,
    rename: true,
    fontFamily: "auto",
    ocr: false,
    autoOcr: true,
    enhanceCompatibility: false,
    disableRichTextTranslate: false,
    onlyIncludeTranslatedPage: false,
    saveGlossary: false,
    disableGlossary: false,
    translateTableText: true,
    "mono-open": false,
    "dual-open": false,
    // 知译 PDF enhancements
    fixCrossPage: true,
    fixCrossPageStrict: true,
    fixTerminology: true,
    numericPolicy: "arabic",
    fixTables: true,
    tableCells: true,
    fixSuperscripts: true,
    glossaryPath: "",
    extraTerms: "",
    llmApis: "[]",
    fontFile: "", babeldoc: false, skipSubsetFonts: false, transFirst: false,
    skipClean: false, showProgress: true,
    "mono-cut": false, "dual-cut": false, "crop-compare": false, compare: false,
    "mono-cut-open": false, "dual-cut-open": false, "crop-compare-open": false,
    "compare-open": false, "origin-open": false, "origin-cut-open": false
  };

  function ensureDefaults() {
    Object.entries(DEFAULT_PREFS).forEach(([key, value]) => {
      if (Zotero.Prefs.get(PREF_PREFIX + key, true) === undefined) {
        setPref(key, value);
      }
    });
  }

  // -------------------------------------------------------------- request ---
  /** Field names must match Config.__init__ in ~/.pdf2rea/server/utils/config.py */
  function getServerConfig() {
    const asString = (key) => String(getPref(key, DEFAULT_PREFS[key]));
    return {
      serverUrl: asString("new_serverip").replace(/\/+$/, ""),
      engine: asString("engine"),
      service: asString("service"),
      next_service: asString("next_service"),
      sourceLang: asString("sourceLang"),
      targetLang: asString("targetLang"),
      skipLastPages: asString("skipLastPages"),
      threadNum: asString("threadNum"),
      qps: asString("qps"),
      poolSize: asString("poolSize"),
      mono: asString("mono"),
      dual: asString("dual"),
      mono_cut: asString("mono-cut"),
      dual_cut: asString("dual-cut"),
      crop_compare: asString("crop-compare"),
      compare: asString("compare"),
      babeldoc: asString("babeldoc"),
      skipSubsetFonts: asString("skipSubsetFonts"),
      fontFile: asString("fontFile"),
      fontFamily: asString("fontFamily"),
      dualMode: asString("dualMode"),
      transFirst: asString("transFirst"),
      ocr: asString("ocr"),
      autoOcr: asString("autoOcr"),
      noWatermark: asString("noWatermark"),
      saveGlossary: asString("saveGlossary"),
      disableGlossary: asString("disableGlossary"),
      noDual: asString("noDual"),
      noMono: asString("noMono"),
      skipClean: asString("skipClean"),
      disableRichTextTranslate: asString("disableRichTextTranslate"),
      enhanceCompatibility: asString("enhanceCompatibility"),
      translateTableText: asString("translateTableText"),
      onlyIncludeTranslatedPage: asString("onlyIncludeTranslatedPage")
    };
  }

  const NO_KEY_SERVICES = new Set(["siliconflowfree", "bing", "google"]);

  function activeLLMApi(service) {
    if (NO_KEY_SERVICES.has(service)) return undefined;
    let list = [];
    try {
      list = JSON.parse(getPref("llmApis", "[]")) || [];
    } catch (error) {
      list = [];
    }
    const found = list.find((api) => api.activate && api.service === service);
    if (!found) {
      return undefined;
    }
    if (service === "deepseek" && found.apiUrl && !isOfficialDeepSeekUrl(found.apiUrl)) {
      throw new Error("DeepSeek 专用接口不支持自定义 URL。请在 API 配置中改用 openailiked（OpenAI 兼容），以便按所填地址发送请求。");
    }
    return {
      service: found.service,
      model: found.model || "",
      apiKey: found.apiKey || "",
      apiUrl: found.apiUrl || "",
      extraData: found.extraData || {}
    };
  }

  function isOfficialDeepSeekUrl(url) {
    return ["https://api.deepseek.com", "https://api.deepseek.com/v1"].includes(String(url).trim().replace(/\/+$/, ""));
  }

  // ---------------------------------------------------------- file system ---
  async function exists(path) {
    try {
      return await IOUtils.exists(path);
    } catch (error) {
      Zotero.logError(error);
      return false;
    }
  }

  async function writeText(path, contents) {
    await IOUtils.writeUTF8(path, contents);
  }

  async function readText(path) {
    try {
      if (/^(?:jar:|chrome:|resource:|file:)/.test(path)) {
        const { NetUtil } = ChromeUtils.importESModule("resource://gre/modules/NetUtil.sys.mjs");
        const channel = NetUtil.newChannel({ uri: path, loadUsingSystemPrincipal: true });
        return await Zotero.File.getContentsAsync(channel, "utf-8");
      }
      return await Zotero.File.getContentsAsync(path, "utf-8");
    } catch (error) {
      return null;
    }
  }

  // Zotero 7+ dropped the old `OS` (osfile) module -- pdf2zh itself only ever uses
  // PathUtils/IOUtils. Everything path-related therefore goes through these helpers
  // with layered fallbacks, and never touches a global that may not exist.
  function joinPath(...parts) {
    if (typeof PathUtils !== "undefined" && typeof PathUtils.join === "function") {
      return PathUtils.join(parts[0], ...parts.slice(1).flatMap(part => String(part).split(/[\\/]+/).filter(Boolean)));
    }
    return parts.join("/").replace(/([^:]\/{2,})/g, "$1");
  }

  function fileLeaf(path) {
    return String(path).replace(/[\\/]+$/, "").split(/[\\/]/).pop();
  }

  function dirname(path) {
    const trimmed = String(path || "").replace(/\/+$/, "");
    const index = trimmed.lastIndexOf("/");
    if (index <= 0) {
      return index === 0 ? "/" : trimmed;
    }
    return trimmed.slice(0, index);
  }

  function homeDir() {
    if (typeof PathUtils !== "undefined" && PathUtils.homeDir) {
      return PathUtils.homeDir;
    }
    try {
      return Services.dirsvc.get("Home", Components.interfaces.nsIFile).path;
    } catch (error) { /* not available in this Gecko build */ }
    // The data directory sits directly in the user's home on macOS (~/Zotero) and
    // Windows (C:\\Users\\name\\Zotero), so one dirname gives us home.
    const data = (Zotero.DataDirectory && Zotero.DataDirectory.dir)
      || (typeof Zotero.DataDirectory === "function" ? Zotero.DataDirectory.get() : null);
    if (data) {
      return dirname(data);
    }
    if (Zotero.profile) {
      // .../home/Library/Application Support/Zotero/Profiles/<x.default>
      let dir = Zotero.profile;
      for (let i = 0; i < 4 && dir; i += 1) {
        dir = dirname(dir);
      }
      return dir;
    }
    return "";
  }

  /**
   * Locate the pdf2zh server directory. The plugin does not guess a single
   * hardcoded path: the user may keep it anywhere, so pref > well-known > probe.
   */
  async function detectServerDir() {
    const configured = String(getPref("serverDir", "") || "").trim();
    if (configured && (await exists(configured))) {
      return configured;
    }
    const home = homeDir();
    return joinPath(home, ".pdf2rea", "server");
  }

  async function requirePrivateRuntime() {
    const server = await detectServerDir();
    const marker = await readText(joinPath(server, "pdf2rea-runtime.json"));
    if (!marker || JSON.parse(marker).owner !== ADDON_ID) {
      throw new Error("请先运行 知译 PDF 的独立环境准备脚本。为保护原版，不向未标记的 server 安装补丁或写入配置。");
    }
    // The runtime builder never creates a symlinked venv. Refuse manually redirected
    // paths too, so marker placement alone cannot send writes into the stock engine.
    if (IOUtils.stat) {
      for (const path of [server, joinPath(server, "zotero-pdf2zh-next-venv")]) {
        if ((await IOUtils.stat(path)).type === "symlink") throw new Error("独立环境不能指向原版的符号链接");
      }
    }
    return server;
  }

  async function detectVenv(serverDir) {
    const names = ["zotero-pdf2zh-next-venv", "pdf2zh-next-venv", ".venv", "venv"];
    for (const name of names) {
      const venv = joinPath(serverDir, name);
      if (await exists(joinPath(venv, "bin", "python"))) {
        return venv;
      }
      if (await exists(joinPath(venv, "bin", "python3"))) {
        return venv;
      }
    }
    return null;
  }

  async function detectSitePackages(venv) {
    const libDir = joinPath(venv, "lib");
    try {
      const entries = await IOUtils.getChildren(libDir);
      const versions = entries
        .map((entry) => PathUtils.filename(entry))
        .filter((name) => name.startsWith("python"))
        .sort()
        .reverse();
      for (const version of versions) {
        const site = joinPath(libDir, version, "site-packages");
        if (await exists(site)) {
          return site;
        }
      }
    } catch (error) {
      Zotero.logError(error);
    }
    return null;
  }

  // ------------------------------------------------- enhancement settings ---
  /** Mirror of pdf2rea_engine/options.py DEFAULTS, narrowed to the UI toggles. */
  function enhancementPayload(runId) {
    return {
      enabled: true,
      run_id: runId,
      plugin_version: PLUGIN_VERSION,
      report: true,
      log_level: "info",
      cross_page: {
        mode: "joint",
        enabled: !!getPref("fixCrossPage", true),
        require_open_ending: !!getPref("fixCrossPageStrict", true),
        min_fragment_chars: 12,
        also_cross_column: true
      },
      terminology: {
        enabled: !!getPref("fixTerminology", true),
        numeric_policy: String(getPref("numericPolicy", "arabic")),
        enforce_units: true,
        keep_user_glossaries: true,
        reuse_ledger: true,
        use_builtin_glossary: true,
        glossary_files: glossaryFilePaths(),
        custom_rules: parseExtraRules()
      },
      table_text: {
        enabled: !!getPref("fixTables", true),
        treat_table_layout_as_text: true,
        cell_segmentation: !!getPref("tableCells", true),
        table_labels: ["table", "wired_table", "blocked_table"]
      },
      formula_guard: {
        enabled: !!getPref("fixSuperscripts", true),
        rescue_corner_marks: true,
        max_rescue_chars: 8
      }
    };
  }

  function parseExtraRules() {
    const raw = String(getPref("extraTerms", "") || "");
    return raw
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith("#"));
  }

  /** Where the CSV lives: user file if set, otherwise the server config dir. */
  function glossaryFilePaths() {
    const paths = [];
    const custom = String(getPref("glossaryPath", "") || "").trim();
    if (custom) {
      paths.push(custom);
    }
    return paths;
  }

  /** Merge the user's inline glossary text into a CSV we write next to config.toml. */
  function buildGlossaryCsv() {
    const lines = ["source,target,tgt_lng,avoid"];
    const custom = String(getPref("glossaryPath", "") || "").trim();
    const inline = parseExtraRules();
    inline.forEach((rule) => {
      // "recall = 召回率 | 查全率" -> source, target, avoid-variants
      const [left, right = ""] = rule.split(/[==>]/);
      const target = right.trim();
      if (!left || !target) {
        return;
      }
      const parts = target.split("|");
      lines.push([
        csvEscape(left.trim()), csvEscape(parts[0].trim()), "{lang}",
        csvEscape(parts.slice(1).join("|"))
      ].join(","));
    });
    return { content: lines.join("\n") + "\n", hasRows: lines.length > 1, custom };
  }

  function csvEscape(value) {
    const text = String(value == null ? "" : value);
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  }

  /**
   * Called before every job. The server rewrites config/config.toml itself, so we
   * put our JSON next to it -- that directory is the one the engine can find from
   * --config-file without any change to server.py.
   */
  async function publishEnhancementSettings(config) {
    const serverDir = await requirePrivateRuntime();
    const configDir = joinPath(serverDir, "config");
    const runId = `zotero-${new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 14)}-${Math.floor(Math.random() * 1e4)}`;
    const report = { serverDir, configDir, runId, ok: false, errors: [] };

    try {
      if (!(await exists(configDir))) {
        await IOUtils.makeDirectory(configDir, { ignoreExisting: true });
      }
      const glossary = buildGlossaryCsv();
      const glossaryPath = joinPath(configDir, "pdf2rea_glossary.csv");
      if (glossary.hasRows) {
        await writeText(glossaryPath,
          glossary.content.replace(/{lang}/g, config.targetLang || "zh-CN"));
      } else if (await exists(glossaryPath)) {
        await IOUtils.remove(glossaryPath);
      }

      const payload = enhancementPayload(runId);
      if (glossary.hasRows) {
        payload.terminology.glossary_files = [glossaryPath]
          .concat(glossaryFilePaths());
      }
      await writeText(joinPath(configDir, "pdf2rea.json"), JSON.stringify(payload, null, 2));
      report.ok = true;
      report.optionsPath = joinPath(configDir, "pdf2rea.json");
      report.statusPath = joinPath(configDir, "pdf2rea.status.json");
    } catch (error) {
      report.errors.push(String(error));
      Zotero.logError(error);
    }
    return report;
  }

  async function readEngineStatus(sinceMs) {
    const serverDir = await detectServerDir();
    const statusPath = joinPath(serverDir, "config", "pdf2rea.status.json");
    const eventsPath = joinPath(serverDir, "config", "pdf2rea.events.jsonl");
    const result = { statusPath, eventsPath, installed: false, run: null, processes: 0 };

    const site = await patchTarget();
    if (site) {
      result.installed = await exists(joinPath(site, "pdf2rea.pth"));
    }
    const raw = await readText(statusPath);
    if (!raw) {
      return result;
    }
    try {
      const parsed = JSON.parse(raw);
      if (sinceMs && parsed.finished_at) {
        // ignore a stale report from before this job
      }
      result.run = parsed;
      result.processes = (parsed.processes || []).length;
    } catch (error) {
      result.parseError = String(error);
    }
    return result;
  }

  async function patchTarget() {
    const serverDir = await detectServerDir();
    const venv = await detectVenv(serverDir);
    if (!venv) {
      return null;
    }
    return await detectSitePackages(venv);
  }

  // ------------------------------------------------------ patch management ---
  async function installPatchLayer() {
    await requirePrivateRuntime();
    const site = await patchTarget();
    if (!site) {
      throw new Error(t("engine-install-failed",
        "no zotero-pdf2zh-next-venv under " + (await detectServerDir())));
    }
    const manifestRaw = await readText(rootURI + "content/engine/engine_manifest.json");
    if (!manifestRaw) {
      throw new Error(t("engine-install-failed", "engine_manifest.json missing in XPI"));
    }
    const manifest = JSON.parse(manifestRaw);
    const packageDir = joinPath(site, "pdf2rea_engine");
    await IOUtils.makeDirectory(packageDir, { ignoreExisting: true });

    let copied = 0;
    for (const entry of manifest.files) {
      const contents = await readText(rootURI + "content/engine/" + entry.path);
      if (contents === null) {
        throw new Error(t("engine-install-failed", "missing " + entry.path));
      }
      if (entry.path.split("/").some(part => part === ".." || part === "")) throw new Error("invalid engine path");
      const relative = entry.path.startsWith("pdf2rea_engine/") ? entry.path : "pdf2rea_engine/" + entry.path;
      const target = joinPath(site, relative);
      await IOUtils.makeDirectory(dirname(target), { ignoreExisting: true });
      await writeText(target, contents);
      copied += 1;
    }
    await writeText(joinPath(site, "pdf2rea.pth"), "import pdf2rea_engine.bootstrap\n");

    await IOUtils.remove(joinPath(packageDir, "__pycache__"), { recursive: true, ignoreAbsent: true });
    return { site, copied };
  }

  async function uninstallPatchLayer() {
    await requirePrivateRuntime();
    const site = await patchTarget();
    if (!site) {
      throw new Error("venv site-packages not found");
    }
    const removed = [];
    for (const path of [
      joinPath(site, "pdf2rea.pth"),
      joinPath(site, "pdf2rea_engine")
    ]) {
      if (await exists(path)) {
        await IOUtils.remove(path, { recursive: true });
        removed.push(path);
      }
    }
    return { removed };
  }

  // ------------------------------------------------------------- job flow ---
  class EngineJob {
    constructor(fileName, item, config, endpoint) {
      this.fileName = fileName;
      this.item = item;
      this.config = config;
      this.endpoint = endpoint;
    }

    async run(onProgress) {
      onProgress("submitting");
      const { fileName, base64 } = await this.prepareFileData();
      const response = await this.sendRequest(fileName, base64, onProgress);
      onProgress("importing");
      await this.importResults(response);
    }

    async prepareFileData() {
      const filePath = await EngineJob.attachmentPath(this.item);
      const bytes = await IOUtils.read(filePath);
      const base64 = await EngineJob.toBase64(bytes);
      return { fileName: PathUtils.filename(filePath), base64 };
    }

    static async attachmentPath(item) {
      let attachment = null;
      if (item.isAttachment()) {
        attachment = item;
      } else if (item.isRegularItem?.()) {
        attachment = await item.getBestAttachment();
      }
      if (!attachment) {
        throw new Error("no PDF attachment");
      }
      const path = attachment.getFilePath();
      if (!path || !path.toLowerCase().endsWith(".pdf")) {
        throw new Error("attachment is not a PDF");
      }
      if (!(await exists(path))) {
        throw new Error("PDF file missing on disk: " + path);
      }
      return path;
    }

    static async toBase64(bytes) {
      const chunks = [];
      for (let index = 0; index < bytes.length; index += 8192) {
        chunks.push(String.fromCharCode.apply(null, bytes.subarray(index, index + 8192)));
      }
      return btoa(chunks.join(""));
    }

    async sendRequest(fileName, base64, onProgress) {
      const service = this.config.engine === "pdf2zh"
        ? this.config.service : this.config.next_service;
      const llmApi = activeLLMApi(service);
      const body = {
        fileName,
        fileContent: base64,
        ...this.config,
        // same async protocol flag the server uses to pick poll vs. sync reply
        asyncJob: true
      };
      if (llmApi) {
        body.llm_api = llmApi;
      }
      const response = await fetch(`${this.config.serverUrl}/${this.endpoint}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-PDF2zh-Protocol": "accepted",
          "X-PDF2zh-Plugin-Version": PLUGIN_VERSION
        },
        body: JSON.stringify(body),
        cache: "no-store"
      });
      const text = await response.text();
      let payload;
      try {
        payload = JSON.parse(text);
      } catch (error) {
        throw new Error(text.slice(0, 300) || `HTTP ${response.status}`);
      }
      if (!response.ok || payload.status === "error" || payload.status === "failed") {
        throw new Error(payload.message || `HTTP ${response.status}`);
      }
      const taskId = String(payload.taskId ?? payload.task_id ?? "");
      if (payload.status === "accepted" || (taskId && payload.status !== "success")) {
        if (!taskId) {
          throw new Error("server accepted the job but returned no taskId");
        }
        onProgress("running");
        return await this.waitForTask(taskId, onProgress);
      }
      return payload;
    }

    async fetchTaskRecord(taskId) {
      const base = this.config.serverUrl;
      try {
        const tasksResponse = await fetch(`${base}/api/tasks`, { cache: "no-store" });
        const tasksData = tasksResponse.ok ? await tasksResponse.json() : { tasks: [] };
        const match = (tasksData.tasks || []).find(
          (task) => String(task.taskId ?? task.task_id ?? "") === taskId
        );
        if (match) {
          return match;
        }
        const historyResponse = await fetch(`${base}/api/history`, { cache: "no-store" });
        const historyData = historyResponse.ok ? await historyResponse.json() : { history: [] };
        return (historyData.history || []).find(
          (task) => String(task.taskId ?? task.task_id ?? "") === taskId
        );
      } catch (error) {
        Zotero.logError(error);
        return undefined;
      }
    }

    async waitForTask(taskId, onProgress) {
      const deadline = Date.now() + 3 * 60 * 60 * 1000;
      let completedWithoutFilesSince = null;
      while (Date.now() < deadline) {
        const task = await this.fetchTaskRecord(taskId);
        const nested = task && typeof task.result === "object" ? task.result : null;
        const status = String((task && task.status) || (nested && nested.status) || "");
        const failed = ["失败", "failed", "error"].includes(status);
        if (failed) {
          throw new Error(String((nested && nested.message) || (task && task.error)
            || (task && task.message) || "translation failed"));
        }
        const files = EngineJob.stringList((nested && nested.fileList) ?? (task && task.fileList));
        const paths = EngineJob.stringList((nested && nested.filePaths) ?? (task && task.filePaths));
        const done = task && (task.finished === true || ["完成", "success"].includes(status));
        if (done && files.length) {
          return {
            status: "success",
            fileList: files,
            filePaths: paths,
            outputDir: String((nested && nested.outputDir) || (task && task.outputDir) || "")
          };
        }
        if (done && !files.length) {
          completedWithoutFilesSince ??= Date.now();
          if (Date.now() - completedWithoutFilesSince >= 20000) throw new Error("任务已结束，但服务器没有返回 PDF 文件");
        } else { completedWithoutFilesSince = null; }
        if (task && task.finished !== true) {
          onProgress("running", String(task.message || ""));
        }
        await Zotero.Promise.delay(1000);
      }
      throw new Error("timed out waiting for the translation job");
    }

    static stringList(value) {
      return Array.isArray(value) ? value.filter((item) => typeof item === "string" && item) : [];
    }

    async importResults(payload) {
      if (!payload || payload.status !== "success" || !Array.isArray(payload.fileList)) {
        throw new Error("server returned no file list");
      }
      const attachKeys = new Set();
      for (let index = 0; index < payload.fileList.length; index += 1) {
        const name = payload.fileList[index];
        const localPath = typeof payload.filePaths?.[index] === "string" ? payload.filePaths[index] : "";
        const type = EngineJob.fileType(name);
        const bytes = localPath && (await exists(localPath))
          ? await IOUtils.read(localPath)
          : await this.download(name);
        EngineJob.validatePdf(bytes, name);
        const tempPath = joinPath(PathUtils.tempDir,
          `pdf2rea-${Date.now()}-${index}-${fileLeaf(name)}`);
        await IOUtils.write(tempPath, bytes);
        try {
          await this.attach(tempPath, name, type, attachKeys);
        } finally {
          try {
            await IOUtils.remove(tempPath);
          } catch (error) {
            Zotero.logError(error);
          }
        }
      }
    }

    async download(fileName) {
      let lastError;
      for (let attempt = 0; attempt < 3; attempt++) {
        try { return await this.downloadOnce(fileName); }
        catch (error) { lastError = error; if (attempt < 2) await Zotero.Promise.delay(1000); }
      }
      throw lastError;
    }

    async downloadOnce(fileName) {
      const url = `${this.config.serverUrl}/translatedFile/${encodeURIComponent(fileName)}`;
      const response = await fetch(url, { method: "GET" });
      if (!response.ok) {
        throw new Error(`download failed HTTP ${response.status}: ${fileName}`);
      }
      const buffer = await response.arrayBuffer();
      const bytes = new Uint8Array(buffer);
      EngineJob.validatePdf(bytes, fileName);
      return bytes;
    }

    static validatePdf(bytes, fileName) {
      const needle = [0x25, 0x50, 0x44, 0x46, 0x2d]; // %PDF-
      let header = false;
      for (let index = 0; index < Math.min(1023, bytes.length - needle.length) + 1; index += 1) {
        if (needle.every((byte, offset) => bytes[index + offset] === byte)) {
          header = true;
          break;
        }
      }
      if (!header) {
        throw new Error("downloaded content is not a PDF: " + fileName);
      }
    }

    static fileType(fileName) {
      if (fileName.includes("mono-cut.pdf")) return "mono-cut";
      if (fileName.includes("dual-cut.pdf")) return "dual-cut";
      if (fileName.includes("crop-compare.pdf")) return "crop-compare";
      if (fileName.includes("compare.pdf")) return "compare";
      if (fileName.includes("mono.pdf")) return "mono";
      if (fileName.includes("dual.pdf")) return "dual";
      if (/(?:\.cut|-cut|origin-cut)\.pdf$/i.test(fileName)) return "origin-cut";
      return "origin";
    }

    async attach(filePath, fileName, type, attachKeys) {
      const item = this.item;
      const parentItemID = item.isAttachment() && item.parentItemID
        ? item.parentItemID : (item.isRegularItem?.() ? item.id : undefined);
      let target = item;
      if (item.isAttachment() && item.parentItemID) {
        target = Zotero.Items.get(item.parentItemID);
      }
      const leaf = Zotero.File.getValidFileName(fileLeaf(fileName)) || "translated.pdf";
      const service = this.config.engine === "pdf2zh"
        ? this.config.service : this.config.next_service;
      const shortTitle = target.getField ? target.getField("shortTitle") : "";
      const title = getPref("rename", true)
        ? `${shortTitle ? shortTitle + "-" : ""}ZhiYi-PDF-${service}-${type}`
        : leaf;
      const key = `${parentItemID || "none"}::${service}::${type}::${leaf}`;
      if (attachKeys.has(key)) {
        return false;
      }
      const attachment = await Zotero.Attachments.importFromFile({
        file: filePath,
        parentItemID,
        libraryID: item.libraryID,
        fileBaseName: leaf.replace(/\.pdf$/i, ""),
        title
      });
      if (attachment && attachment.id) {
        attachKeys.add(key);
        if (getPref(`${type}-open`, false)) {
          try {
            Zotero.Reader.open(attachment.id);
          } catch (error) {
            Zotero.logError(error);
          }
        }
        return true;
      }
      return false;
    }
  }

  // -------------------------------------------------------------- driver ----
  const state = { running: false, jobs: [] };

  async function processWorker(endpoint) {
    if (state.running) {
      alert("知译 PDF: a job is already running. Wait for it or restart Zotero to clear it.");
      return;
    }
    const win = Zotero.getMainWindow?.() || Services.wm.getMostRecentWindow("navigator:browser");
    const pane = win && win.ZoteroPane;
    const items = pane ? pane.getSelectedItems() : [];
    if (!items.length) {
      win.alert(t("no-selection"));
      return;
    }

    const config = getServerConfig();
    await ensureLocalService(config.serverUrl);
    if (!(await probeServer(config.serverUrl))) {
      win.alert(t("no-server", config.serverUrl));
      return;
    }

    const tasks = [];
    for (const item of items) {
      try {
        await EngineJob.attachmentPath(item);
        tasks.push(new EngineJob("", item, config, endpoint));
      } catch (error) {
        win.alert(`${item.getDisplayTitle?.() || item.libraryID}: ${error.message}`);
      }
    }
    if (!tasks.length) {
      return;
    }

    const published = await publishEnhancementSettings(config);
    if (!published.ok) throw new Error("增强配置写入失败：" + published.errors.join("; "));

    state.running = true;
    const progress = new ProgressReporter(tasks.length);
    let ok = 0;
    let failed = 0;
    try {
      for (const task of tasks) {
        try {
          await task.run((phase, detail) => progress.update(task, phase, detail));
          ok += 1;
          progress.done(task);
        } catch (error) {
          failed += 1;
          progress.fail(task, error);
          Zotero.logError(error);
        }
      }
    } catch (error) {
      progress.finish(ok, failed, error);
      state.running = false;
      return;
    }
    try {
      const engineStatus = await readEngineStatus();
      progress.finish(ok, failed, null, engineStatus);
    } finally { state.running = false; }
  }

  async function probeServer(serverUrl) {
    try {
      const response = await fetch(`${serverUrl.replace(/\/+$/, "")}/health`, {
        cache: "no-store"
      });
      return response.ok;
    } catch (error) {
      return false;
    }
  }

  let serviceStartPromise = null;

  async function ensureLocalService(serverUrl = getServerConfig().serverUrl) {
    const server = await requirePrivateRuntime();
    const marker = JSON.parse(await readText(joinPath(server, "pdf2rea-runtime.json")));
    const expected = `http://127.0.0.1:${marker.server_port || 8891}`;
    if (serverUrl.replace(/\/+$/, "") !== expected) {
      throw new Error(`此插件仅使用独立环境的地址 ${expected}，请检查 Server 地址，勿填写原版 8890`);
    }
    if (await probeServer(serverUrl)) return { ok: true, status: "running" };
    if (serviceStartPromise) return serviceStartPromise;
    serviceStartPromise = (async () => {
      const script = await readText(rootURI + "content/runtime/service_control.py");
      if (!script) throw new Error("安装包缺少独立服务启动器");
      const helper = joinPath(server, "pdf2rea_service_control.py");
      await writeText(helper, script);
      const { Subprocess } = ChromeUtils.importESModule("resource://gre/modules/Subprocess.sys.mjs");
      const proc = await Subprocess.call({
        command: joinPath(server, "zotero-pdf2zh-next-venv", "bin", "python"),
        arguments: ["-B", helper, "start", "--runtime", server],
        stderr: "stdout"
      });
      const output = await proc.stdout.readString();
      await proc.wait();
      let result;
      try { result = JSON.parse(output.trim()); }
      catch (_) { throw new Error("独立服务启动器没有返回有效结果"); }
      if (!result.ok) throw new Error(result.message || "独立服务启动失败");
      return result;
    })();
    try { return await serviceStartPromise; }
    finally { serviceStartPromise = null; }
  }

  function refreshServiceSummary(win) {
    const host = win.document.getElementById("pdf2rea-service-summary");
    if (!host) return;
    const config = getServerConfig();
    const service = config.engine === "pdf2zh" ? config.service : config.next_service;
    if (NO_KEY_SERVICES.has(service)) {
      host.textContent = `当前选择：${service}。不发送已保存的 DeepSeek 或其他服务的 API Key / URL，也不会自动切换到付费服务。免费服务可能限流或暂时不可用。`;
    } else {
      let api;
      try { api = activeLLMApi(service); }
      catch (error) { host.textContent = error.message; return; }
      host.textContent = api ? `当前选择：${service} / ${api.model || "默认模型"}。仅使用该服务已激活的配置；费用按所填 API 服务计费。`
        : `当前选择：${service}。请在“翻译服务”中保存并激活对应配置。`;
    }
  }

  /** Zotero's own progress window -- one row per file, no custom chrome needed. */
  class ProgressReporter {
    constructor(total) {
      this.total = total;
      this.window = null;
      this.rows = [];
      if (!getPref("showProgress", true)) return;
      try {
        this.window = new Zotero.ProgressWindow({ closeOnClick: false });
        this.window.changeHeadline("知译 PDF");
        this.window.show();
      } catch (error) {
        Zotero.logError(error);
        this.window = null;
      }
      this.shown = Date.now();
    }

    row(task, phase, detail) {
      if (!this.window) {
        return;
      }
      let entry = this.rows.find((item) => item.task === task);
      if (!entry) {
        entry = {
          task,
          item: new this.window.ItemProgress(
            "application/pdf", task.item.getDisplayTitle?.() || t("job-running", task.fileName)
          )
        };
        this.rows.push(entry);
      }
      entry.item.setProgress(100);
    }

    update(task, phase, detail) {
      if (!this.window) {
        return;
      }
      const label = `${task.item.getDisplayTitle?.() || ""} · ${phase}${detail ? " · " + detail : ""}`;
      let entry = this.rows.find((item) => item.task === task);
      if (!entry) {
        entry = {
          task,
          item: new this.window.ItemProgress("application/pdf", label)
        };
        this.rows.push(entry);
      } else {
        entry.item.setText(label);
      }
      entry.item.setProgress(50);
    }

    done(task) {
      const entry = this.rows.find((item) => item.task === task);
      if (entry) {
        entry.item.setProgress(100);
      }
    }

    fail(task, error) {
      const entry = this.rows.find((item) => item.task === task);
      if (entry) {
        entry.item.setError();
        entry.item.setText(`${task.item.getDisplayTitle?.() || ""}: ${error.message}`);
      } else if (this.window) {
        const created = new this.window.ItemProgress("", error.message);
        created.setError();
      }
    }

    finish(ok, failed, error, engineStatus) {
      if (!this.window) {
        return;
      }
      const lines = [t("job-done", ok, failed)];
      if (error) {
        lines.push(String(error.message || error));
      }
      if (engineStatus && engineStatus.run) {
        lines.push("处理记录可在设置 → 知译 PDF → 连接与诊断中查看。");
        const errors = engineStatus.run.errors || [];
        if (errors.length) lines.push(t("engine-errors", errors.length, errors[0]));
      }
      lines.forEach((line) => {
        try {
          this.window.addDescription(line);
        } catch (inner) {
          Zotero.logError(inner);
        }
      });
      this.window.startCloseTimer(8000);
    }
  }

  // ----------------------------------------------------------------- ui -----
  function registerMainWindow(win) {
    const document = win.document;
    const menu = document.getElementById("zotero-itemmenu");
    if (!menu || document.getElementById("pdf2rea-itemmenu")) {
      return;
    }
    const submenu = document.createXULElement("menu");
    submenu.id = "pdf2rea-itemmenu";
    submenu.setAttribute("label", t("menu-title"));
    submenu.classList.add("menu-iconic");
    submenu.setAttribute("image", `${rootURI}content/icons/favicon@0.5x.svg`);

    const popup = document.createXULElement("menupopup");
    popup.id = "pdf2rea-itemmenu-popup";
    [
      ["translate", "menu-translate"],
      ["crop", "menu-crop"],
      ["compare", "menu-compare"],
      ["crop-compare", "menu-crop-compare"]
    ].forEach(([endpoint, key]) => {
      const item = document.createXULElement("menuitem");
      item.id = `pdf2rea-menu-${endpoint}`;
      item.setAttribute("label", `${t("menu-title")}: ${t(key)}`);
      item.addEventListener("command", () => processWorker(endpoint).catch(error => { Zotero.logError(error); win.alert(error.message); }));
      popup.appendChild(item);
    });

    submenu.appendChild(popup);
    // Zotero builds native menu actions by child index. Appending preserves those
    // indices; inserting at the beginning hides this menu and relabels native ones.
    menu.appendChild(submenu);
  }

  // ------------------------------------------------------- preferences UI ---
  /**
   * The pane's <groupbox onload=...> hands us its window. Controls already use
   * Zotero's own `preference="..."` binding, so only buttons and read-only
   * summaries need JavaScript here.
   */
  function registerPrefsScripts(win) {
    const doc = win.document;
    const root = doc.getElementById("pdf2rea-pref-root");
    if (root?.dataset.pdf2reaInitialized === "true") return;
    if (root) root.dataset.pdf2reaInitialized = "true";
    const tabs = [...doc.querySelectorAll("[data-pdf2rea-tab]")];
    const panels = [...doc.querySelectorAll("[data-pdf2rea-panel]")];
    let activeSection = "general";
    const selectSection = (name, focus = false) => {
      activeSection = name;
      for (const tab of tabs) {
        const selected = tab.dataset.pdf2reaTab === name;
        tab.setAttribute("aria-selected", String(selected));
        tab.tabIndex = selected ? 0 : -1;
        if (selected && focus) tab.focus();
      }
      for (const panel of panels) panel.hidden = panel.dataset.pdf2reaPanel !== name;
    };
    tabs.forEach((tab, index) => {
      tab.addEventListener("click", () => selectSection(tab.dataset.pdf2reaTab));
      tab.addEventListener("keydown", event => {
        const rtl = doc.documentElement.dir === "rtl";
        let next = index;
        if (event.key === "ArrowRight") next += rtl ? -1 : 1;
        else if (event.key === "ArrowLeft") next += rtl ? 1 : -1;
        else if (event.key === "Home") next = 0;
        else if (event.key === "End") next = tabs.length - 1;
        else return;
        event.preventDefault();
        selectSection(tabs[(next + tabs.length) % tabs.length].dataset.pdf2reaTab, true);
      });
    });
    selectSection(activeSection);
    // Zotero searches all settings panes. Reveal every section for native search,
    // then restore our selected section when navigating back to the pane.
    root?.addEventListener("showing", () => {
      const searching = !!win.Zotero_Preferences?.searchField?.value?.trim();
      const navigation = root.querySelector(".p2r-tabs");
      if (navigation) navigation.hidden = searching;
      if (searching) panels.forEach(panel => { panel.hidden = false; });
      else selectSection(activeSection);
    });
    const versionBanner = doc.getElementById("pdf2rea-version-banner");
    if (versionBanner) versionBanner.textContent = `知译 PDF ${PLUGIN_VERSION}`;
    const loadedVersion = doc.getElementById("pdf2rea-loaded-version");
    if (loadedVersion) loadedVersion.textContent = `插件版本 ${PLUGIN_VERSION} · 设置自动保存`;
    const on = (id, event, handler) => {
      const node = doc.getElementById(id);
      if (node) {
        node.addEventListener(event === "command" ? "click" : event, handler);
      }
      return node;
    };

    refreshEnginePanel(win).catch((error) => {
      const pill = doc.getElementById("pdf2rea-engine-pill");
      if (pill) {
        pill.textContent = String(error.message || error);
        pill.setAttribute("data-state", "missing");
      }
    });
    refreshApiList(win);
    refreshServiceSummary(win);
    for (const id of ["pdf2rea-engine", "pdf2rea-next_service"]) {
      on(id, "change", () => setTimeout(() => refreshServiceSummary(win), 0));
    }

    on("pdf2rea-btn-probe", "command", async () => {
      const state = doc.getElementById("pdf2rea-probe-state");
      state.textContent = "…";
      try {
        const url = String(getPref("new_serverip", DEFAULT_PREFS.new_serverip));
        await ensureLocalService(url.replace(/\/+$/, ""));
        const response = await fetch(`${url.replace(/\/+$/, "")}/health`, { cache: "no-store" });
        const payload = await response.json();
        state.textContent = payload.status === "ok" ? `✓ 已连接 · 知译 PDF ${PLUGIN_VERSION}` : "✗ 服务返回异常";
        state.setAttribute("data-state", payload.status === "ok" ? "ok" : "missing");
      } catch (error) {
        state.textContent = "✗ " + (error.message || "无法连接");
        state.setAttribute("data-state", "missing");
      }
    });

    on("pdf2rea-btn-start-service", "command", () => doc.getElementById("pdf2rea-btn-probe").click());

    on("pdf2rea-btn-pickdir", "command", async () => {
      // Auto-detect fills the field when it is empty; the user may still type a path.
      const detected = await detectServerDir();
      const field = doc.getElementById("pdf2rea-serverDir");
      if (!String(field.value || "").trim()) {
        field.value = detected;
      }
      setPref("serverDir", field.value);
    });

    on("pdf2rea-btn-pickcsv", "command", async () => {
      const file = await pickFile(win, [".csv"]);
      if (file) {
        doc.getElementById("pdf2rea-glossaryPath").value = file;
        setPref("glossaryPath", file);
      }
    });

    on("pdf2rea-btn-source", "command", () => Zotero.launchURL("https://github.com/giaogiaogiaoluan/zhiyi-pdf"));
    on("pdf2rea-btn-upstream", "command", () => Zotero.launchURL("https://github.com/guaguastandup/zotero-pdf2zh"));
    on("pdf2rea-btn-license", "command", () => Zotero.launchURL("https://github.com/giaogiaogiaoluan/zhiyi-pdf/blob/main/LICENSE"));

    on("pdf2rea-btn-api-save", "command", () => saveApiFromForm(win));
    on("pdf2rea-btn-api-clear", "command", () => clearApiForm(win));

    on("pdf2rea-btn-engine-install", "command", async () => {
      const pill = doc.getElementById("pdf2rea-engine-pill");
      pill.textContent = "安装中…";
      try {
        const result = await installPatchLayer();
        pill.textContent = t("engine-installed");
        pill.setAttribute("data-state", "ok");
        doc.getElementById("pdf2rea-engine-target").textContent =
          t("engine-install-done", result.site) + ` (${result.copied} files)`;
      } catch (error) {
        pill.textContent = t("engine-install-failed", String(error.message || error));
        pill.setAttribute("data-state", "missing");
      }
    });

    on("pdf2rea-btn-engine-remove", "command", async () => {
      const pill = doc.getElementById("pdf2rea-engine-pill");
      try {
        const result = await uninstallPatchLayer();
        pill.textContent = t("engine-not-installed");
        pill.setAttribute("data-state", "missing");
        doc.getElementById("pdf2rea-engine-target").textContent =
          t("engine-remove-done") + ` (${result.removed.length} paths)`;
      } catch (error) {
        pill.textContent = String(error.message || error);
      }
    });

    on("pdf2rea-btn-engine-refresh", "command", () => refreshEnginePanel(win));
  }

  async function pickFile(win, extensions) {
    const picker = Components.classes["@mozilla.org/filepicker;1"].createInstance(
      Components.interfaces.nsIFilePicker
    );
    picker.init(win.browsingContext, "知译 PDF", Components.interfaces.nsIFilePicker.modeOpen);
    (extensions || []).forEach((extension) => picker.appendFilter(extension, `*${extension}`));
    const result = await new Promise(resolve => picker.open(resolve));
    if (result !== Components.interfaces.nsIFilePicker.returnOK) {
      return null;
    }
    return picker.file.path;
  }

  function readApiList() {
    try {
      const list = JSON.parse(getPref("llmApis", "[]"));
      return Array.isArray(list) ? list : [];
    } catch (error) {
      return [];
    }
  }

  let editingApiKey = null;

  function saveApiFromForm(win) {
    const doc = win.document;
    const service = (doc.getElementById("pdf2rea-api-service").value || "").trim();
    if (!service) {
      doc.getElementById("pdf2rea-api-state").textContent = "请填写服务名";
      return;
    }
    let extraData;
    try {
      extraData = JSON.parse(doc.getElementById("pdf2rea-api-extra").value || "{}");
      if (!extraData || Array.isArray(extraData) || typeof extraData !== "object") throw new Error();
    } catch (_) { doc.getElementById("pdf2rea-api-state").textContent = "额外参数需要有效的 JSON 对象"; return; }
    const apiUrl = (doc.getElementById("pdf2rea-api-url").value || "").trim();
    if (service === "deepseek" && apiUrl && !isOfficialDeepSeekUrl(apiUrl)) {
      doc.getElementById("pdf2rea-api-state").textContent = "自定义 URL 请把服务名改为 openailiked（OpenAI 兼容）；DeepSeek 专用服务固定使用官方接口。";
      return;
    }
    const entry = {
      key: editingApiKey || Zotero.Utilities.generateObjectKey(),
      service,
      model: (doc.getElementById("pdf2rea-api-model").value || "").trim(),
      apiUrl: (doc.getElementById("pdf2rea-api-url").value || "").trim(),
      apiKey: (doc.getElementById("pdf2rea-api-key").value || "").trim(),
      activate: true,
      extraData
    };
    const list = readApiList().filter(item => item.key !== editingApiKey)
      .map(item => item.service === service ? { ...item, activate: false } : item);
    list.push(entry);
    setPref("llmApis", JSON.stringify(list));
    setPref("next_service", service);
    setPref("service", service);
    editingApiKey = entry.key;
    doc.getElementById("pdf2rea-api-state").textContent = `已保存 ${service}`;
    refreshApiList(win);
    refreshServiceSummary(win);
  }

  function clearApiForm(win) {
    editingApiKey = null;
    const extra = win.document.getElementById("pdf2rea-api-extra");
    if (extra) extra.value = "{}";
    ["api-service", "api-model", "api-url", "api-key"].forEach((id) => {
      const node = win.document.getElementById(`pdf2rea-${id}`);
      if (node) {
        node.value = "";
      }
    });
  }

  function refreshApiList(win) {
    const host = win.document.getElementById("pdf2rea-api-list");
    if (!host) {
      return;
    }
    const list = readApiList();
    host.textContent = list.length ? "" : "尚未配置任何 API。";
    for (const entry of list) {
      const row = win.document.createElementNS("http://www.w3.org/1999/xhtml", "div");
      row.textContent = `${entry.service} · ${entry.model || "默认模型"} · ${entry.activate ? "已启用" : "未启用"} `;
      const button = (label, action) => {
        const node = win.document.createElementNS("http://www.w3.org/1999/xhtml", "button");
        node.textContent = label;
        node.addEventListener("click", action);
        row.appendChild(node);
      };
      button("编辑", () => {
        editingApiKey = entry.key;
        for (const [id, key] of [["service", "service"], ["model", "model"], ["url", "apiUrl"], ["key", "apiKey"]]) {
          win.document.getElementById(`pdf2rea-api-${id}`).value = entry[key] || "";
        }
        win.document.getElementById("pdf2rea-api-extra").value = JSON.stringify(entry.extraData || {}, null, 2);
      });
      button(entry.activate ? "停用" : "激活", () => {
        setPref("llmApis", JSON.stringify(readApiList().map(item => ({ ...item,
          activate: item.key === entry.key ? !entry.activate : item.service === entry.service ? false : item.activate }))));
        if (!entry.activate) { setPref("next_service", entry.service); setPref("service", entry.service); }
        refreshApiList(win);
        refreshServiceSummary(win);
      });
      button("删除", () => {
        setPref("llmApis", JSON.stringify(readApiList().filter(item => item.key !== entry.key)));
        if (editingApiKey === entry.key) clearApiForm(win);
        refreshApiList(win);
        refreshServiceSummary(win);
      });
      host.appendChild(row);
    }
  }

  async function refreshEnginePanel(win) {
    const doc = win.document;
    const pill = doc.getElementById("pdf2rea-engine-pill");
    const target = doc.getElementById("pdf2rea-engine-target");
    const statusHost = doc.getElementById("pdf2rea-engine-status");
    const site = await patchTarget();
    const serverDir = await detectServerDir();
    target.textContent = `服务目录：${serverDir}` + (site ? `\n组件位置：${site}`
      : "\n未找到翻译引擎，请先准备本机服务环境。");

    const installed = site ? await exists(joinPath(site, "pdf2rea.pth")) : false;
    let installedVersion = "未知";
    if (installed) {
      try {
        const source = await IOUtils.readUTF8(joinPath(site, "pdf2rea_engine", "__init__.py"));
        installedVersion = source.match(/__version__\s*=\s*["']([^"']+)/)?.[1] || "未知";
      } catch (error) { Zotero.logError(error); }
    }
    pill.textContent = installed ? `${t("engine-installed")} ${installedVersion}` : t("engine-not-installed");
    pill.setAttribute("data-state", installed ? "ok" : "missing");

    const report = await readEngineStatus();
    if (!report.run) {
      statusHost.textContent = installed ? t("engine-not-run-yet") : "—";
      return;
    }
    const run = report.run;
    const stats = run.stats || {};
    const lines = [
      `插件 ${PLUGIN_VERSION} · 上次翻译使用的增强组件 ${run.versions?.pdf2rea_engine || "未知"}`,
      `文本结构：连接 ${stats.layout_repair?.inline_fragments || 0} 处断裂片段、${stats.layout_repair?.continued_lines || 0} 行续文。`,
      `列表与缩略语：${stats.structured_text?.list_rows || 0} 个列表条目、${stats.structured_text?.definition_rows || 0} 行两列定义。`,
      `已拦截异常输出：${stats.response_guard?.rejected || 0} 次（保留原文）。`,
      t("engine-status-head", run.finished_at || run.loaded_at || "?",
        (run.processes || []).length || report.processes, (run.patches || []).length)
    ];
    lines.push(t("engine-fix1", stats.cross_page?.joined || 0, stats.cross_page?.joined_requests || 0, stats.cross_page?.context_fallbacks || 0));
    const terms = stats.terminology || {};
    const termStats = terms.stats || {};
    lines.push(t("engine-fix2", (terms.glossaries || []).length, terms.terms || 0,
      termStats.enforced || 0, termStats.normalized || 0));
    const table = stats.table_text || {};
    lines.push(t("engine-fix3", table.table_blocks || 0, table.cell_paragraphs || 0));
    const formula = stats.formula_guard || {};
    lines.push(t("engine-fix4", formula.rescued || 0, formula.recovered_corner_marks || 0,
      formula.kept_as_formula || 0));
    if ((run.errors || []).length) {
      lines.push(t("engine-errors", run.errors.length, run.errors.slice(0, 3).join(" | ")));
    }
    statusHost.textContent = lines.join("\n");
    statusHost.style.whiteSpace = "pre-wrap";
  }

  const hooks = {
    async onStartup(bootRootURI) {
      await Promise.all([
        Zotero.initializationPromise, Zotero.unlockPromise, Zotero.uiReadyPromise
      ]);
      if (bootRootURI) {
        // keep a reference for chrome:// path resolution in dialogs
        Zotero.pdf2rea.rootURI = bootRootURI;
      }
      ensureDefaults();
      try {
        if (Zotero.PreferencePanes.pluginPanes?.some(pane => pane.id === "pdf2rea-preferences" && pane.pluginID === ADDON_ID)) {
          Zotero.PreferencePanes.unregister("pdf2rea-preferences");
        }
        await Zotero.PreferencePanes.register({
          id: "pdf2rea-preferences",
          pluginID: ADDON_ID,
          src: `${rootURI}content/preferences.xhtml`,
          label: t("prefs-title"),
          image: `${rootURI}content/icons/favicon.svg`
        });
      } catch (error) {
        Zotero.logError(error);
      }
      Zotero.getMainWindows().forEach(registerMainWindow);
      ensureLocalService().catch(error => Zotero.logError(error));
    },

    onMainWindowLoad(win) {
      registerMainWindow(win);
    },

    onMainWindowUnload() {
      /* menus live in the window document; they disappear with it */
    },

    onShutdown() {
      Zotero.PreferencePanes.unregister?.("pdf2rea-preferences");
      Zotero.getMainWindows().forEach(win => win.document.getElementById("pdf2rea-itemmenu")?.remove());
      delete Zotero.pdf2rea;
    },

    onPrefsEvent(type, data) {
      if (type === "load") {
        registerPrefsScripts(data.window);
      }
    },

    // Menu items route here; engine patch management lives in Preferences -> 知译 PDF.
    onDialogEvents(type) {
      return processWorker(type);
    }
  };

  const addon = {
    data: { alive: true, config: { addonID: ADDON_ID, addonRef: ADDON_REF, pluginVersion: PLUGIN_VERSION, rootURI } },
    hooks,
    api: {
      processWorker,
      t,
      getPref,
      setPref,
      getServerConfig,
      enhancementPayload,
      publishEnhancementSettings,
      readEngineStatus,
      installPatchLayer,
      uninstallPatchLayer,
      requirePrivateRuntime,
      detectServerDir,
      patchTarget,
      probeServer,
      ensureLocalService,
      EngineJob
    }
  };

  Zotero.pdf2rea = addon;
})();
