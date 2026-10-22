/* eslint-disable no-undef */
/**
 * 知译 PDF bootstrap -- Zotero 7/10 bootstrapped-extension entry point.
 *
 * Structure follows zotero-pdf2zh 4.1.7 (same manifest + bootstrap + single
 * content script contract), but without the zotero-plugin-toolkit dependency: the
 * plugin only needs a preferences pane, a submenu in the item list, and file IO.
 */

var chromeHandle;

function install(data, reason) {}

async function startup({ id, version, resourceURI, rootURI }, reason) {
  await Zotero.initializationPromise;

  if (!rootURI) {
    // Zotero 6 compatibility path.
    rootURI = resourceURI.spec;
  }

  var aomStartup = Components.classes[
    "@mozilla.org/addons/addon-manager-startup;1"
  ].getService(Components.interfaces.amIAddonManagerStartup);
  var manifestURI = Services.io.newURI(rootURI + "manifest.json");
  chromeHandle = aomStartup.registerChrome(manifestURI, [
    ["content", "pdf2rea", rootURI + "content/"],
  ]);

  const ctx = { rootURI, Zotero, Services, IOUtils, PathUtils, Components,
    ChromeUtils, Cc, Ci, Cu, fetch, btoa, atob, setTimeout, clearTimeout };
  ctx._globalThis = ctx;

  Services.scriptloader.loadSubScript(
    `${rootURI}content/scripts/pdf2rea.js`,
    ctx
  );
  await Zotero.pdf2rea.hooks.onStartup(rootURI);
}

async function onMainWindowLoad({ window }, reason) {
  Zotero.pdf2rea?.hooks.onMainWindowLoad(window);
}

async function onMainWindowUnload({ window }, reason) {
  Zotero.pdf2rea?.hooks.onMainWindowUnload(window);
}

function shutdown({ id, version, resourceURI, rootURI }, reason) {
  if (reason === APP_SHUTDOWN) {
    return;
  }

  if (typeof Zotero === "undefined") {
    Zotero = Components.classes["@zotero.org/Zotero;1"].getService(
      Components.interfaces.nsISupports
    ).wrappedJSObject;
  }
  Zotero.pdf2rea?.hooks.onShutdown();

  Cc["@mozilla.org/intl/stringbundle;1"]
    .getService(Components.interfaces.nsIStringBundleService)
    .flushBundles();

  if (chromeHandle) {
    chromeHandle.destruct();
    chromeHandle = null;
  }
}

function uninstall(data, reason) {}
