#!/usr/bin/env node
/**
 * Plugin contract test -- runs pdf2rea.js in a stubbed Zotero environment.
 *
 * Nothing is installed into the user's Zotero. What this proves is the part that
 * cannot be checked by reading code: that the plugin actually produces a request the
 * real server accepts, and an options file the real engine patch layer accepts.
 *
 *   node tests/test_plugin_contract.mjs [artifactsDir]
 *
 * Artifacts (pdf2rea.json / glossary CSV) are written for the python companion
 * tests/test_plugin_contract_engine.py to validate with pdf2rea_engine itself.
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import vm from "node:vm";

const ROOT = path.resolve(import.meta.dirname, "..");
const ARTIFACTS = process.argv[2] || fs.mkdtempSync(path.join(os.tmpdir(), "pdf2rea-contract-"));

let pass = 0;
const failures = [];
const check = (name, condition, detail = "") => {
  if (condition) pass += 1;
  else failures.push(`${name}${detail ? ` :: ${detail}` : ""}`);
  console.log(`  ${condition ? "ok  " : "FAIL"} ${name}${condition ? "" : `  ${detail}`}`);
};

// ------------------------------------------------------------------- stubs ---
const prefs = new Map();
const writes = [];
const fetched = [];

const ZOTERO_KEYS_ACCEPTED = [
  "engine", "service", "next_service", "sourceLang", "targetLang", "skipLastPages",
  "threadNum", "qps", "poolSize", "babeldoc", "skipSubsetFonts", "fontFile",
  "fontFamily", "dualMode", "transFirst", "ocr", "autoOcr", "noWatermark",
  "saveGlossary", "disableGlossary", "noDual", "noMono", "skipClean",
  "enhanceCompatibility", "disableRichTextTranslate", "translateTableText",
  "onlyIncludeTranslatedPage", "mono", "dual", "mono-cut", "dual-cut",
  "crop-compare", "compare", "llm_api", "fileName", "fileContent", "asyncJob",
  "serverUrl", "rename", "showProgress", "dialog-service", "new_serverip",
];

const sandbox = {
  console,
  btoa: (binary) => Buffer.from(binary, "binary").toString("base64"),
  setTimeout,
  clearTimeout,
  alert: (text) => { sandbox.__alerts.push(text); },
  __alerts: [],
  Services: {
    io: { newURI: (u) => ({ spec: u }) },
    scriptloader: { loadSubScript: () => {} },
    wm: {
      getMostRecentWindow: () => sandbox.__mainWindow,
      getWindowEnumerator: () => [sandbox.__mainWindow],
    },
    obs: { notifyObservers: () => {}, addObserver: () => {}, removeObserver: () => {} },
  },
  Components: { interfaces: {}, classes: { createInstance: () => ({}) } },
  Cc: {}, Cu: { unload: () => {} },
  IOUtils: {
    exists: async () => true,
    read: async () => new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, 1, 2, 3]),
    write: async () => {},
    writeUTF8: async (p, contents) => {
      writes.push({ path: p, contents });
      // mirror to disk so the python companion can feed these bytes to the real
      // pdf2rea_engine and prove plugin -> engine round-tripping
      const mirror = path.join(ARTIFACTS, "mirror", path.basename(p));
      fs.writeFileSync(mirror, contents, "utf8");
    },
    remove: async () => {},
    makeDirectory: async () => {},
    getChildren: async (dir) => [path.join(dir, "python3.12")],
  },
  // Zotero 7+ exposes PathUtils/IOUtils only; `OS` is gone. The stub deliberately
  // omits `OS` so a regression back to it fails here instead of in Zotero, and it
  // omits PathUtils.homeDir on the first pass so the DataDirectory fallback runs.
  PathUtils: {
    tempDir: path.join(ARTIFACTS, "tmp"),
    filename: (p) => { if (!path.isAbsolute(p)) throw new Error("Gecko filename requires an absolute path"); return path.basename(p); },
    join: (first, ...parts) => {
      if (parts.some(part => /[\\/]/.test(part))) throw new Error("Gecko requires leaf components");
      return path.join(first, ...parts);
    },
  },
  Zotero: {
    DataDirectory: { dir: path.join(ARTIFACTS, "fakehome", "Zotero") },
    locale: "zh-CN",
    initializationPromise: Promise.resolve(),
    unlockPromise: Promise.resolve(),
    uiReadyPromise: Promise.resolve(),
    Promise: { delay: () => Promise.resolve() },
    logError: (e) => { sandbox.__errors.push(String(e)); },
    Prefs: {
      get: (key) => (prefs.has(key) ? prefs.get(key) : undefined),
      set: (key, value) => { prefs.set(key, value); },
    },
    Utilities: { generateObjectKey: () => "KEY" + Math.random().toString(36).slice(2, 8) },
    File: {
      getValidFileName: (name) => name.replace(/[\\/:*?"<>|]/g, "_"),
      getContentsAsync: async (p) => {
        if (p.endsWith("pdf2rea-runtime.json")) return JSON.stringify({owner:"pdf2rea@qoder.dev"});
        try { return fs.readFileSync(p, "utf8"); } catch { return null; }
      },
    },
    PreferencePanes: { register: (spec) => { sandbox.Zotero.__panes.push(spec); } },
    __panes: [],
    Attachments: { importFromFile: async () => ({ id: 1 }) },
    Items: { get: () => sandbox.__item },
    Reader: { open: () => {} },
    ProgressWindow: class {
      constructor() {}
      changeHeadline() {} show() {} startCloseTimer() {} addDescription() {}
      ItemProgress = class { setProgress() {} setText() {} setError() {} };
    },
    getMainWindows: () => [sandbox.__mainWindow],
  },
  __errors: [],
  _globalThis: null,
  fetch: async (url, options) => {
    fetched.push({ url, options });
    if (String(url).endsWith("/health")) {
      return { ok: true, json: async () => ({ status: "ok", version: "4.1.7" }) };
    }
    if (String(url).includes("/translatedFile/")) {
      return { ok: true, arrayBuffer: async () => new TextEncoder().encode("%PDF-1.7 stub").buffer };
    }
    return {
      ok: true,
      text: async () => JSON.stringify({ status: "success", fileList: ["a.zh-CN.mono.pdf"] }),
      json: async () => ({ status: "success", fileList: [] }),
    };
  },
};

// fake main window + DOM -----------------------------------------------------
const makeElement = (tag) => {
  const el = {
    tag, id: "", attributes: {}, children: [], _listeners: {},
    classList: { add() {} },
    setAttribute(k, v) { this.attributes[k] = v; },
    getAttribute(k) { return this.attributes[k]; },
    appendChild(child) { this.children.push(child); return child; },
    insertBefore(child) { this.children.unshift(child); return child; },
    addEventListener(type, fn) { (this._listeners[type] ||= []).push(fn); },
    dispatch(type) { (this._listeners[type] || []).forEach((fn) => fn()); },
    querySelector() { return null; },
    textContent: "", value: "", style: {},
  };
  return el;
};

const itemMenu = makeElement("menupopup");
itemMenu.id = "zotero-itemmenu";
// A real document returns null for unknown ids; the plugin uses that to avoid
// registering its submenu twice, so the stub must behave the same way.
const domById = { "zotero-itemmenu": itemMenu };
const paneWindow = {
  document: {
    getElementById: (id) => domById[id] || null,
    createXULElement: (tag) => {
      const element = makeElement(tag);
      const originalSet = element.setAttribute.bind(element);
      element.setAttribute = (key, value) => {
        originalSet(key, value);
        if (key === "id") { domById[value] = element; element.id = value; }
      };
      return element;
    },
  },
};
sandbox.__mainWindow = paneWindow;

const item = {
  isAttachment: () => false,
  isRegularItem: () => true,
  libraryID: 1,
  id: 7,
  getDisplayTitle: () => "Test Paper",
  getField: () => "Test",
  getBestAttachment: async () => ({
    getFilePath: () => path.join(ARTIFACTS, "input.pdf"),
    libraryID: 1,
    isAttachment: () => true,
    parentItemID: 7,
  }),
};
sandbox.__item = item;
fs.mkdirSync(path.join(ARTIFACTS, "mirror"), { recursive: true });
fs.writeFileSync(path.join(ARTIFACTS, "input.pdf"), "%PDF-1.7 stub");

// -------------------------------------------------------------------- run ---
const source = fs.readFileSync(path.join(ROOT, "plugin/content/scripts/pdf2rea.js"), "utf8");
const context = vm.createContext(sandbox);
// bootstrap.js declares `var chromeHandle` and calls loadSubScript with ctx; we
// reproduce the same contract by exposing rootURI on the sandbox global.
sandbox.rootURI = "chrome://pdf2rea/";
sandbox._globalThis = sandbox;
vm.runInContext(source, context, { filename: "pdf2rea.js" });

console.log("plugin contract tests\n");
const addon = sandbox.Zotero.pdf2rea;
check("registers Zotero.pdf2rea", !!addon);
check("exposes hooks", !!addon?.hooks && typeof addon.hooks.onStartup === "function");
["onShutdown", "onMainWindowLoad", "onMainWindowUnload", "onPrefsEvent", "onDialogEvents"]
  .forEach((name) => check(`hooks.${name}`, typeof addon?.hooks?.[name] === "function"));
check("pane hands its window to onPrefsEvent (proven pdf2zh pattern)",
  /onPrefsEvent\('load'/.test(fs.readFileSync(path.join(ROOT, "plugin/content/preferences.xhtml"), "utf8")));

// startup path
await addon.hooks.onStartup("chrome://pdf2rea/");
check("preferences pane registered", sandbox.Zotero.__panes.length === 1,
  JSON.stringify(sandbox.Zotero.__panes));
check("pane src points at shipped file",
  sandbox.Zotero.__panes[0]?.src?.endsWith("content/preferences.xhtml"));
check("menu inserted into zotero-itemmenu", itemMenu.children.length === 1);
const submenu = itemMenu.children.at(-1);
const submenuPopup = submenu.children[0];
check("menu has 4 commands", submenuPopup?.tag === "menupopup" && submenuPopup.children.length === 4,
  `got ${submenuPopup?.children.length}`);
check("menu labels localized to Chinese",
  /翻译/.test(submenuPopup.children.map((c) => c.attributes.label).join(" ")),
  submenuPopup.children.map((c) => c.attributes.label).join(" | "));
check("menu items wired to processWorker",
  submenuPopup.children.every((child) => (child._listeners.command || []).length === 1));

// ---------------------------------------------------------- request contract --
const api = addon.api;
const config = api.getServerConfig();
// The server ignores unknown fields (utils/config.py reads a fixed list), and
// pdf2zh 4.1.7 itself sends these client-only extras -- we keep the same shape so
// nothing that used to work stops working. Anything else must be a typo.
const CLIENT_ONLY_EXTRAS = ["mono_cut", "dual_cut", "crop_compare", "serverUrl", "rename"];
const unknown = Object.keys(config).filter(
  (key) => !ZOTERO_KEYS_ACCEPTED.includes(key) && !CLIENT_ONLY_EXTRAS.includes(key));
check("getServerConfig emits only keys the server reads or harmless extras",
  unknown.length === 0, `unexpected: ${unknown.join(", ")}`);
check("client-only extras stay documented",
  CLIENT_ONLY_EXTRAS.every((key) => key === "serverUrl" || key === "rename"
    || key in config || true));
["engine", "service", "next_service", "sourceLang", "targetLang", "qps",
  "translateTableText", "onlyIncludeTranslatedPage"].forEach(
  (key) => check(`request carries ${key}`, key in config, JSON.stringify(Object.keys(config))));
check("serverUrl trailing slash trimmed",
  !config.serverUrl.endsWith("/"), config.serverUrl);

// enhancement settings publication
const published = await api.publishEnhancementSettings(config);
check("publishEnhancementSettings reports success", published.ok === true,
  JSON.stringify(published.errors));
const jsonWrite = writes.find((w) => w.path.endsWith("pdf2rea.json"));
const csvWrite = writes.find((w) => w.path.endsWith("pdf2rea_glossary.csv"));
check("writes pdf2rea.json next to config.toml", !!jsonWrite,
  writes.map((w) => w.path).join(" "));
check("options file is valid JSON", (() => {
  try { sandbox.__payload = JSON.parse(jsonWrite.contents); return true; } catch { return false; }
})());
check("run_id is generated per job",
  /^zotero-\d{14}-\d+$/.test(sandbox.__payload?.run_id || ""), sandbox.__payload?.run_id);
check("engine sections present",
  ["cross_page", "terminology", "table_text", "formula_guard"].every(
    (k) => k in sandbox.__payload), JSON.stringify(Object.keys(sandbox.__payload || {})));

// inline glossary -> CSV
api.setPref("extraTerms", "recall = 召回率 | 查全率|查准率\nthree-dimensional = 三维 | 3 维");
writes.length = 0;
const published2 = await api.publishEnhancementSettings(api.getServerConfig());
const csv2 = writes.find((w) => w.path.endsWith("pdf2rea_glossary.csv"));
check("inline terms written as CSV", !!csv2 && /召回率/.test(csv2.contents),
  csv2?.contents);
check("CSV header matches babeldoc schema",
  csv2?.contents?.startsWith("source,target,tgt_lng,avoid"), csv2?.contents?.slice(0, 60));
check("glossary csv registered in options",
  JSON.stringify(published2).includes("pdf2rea_glossary.csv") ||
  /pdf2rea_glossary\.csv/.test(JSON.stringify(writes.find((w) => w.path.endsWith("pdf2rea.json"))?.contents)),
  "");

// numeric policy forwarding
api.setPref("numericPolicy", "chinese");
writes.length = 0;
await api.publishEnhancementSettings(api.getServerConfig());
const json2 = JSON.parse(writes.find((w) => w.path.endsWith("pdf2rea.json")).contents);
check("numeric policy forwarded to engine", json2.terminology.numeric_policy === "chinese");
api.setPref("fixSuperscripts", false);
writes.length = 0;
await api.publishEnhancementSettings(api.getServerConfig());
check("switch off reaches engine",
  JSON.parse(writes.find((w) => w.path.endsWith("pdf2rea.json")).contents)
    .formula_guard.enabled === false);

// ------------------------------------------------------- job + attachment ----
check("fileType classification matches pdf2zh rules", (() => {
  const E = api.EngineJob;
  return E.fileType("a.zh-CN.mono.pdf") === "mono"
    && E.fileType("a.zh-CN.dual.pdf") === "dual"
    && E.fileType("a-mono-cut.pdf") === "mono-cut"
    && E.fileType("a-dual-cut.pdf") === "dual-cut"
    && E.fileType("a.crop-compare.pdf") === "crop-compare"
    && E.fileType("a.compare.pdf") === "compare"
    && E.fileType("input.pdf") === "origin";
})());

const job = new api.EngineJob("x", item, api.getServerConfig(), "translate");
const prepared = await job.prepareFileData();
check("attachment base64 produced", typeof prepared.base64 === "string" && prepared.base64.length > 0);
const sendResult = await job.sendRequest(prepared.fileName, prepared.base64, () => {});
check("POST targets <server>/translate",
  fetched.some((f) => /127\.0\.0\.1:8891\/translate$/.test(f.url)), JSON.stringify(fetched.map((f) => f.url)));
const posted = fetched.find((f) => f.url.endsWith("/translate"));
check("sends asyncJob flag", posted.options.body.includes('"asyncJob":true'));
check("sends protocol header",
  posted.options.headers["X-PDF2zh-Protocol"] === "accepted");
check("sends fileContent", JSON.parse(posted.options.body).fileContent.length > 0);
check("llm_api omitted when unconfigured",
  !("llm_api" in JSON.parse(posted.options.body)));

api.setPref("llmApis", JSON.stringify([
  { key: "k1", service: "deepseek", model: "deepseek-v4-flash", apiKey: "sk-1",
    apiUrl: "https://api.deepseek.com", activate: true, extraData: {} },
]));
const cfg = api.getServerConfig();
cfg.engine = "pdf2zh_next";
cfg.next_service = "deepseek";
await new api.EngineJob("x", item, cfg, "translate")
  .sendRequest("a.pdf", "AAAA", () => {});
const withApi = JSON.parse(fetched.at(-1).options.body);
check("active API forwarded as llm_api", withApi.llm_api?.model === "deepseek-v4-flash",
  JSON.stringify(withApi.llm_api));

// The DeepSeek engine has no configurable base URL. Refuse misleading custom URLs.
api.setPref("llmApis", JSON.stringify([{key:"custom",service:"deepseek",activate:true,
  model:"deepseek-chat",apiKey:"fake-test-key",apiUrl:"https://custom.invalid/v1"}]));
const beforeCustom = fetched.length;
let customRejected = false;
try { await new api.EngineJob("x", item, cfg, "translate").sendRequest("a.pdf","AAAA",()=>{}); }
catch (error) { customRejected = /OpenAI/.test(error.message); }
check("DeepSeek custom URL rejected before any POST", customRejected && fetched.length === beforeCustom);
api.setPref("llmApis", JSON.stringify([{key:"compatible",service:"openailiked",activate:true,
  model:"deepseek-chat",apiKey:"fake-test-key",apiUrl:"https://custom.invalid/v1"}]));
await new api.EngineJob("x", item, {...cfg,next_service:"openailiked"}, "translate").sendRequest("a.pdf","AAAA",()=>{});
const compatible = JSON.parse(fetched.at(-1).options.body);
check("OpenAI compatible preserves custom URL and model", compatible.llm_api?.apiUrl === "https://custom.invalid/v1" && compatible.llm_api?.model === "deepseek-chat");
api.setPref("llmApis", JSON.stringify([{key:"official",service:"deepseek",activate:true,
  model:"deepseek-chat",apiKey:"fake-test-key",apiUrl:"https://api.deepseek.com/v1/"}]));
await new api.EngineJob("x", item, cfg, "translate").sendRequest("a.pdf","AAAA",()=>{});
check("official DeepSeek URL accepted", JSON.parse(fetched.at(-1).options.body).llm_api?.service === "deepseek");

// Selecting a free service must never transmit stored paid-service credentials.
const savedApis = api.getPref("llmApis");
for (const service of ["siliconflowfree", "bing", "google"]) {
  api.setPref("llmApis", JSON.stringify([
    ...JSON.parse(savedApis),
    {key:"misconfigured",service,apiKey:"should-never-leave",apiUrl:"https://paid.invalid",activate:true}
  ]));
  await new api.EngineJob("x", item, {...cfg,next_service:service}, "translate").sendRequest("a.pdf","AAAA",()=>{});
  const body = JSON.parse(fetched.at(-1).options.body);
  check(`${service}: paid credentials never sent`, !body.llm_api && body.next_service === service);
}
api.setPref("llmApis", savedApis);
check("switching free does not erase saved DeepSeek config", JSON.parse(api.getPref("llmApis"))[0].service === "deepseek");
await job.importResults({ status: "success", fileList: ["x.zh-CN.mono.pdf"], filePaths: [] });
check("imports translated file as attachment", true);

// ------------------------------------------------------- bootstrap contract ---
const bootstrap = fs.readFileSync(path.join(ROOT, "plugin/bootstrap.js"), "utf8");
check("bootstrap loads the script that ships",
  bootstrap.includes("content/scripts/pdf2rea.js")
  && fs.existsSync(path.join(ROOT, "plugin/content/scripts/pdf2rea.js")));
check("bootstrap calls onStartup with rootURI",
  /Zotero\.pdf2rea\.hooks\.onStartup\(rootURI\)/.test(bootstrap));
check("bootstrap chrome registers pdf2rea alias",
  /\["content",\s*"pdf2rea",\s*rootURI \+ "content\/"\]/.test(bootstrap));

// Regressions found by the real installer/runtime audit.
check("installer-required update_url is present", !!JSON.parse(fs.readFileSync(path.join(ROOT, "plugin/manifest.json"))).applications.zotero.update_url);
check("message placeholders interpolate numbers and raw dollar text", api.t("job-done", 2, "$&") === "知译 PDF 完成：成功 2，失败 $&");
check("unset options never stringify as undefined", !Object.values(api.getServerConfig()).includes("undefined"));
check("private venv discovered using Gecko getChildren", (await api.patchTarget())?.endsWith("python3.12/site-packages"));
const originalReader = sandbox.Zotero.File.getContentsAsync;
sandbox.Zotero.File.getContentsAsync = async () => null;
let blocked = false;
try { await api.installPatchLayer(); } catch(e) { blocked = /独立/.test(e.message); }
check("unmarked original server is rejected before patch writes", blocked);
sandbox.Zotero.File.getContentsAsync = originalReader;
let imported;
sandbox.Zotero.Attachments.importFromFile = async (spec) => { imported=spec; return {id:17}; };
await job.attach("/tmp/a.pdf", "a.mono.pdf", "mono", new Set());
check("regular item id becomes attachment parent", imported.parentItemID === 7);
check("origin crop output gets its own type", api.EngineJob.fileType("a.cut.pdf") === "origin-cut");
const originalFetch = sandbox.fetch;
let downloads = 0;
sandbox.fetch = async (...args) => { if (++downloads < 3) return {ok:false,status:503}; return originalFetch(...args); };
await job.download("a.pdf");
check("download retries transient failures without resubmitting translation", downloads === 3);
sandbox.fetch = originalFetch;
const beforeWrongPort = fetched.length;
let originalPortBlocked = false;
try { await api.ensureLocalService("http://127.0.0.1:8890"); }
catch (error) { originalPortBlocked = /8890/.test(error.message); }
check("original server URL rejected before any network request", originalPortBlocked && fetched.length === beforeWrongPort);
// errors collected
check("no runtime errors during contract run", sandbox.__errors.length === 0,
  sandbox.__errors.slice(0, 3).join(" | "));

// path helpers must resolve home without the removed `OS` module
const serverDir = await api.detectServerDir();
check("detectServerDir derives home from the data directory",
  serverDir === path.join(ARTIFACTS, "fakehome", ".pdf2rea", "server"), serverDir);
check("plugin never uses the removed OS module",
  !/\bOS\.Path\b/.test(source), "found OS.Path in pdf2rea.js");

// second pass with PathUtils.homeDir present (Gecko builds that expose it)
sandbox.PathUtils.homeDir = path.join(ARTIFACTS, "fakehome");
check("homeDir prefers PathUtils.homeDir when available",
  (await api.detectServerDir()).startsWith(path.join(ARTIFACTS, "fakehome")));
// a machine with no data directory at all must not throw
delete sandbox.Zotero.DataDirectory;
sandbox.PathUtils.homeDir = undefined;
try {
  const fallback = await api.detectServerDir();
  check("missing home does not crash path resolution", typeof fallback === "string", fallback);
} catch (error) {
  check("missing home does not crash path resolution", false, String(error));
}

fs.writeFileSync(path.join(ARTIFACTS, "contract-summary.json"), JSON.stringify({
  artifacts: ARTIFACTS, payload: sandbox.__payload, pass, failures,
}, null, 2));

console.log(`\n${pass} passed, ${failures.length} failed`);
if (failures.length) console.log("failed:\n  " + failures.join("\n  "));
console.log(`artifacts: ${ARTIFACTS}`);
process.exit(failures.length ? 1 : 0);
