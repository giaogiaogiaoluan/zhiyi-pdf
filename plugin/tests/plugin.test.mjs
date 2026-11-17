// Runs the plugin script inside a minimal fake Zotero to check its logic.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../content/scripts/pdf2rea.js", import.meta.url), "utf8");
const defaults = readFileSync(new URL("../prefs.js", import.meta.url), "utf8");

function load(initialPrefs = {}) {
  const prefs = new Map(Object.entries(initialPrefs));
  const Zotero = {
    Prefs: {
      get: (key) => prefs.get(key),
      set: (key, value) => prefs.set(key, value)
    },
    logError() {}
  };
  const context = vm.createContext({
    Zotero, PathUtils: { homeDir: "/home/u", join: (...p) => p.join("/"), filename: (p) => p.split("/").pop() },
    Services: { appinfo: { OS: "Darwin" } }, IOUtils: {}, ChromeUtils: {}, fetch: async () => { throw new Error("offline"); },
    btoa: (s) => Buffer.from(s, "binary").toString("base64"), setTimeout, clearTimeout, AbortController
  });
  vm.runInContext(source, context);
  return { api: Zotero.pdf2rea.api, hooks: Zotero.pdf2rea.hooks, prefs, Zotero };
}

const P = "extensions.zotero.pdf2rea.";

test("defaults in prefs.js match the script's defaults", () => {
  const { prefs, hooks, Zotero } = load();
  Zotero.PreferencePanes = { register: async () => {} };
  Zotero.getMainWindows = () => [];
  return hooks.onStartup("chrome://x/").then(() => {
    for (const [, key, raw] of defaults.matchAll(/pref\("extensions\.zotero\.pdf2rea\.([^"]+)",\s*(.+)\);/g)) {
      assert.deepEqual(prefs.get(P + key), JSON.parse(raw), key);
    }
  });
});

test("request config for the keyless service carries no credentials", () => {
  const { api, prefs } = load();
  prefs.set(P + "llmService", "google");
  prefs.set(P + "llmApiKey", "secret");
  prefs.set(P + "mono", true);
  prefs.set(P + "dual", false);
  prefs.set(P + "dualMode", "TB");
  const config = api.buildConfig();
  assert.equal(config.service, "google");
  assert.equal(config.api_key, undefined);
  assert.equal(config.base_url, undefined);
  assert.equal(config.dual_mode, "TB");
  assert.equal(config.mono, true);
  assert.equal(config.dual, false);
});

test("LLM config is complete and validated", () => {
  const { api, prefs } = load();
  prefs.set(P + "llmService", "openai");
  prefs.set(P + "llmBaseUrl", " https://api.deepseek.com/v1 ");
  prefs.set(P + "llmModel", "deepseek-chat");
  prefs.set(P + "mono", true);
  const config = api.buildConfig();
  assert.equal(config.base_url, "https://api.deepseek.com/v1");
  assert.throws(() => api.validateConfig(config), /API Key/);
  assert.doesNotThrow(() => api.validateConfig({ ...config, api_key: "k" }));
  assert.doesNotThrow(() => api.validateConfig({ ...config, base_url: "http://localhost:11434/v1" }));
  assert.throws(() => api.validateConfig({ ...config, api_key: "k", mono: false, dual: false }), /未选择输出/);
});

test("1.x settings are migrated once", () => {
  const { api, prefs } = load({
    [P + "next_service"]: "openailiked",
    [P + "llmApis"]: JSON.stringify([{ service: "openailiked", activate: true, apiKey: "k1",
      apiUrl: "https://ark.cn-beijing.volces.com/api/v3", model: "deepseek-v3-250324" }]),
    [P + "new_serverip"]: "http://127.0.0.1:8891"
  });
  api.migrateFrom1x();
  assert.equal(prefs.get(P + "llmService"), "openai");
  assert.equal(prefs.get(P + "llmPreset"), "ark");
  assert.equal(prefs.get(P + "llmApiKey"), "k1");
  assert.equal(prefs.get(P + "llmModel"), "deepseek-v3-250324");
  assert.equal(prefs.get(P + "serverUrl"), undefined, "old 8891 address is not carried over");
  prefs.set(P + "llmApiKey", "changed");
  api.migrateFrom1x();
  assert.equal(prefs.get(P + "llmApiKey"), "changed");
});

test("result files are classified by suffix", () => {
  const { api } = load();
  assert.equal(api.Job.fileType("Paper.zh-CN.mono.pdf"), "mono");
  assert.equal(api.Job.fileType("Paper.zh-CN.dual.pdf"), "dual");
  assert.equal(api.Job.fileType("other.pdf"), "pdf");
});
