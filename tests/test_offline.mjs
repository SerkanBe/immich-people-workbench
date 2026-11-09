// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
const source = readFileSync(new URL("../web/offline.js", import.meta.url), "utf8").replace(/^export /gm, "");
const defer = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise,resolve}; };
function setup() {
  const elements = new Map(), calls = [], timers = new Map(), redirects = [], toasts = [];
  let status = {available:false,ready:false,offline:false,job:{phase:"idle"}}, reply = null, tick = 0;
  const node = id => {
    if (!elements.has(id)) elements.set(id, {hidden:false,disabled:false,open:false,textContent:"",listeners:{},
      addEventListener(event, handler) { this.listeners[event] = handler; }, removeAttribute() {}, setAttribute() {},
      showModal() { this.open = true; }, focus() { document.activeElement = this; },
      close() { this.open = false; this.listeners.close?.(); }});
    return elements.get(id);
  };
  const document = { querySelector: selector => node(selector.slice(1)) };
  const state = {summary:{connected:true,offline:false}};
  const api = async (path, options={}) => {
    calls.push({path,body:options.body});
    if (reply) return reply(path,options);
    if (path === "/api/offline/status") return status;
    return {};
  };
  const context = vm.createContext({document,state,api,window:{location:{assign:path=>redirects.push(path)}},showToast:text=>toasts.push(text),
    setTimeout(fn) { const id=++tick; timers.set(id,fn); return id; }, clearTimeout(id) { timers.delete(id); }});
  vm.runInContext(`${source}\nglobalThis.workspace = createOfflineWorkspace({api,state,document,window,showToast}); globalThis.bytes = formatBytes;`,context);
  return {document,state,calls,node,timers,toasts,redirects,workspace:context.workspace,bytes:context.bytes,
    status(value) { status=value; }, reply(fn) {reply=fn;}, click:id=>node(id).listeners.click()};
}

test("preparation reviews unique count and size before explicit original download", async () => {
  const run=setup(); await run.workspace.refresh();
  assert.equal(run.node("offline-download").hidden,true);
  assert.equal(run.node("offline-plan").textContent,"Check photos & size");
  assert.match(run.node("offline-workspace-description").textContent,/No photos are downloaded yet/);
  run.status({job:{phase:"planned",planId:"synthetic-plan",assetCount:3,estimatedBytes:2048,unknownSizes:1}});
  await run.click("offline-plan");
  assert.match(run.node("offline-workspace-description").textContent,/3 photos · estimated 2.0 KiB plus 1/);
  assert.equal(run.calls.some(call=>call.path === "/api/offline/download"),false);
  assert.equal(run.node("offline-download").textContent,"Download & prepare");
  assert.equal(run.node("offline-plan").hidden,true);
  run.status({job:{phase:"downloading",planId:"synthetic-plan",assetCount:3,downloadedFiles:1,downloadedBytes:1000}});
  await run.click("offline-download");
  const download=run.calls.find(call=>call.path === "/api/offline/download");
  assert.equal(download.body.confirmation,"DOWNLOAD_ORIGINALS"); assert.equal(download.body.planId,"synthetic-plan");
  assert.equal(run.node("offline-cancel").hidden,false); assert.equal(run.timers.size,1);
  run.status({job:{phase:"cancelled",planId:"synthetic-plan",assetCount:3,downloadedFiles:1}});
  await run.click("offline-cancel");
  assert.equal(run.node("offline-download").textContent,"Resume download");
  assert.equal(run.node("offline-open").hidden,true); assert.equal(run.timers.size,0);
  await run.click("offline-download");
  assert.equal(run.calls.filter(call=>call.path === "/api/offline/download").length,2);
});

test("offline state exposes saved coverage and reconnect while creation actions stay blocked", async () => {
  const run=setup(); run.state.summary.offline=true;
  run.status({ready:true,snapshot:{assetCount:4,totalBytes:3000,createdAt:"2026-01-01T12:00:00Z"},job:{phase:"ready"}});
  await run.workspace.refresh();
  assert.match(run.node("offline-status").textContent,/Offline · 4 saved photos/);
  assert.equal(run.node("offline-reconnect").hidden,false); assert.equal(run.node("offline-plan").hidden,true);
  await run.click("offline-plan"); await run.click("offline-download");
  assert.equal(run.calls.length,1); assert.match(run.node("offline-workspace-description").textContent,/Pending/);
});

test("open is explicit, unavailable for partial download, and never Syncs", async () => {
  const run=setup(); await run.workspace.refresh(); await run.click("offline-open");
  assert.equal(run.calls.length,1);
  run.status({ready:true,snapshot:{assetCount:2,totalBytes:100},job:{phase:"ready"}}); await run.workspace.refresh();
  assert.equal(run.node("offline-open").hidden,false); await run.click("offline-open");
  assert.deepEqual(run.redirects,["/naming/clusters"]);
  assert.equal(run.calls.some(call=>call.path === "/api/sync"),false);
});

test("duplicate action and stale progress replies cannot overwrite current status", async () => {
  const run=setup(), old=defer(), action=defer(); let get=0;
  run.reply((path)=> path === "/api/offline/status" ? ++get===1 ? old.promise : {job:{phase:"planned",planId:"new",assetCount:9,estimatedBytes:10}} : action.promise);
  const previous=run.workspace.refresh();
  const preparing=run.click("offline-plan"); await run.click("offline-plan");
  assert.equal(run.calls.filter(call=>call.path === "/api/offline/plan").length,1);
  action.resolve({}); await preparing;
  old.resolve({job:{phase:"planned",planId:"old",assetCount:1,estimatedBytes:1}}); await previous;
  assert.match(run.node("offline-workspace-description").textContent,/9 photos/);
});

test("dialog restores focus only after closing and errors remain reviewable", async () => {
  const run=setup(); await run.click("offline-workspace-button");
  assert.equal(run.node("offline-workspace-dialog").open,true);
  assert.equal(run.document.activeElement,run.node("offline-workspace-title"));
  run.node("offline-workspace-dialog").close();
  assert.equal(run.document.activeElement,run.node("offline-workspace-button"));
  run.reply(()=>{throw Error("Synthetic download error");});
  await run.click("offline-plan");
  assert.equal(run.node("offline-workspace-error").hidden,false);
  assert.match(run.node("offline-workspace-error").textContent,/Synthetic download error/);
  assert.equal(run.node("offline-plan").disabled,false);
});

test("size formatting handles empty and unknown plans honestly",()=>{
  const run=setup(); assert.equal(run.bytes(0),"0 B"); assert.equal(run.bytes(null),"unknown size"); assert.equal(run.bytes(1024),"1.0 KiB");
});


test("a snapshot made stale by Sync keeps originals and requires fresh preparation", async () => {
  const run=setup();
  run.status({ready:false,stale:true,staleReason:"Saved snapshot changed after Sync.",snapshot:{assetCount:3,totalBytes:1024},job:{phase:"idle"}});
  await run.workspace.refresh();
  assert.match(run.node("offline-status").textContent,/needs refresh/);
  assert.match(run.node("offline-workspace-description").textContent,/needs refreshing after Sync.*Stored files are kept/);
  assert.equal(run.node("offline-open").hidden,true);
  assert.equal(run.node("offline-plan").hidden,false);
  await run.click("offline-open");
  assert.equal(run.calls.some(call=>call.path === "/api/offline/open"),false);
  await run.click("offline-plan");
  assert.equal(run.calls.some(call=>call.path === "/api/offline/plan"),true);
});


test("destination uses the authenticated server path as text, not browser storage", async () => {
  const run=setup();
  run.status({storagePath:"/synthetic/custom-state/offline",job:{phase:"idle"}});
  await run.workspace.refresh();
  assert.equal(run.node("offline-storage-path").textContent,"/synthetic/custom-state/offline");
  run.status({storagePath:"<synthetic-path>",job:{phase:"idle"}});
  await run.workspace.refresh();
  assert.equal(run.node("offline-storage-path").textContent,"<synthetic-path>");
});


test("a fresh check on a stale workspace shows its new estimate and download action", async () => {
  const run=setup();
  run.status({ready:false,stale:true,snapshot:{assetCount:3},job:{phase:"planned",planId:"replacement",assetCount:5,estimatedBytes:4096}});
  await run.workspace.refresh();
  assert.match(run.node("offline-workspace-description").textContent,/5 photos .*4.0 KiB/);
  assert.equal(run.node("offline-plan").hidden,true);
  assert.equal(run.node("offline-download").hidden,false);
  assert.equal(run.node("offline-open").hidden,true);
});
