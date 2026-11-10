// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
const source = readFileSync(new URL("../web/offline.js", import.meta.url), "utf8").replace(/^export /gm, "");
const defer = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise,resolve}; };
function setup() {
  const elements = new Map(), calls = [], timers = new Map(), redirects = [], toasts = [], imported = [], events = {};
  let status = {available:false,ready:false,offline:false,job:{phase:"idle"}}, reply = null, tick = 0;
  const makeNode = tag => ({tagName:tag,children:[],hidden:false,disabled:false,open:false,textContent:"",value:"",files:[],listeners:{},attributes:{},
    addEventListener(event, handler) { this.listeners[event] = handler; },
    removeAttribute(name) { delete this.attributes[name]; if (name === "href") this.href = ""; },
    setAttribute(name,value) { this.attributes[name] = value; },
    append(...children) { this.children.push(...children); }, replaceChildren(...children) { this.children = [...children]; },
    querySelectorAll(selector) { return this.children.flatMap(child => [...(selector === "input" && child.tagName === "input" ? [child] : []), ...child.querySelectorAll(selector)]); },
    showModal() { this.open = true; }, focus() { document.activeElement = this; },
    close() { this.open = false; this.listeners.close?.(); }});
  const node = id => { if (!elements.has(id)) elements.set(id,makeNode("div")); return elements.get(id); };
  const document = { querySelector: selector => node(selector.slice(1)), createElement:makeNode,
    addEventListener(event,handler) { events[event] = handler; }, dispatchEvent(event) { events[event.type]?.(); } };
  const state = {summary:{connected:true,offline:false}};
  const api = async (path, options={}) => {
    calls.push({path,body:options.body});
    if (reply) return reply(path,options);
    if (path === "/api/offline/status") return status;
    if (path === "/api/offline/packages") return {portable:false,workspaces:[],snapshots:[]};
    return {};
  };
  const context = vm.createContext({document,state,api,TextEncoder,Uint8Array,btoa,Date,
    window:{location:{assign:path=>redirects.push(path)}},showToast:text=>toasts.push(text),onImported:async (...args)=>imported.push(args),
    setTimeout(fn,delay) { const id=++tick; timers.set(id,{fn,delay}); return id; }, clearTimeout(id) { timers.delete(id); }});
  vm.runInContext(`${source}\nglobalThis.workspace = createOfflineWorkspace({api,state,document,window,showToast,onImported}); globalThis.bytes = formatBytes;`,context);
  return {document,state,calls,node,timers,toasts,redirects,imported,workspace:context.workspace,bytes:context.bytes,
    status(value) { status=value; }, reply(fn) {reply=fn;},
    click:id=>node(id).listeners.click({preventDefault() { this.defaultPrevented=true; }}),
    change:id=>node(id).listeners.change(), event:type=>document.dispatchEvent({type})};
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

const readyStatus = {ready:true,job:{phase:"ready"},snapshot:{assetCount:3,totalBytes:2048}};
const file = (value="synthetic result", overrides={}) => {
  const bytes = new TextEncoder().encode(value);
  return {name:"return.zip",size:bytes.length,arrayBuffer:async()=>bytes.buffer,...overrides};
};
const groups = () => ({token:"synthetic-token",workspaceName:"<returned-workspace>",expiresAt:new Date(Date.now()+600000).toISOString(),
  changes:[{id:"one",kind:"pending",label:"<Name person>",status:"ready"},{id:"conflict",kind:"pending",label:"Conflicting person",status:"conflict",reason:"Changed at source"},{id:"applied",kind:"review",label:"Reviewed",status:"applied"}],
  counts:{ready:2,conflict:1,applied:1},readyIds:["one","two"],nextCursor:"page-two"});
const response = (run, handlers={}) => run.reply(async (path,options) => {
  if (handlers[path]) return handlers[path](options);
  if (path === "/api/offline/status") return readyStatus;
  if (path === "/api/offline/packages") return {portable:false,workspaces:[],snapshots:[]};
  if (path === "/api/offline/import/preview") return groups();
  return {};
});
const choose = async (run, selected=file()) => { run.node("offline-import-file").files=[selected]; return run.change("offline-import-file"); };

function allNodes(node) { return [node,...node.children.flatMap(allNodes)]; }

test("ZIP creation polls real history and uses private anchors with text-only labels",async()=>{
  const run=setup(), creation=defer(); let created=false;
  response(run,{"/api/offline/packages": async options=>{
    if (options.method === "POST") { await creation.promise; created=true; return {id:"export /id"}; }
    return {portable:false,workspaces:created?[{id:"export /id",name:"<Family>",createdAt:"2026-11-10T12:00:00Z",assetCount:3,mediaBytes:2048,archiveBytes:1024,status:"ready",downloadReady:true,importSummary:{importedGroups:1}}]:[],snapshots:[]};
  }});
  await run.click("offline-workspace-button"); run.node("offline-package-name").value="Family";
  const building=run.click("offline-package-build"); await run.click("offline-package-build");
  assert.equal(run.calls.filter(x=>x.path==="/api/offline/packages"&&x.body).length,1);
  creation.resolve(); await building;
  const nodes=allNodes(run.node("offline-package-list"));
  assert.ok(nodes.some(n=>n.textContent==="<Family>"));
  assert.ok(nodes.some(n=>n.href==="/api/offline/packages/export%20%2Fid/download"));
  assert.ok(nodes.some(n=>n.textContent.includes("1 group imported")));
  assert.equal(run.calls.some(x=>x.path.includes("/download")),false);
});

test("a lost create response requires explicit history refresh before another create",async()=>{
  const run=setup(); let posts=0;
  response(run,{"/api/offline/packages":async options=>{if(options.method==="POST"){posts++;throw Error("Lost response");}return {workspaces:[],snapshots:[]};}});
  await run.click("offline-workspace-button"); run.node("offline-package-name").value="Trip";
  await run.click("offline-package-build"); await run.click("offline-package-build");
  assert.equal(posts,1); assert.equal(run.node("offline-package-build").disabled,true);
  await run.click("offline-refresh"); assert.equal(run.node("offline-package-build").disabled,false);
});

test("preparing artifacts have explicit cancel, no premature link, and failure retains prior ready artifacts",async()=>{
  const run=setup(); let cancelled=false;
  response(run,{"/api/offline/packages":async()=>({workspaces:[{id:"old",name:"Old",status:"ready",downloadReady:true},{id:"new",name:"New",status:cancelled?"error":"preparing",downloadReady:false,error:cancelled?"Cancelled":""}],snapshots:[]}),
    "/api/offline/packages/new/cancel":async()=>{cancelled=true;return {};}});
  await run.click("offline-workspace-button");
  let nodes=allNodes(run.node("offline-package-list"));
  assert.equal(nodes.filter(n=>n.tagName==="a").length,1);
  await nodes.find(n=>n.tagName==="button").listeners.click();
  nodes=allNodes(run.node("offline-package-list"));
  assert.ok(nodes.some(n=>n.href==="/api/offline/packages/old/download"));
  assert.ok(nodes.some(n=>n.textContent==="Failed")); assert.ok(cancelled);
});

test("portable mode exposes results only, preserves legacy reconnect, and blocks captures during saves",async()=>{
  const run=setup(); run.state.summary={connected:true,offline:true,portable:true};
  run.status({...readyStatus,portable:true}); await run.workspace.refresh();
  assert.equal(run.node("offline-source-sections").hidden,true); assert.equal(run.node("offline-reconnect").hidden,true);
  assert.equal(run.node("offline-results").hidden,false); assert.equal(run.node("offline-results-download").href,"/api/offline/results");
  await run.click("offline-plan"); await run.click("offline-snapshot-create"); assert.equal(run.calls.length,1);
  run.state.groupSaving=true; run.event("workbench-save-state");
  assert.equal(run.node("offline-results-download").href,"");
  let prevented=false; run.node("offline-results-download").listeners.click({preventDefault(){prevented=true;}}); assert.ok(prevented);
  run.state.groupSaving=false;run.state.groupUnsaved=true;run.event("workbench-save-state");
  assert.match(run.node("offline-save-notice").textContent,/could not be saved/);
  run.state.summary.portable=false;run.status(readyStatus);await run.workspace.refresh();
  assert.equal(run.node("offline-reconnect").hidden,false);
});

test("preview selects all ready groups across pages while conflicts and applied groups have no checkbox",async()=>{
  const run=setup();response(run,{"/api/offline/import/preview/page":async options=>{assert.equal(options.body.cursor,"page-two");return {changes:[{id:"two",kind:"review",label:"Second group",status:"ready"}],nextCursor:null};}});
  await choose(run);
  assert.match(run.node("offline-import-counts").textContent,/2 ready.*1 already imported.*1 conflicting/);
  assert.equal(run.node("offline-import-rows").querySelectorAll("input").length,1);
  assert.ok(allNodes(run.node("offline-import-rows")).some(n=>n.textContent==="<Name person>"));
  const first=run.node("offline-import-rows").querySelectorAll("input")[0]; first.checked=false;first.listeners.change();
  await run.click("offline-import-next"); assert.equal(run.node("offline-import-rows").querySelectorAll("input")[0].checked,true);
  assert.match(run.node("offline-import-selection").textContent,/1 group selected/);
  await run.click("offline-import-previous"); assert.equal(run.node("offline-import-rows").querySelectorAll("input")[0].checked,false);
  await run.click("offline-import-none"); assert.equal(run.node("offline-import-apply").disabled,true);
  await run.click("offline-import-all"); assert.match(run.node("offline-import-selection").textContent,/2 groups selected/);
});

test("selected import runs once, records snapshot and hands off to Pending without Sync",async()=>{
  const run=setup(), applying=defer();response(run,{"/api/offline/import/apply":async()=>applying.promise});
  await run.click("offline-workspace-button");await choose(run);
  const checkbox=run.node("offline-import-rows").querySelectorAll("input")[0];checkbox.checked=false;checkbox.listeners.change();
  const applyingRequest=run.click("offline-import-apply");await run.click("offline-import-apply");
  const requests=run.calls.filter(x=>x.path==="/api/offline/import/apply");
  assert.equal(requests.length,1);assert.deepEqual(Array.from(requests[0].body.ids),["two"]);assert.equal(requests[0].body.confirmation,"IMPORT_LOCAL");
  applying.resolve({imported:1,skipped:0,snapshot:{id:"before-import"}});await applyingRequest;
  assert.equal(run.imported.length,1);assert.equal(run.imported[0][1].navigate,true);
  assert.equal(run.node("offline-workspace-dialog").open,false);
  assert.notEqual(run.document.activeElement,run.node("offline-workspace-button"));
  assert.equal(run.calls.some(x=>x.path==="/api/sync"),false);
});

test("lost import response clears eligibility and only explicit re-preview reconciles ledger",async()=>{
  const run=setup();let previews=0;response(run,{"/api/offline/import/preview":async()=>{previews++;return previews===1?groups():{...groups(),counts:{ready:0,conflict:0,applied:4},readyIds:[],changes:[],nextCursor:null,lastImport:{imported:2,skipped:0,snapshot:{id:"snapshot"}}};},
    "/api/offline/import/apply":async()=>{throw TypeError("Lost response");}});
  await choose(run);await run.click("offline-import-apply");await run.click("offline-import-apply");
  assert.equal(run.calls.filter(x=>x.path==="/api/offline/import/apply").length,1);
  assert.equal(previews,1);assert.equal(run.node("offline-import-apply").disabled,true);
  assert.match(run.node("offline-import-error").textContent,/not confirmed/);
  await run.click("offline-import-retry");assert.equal(previews,2);
  assert.match(run.node("offline-import-message").textContent,/Previous import confirmed: 2/);
  assert.equal(run.node("offline-import-apply").disabled,true);
});

test("file cap is checked before reading and new file wins over late old read or preview",async()=>{
  const run=setup();let read=false;response(run);
  await choose(run,file("",{size:16*1024*1024+1,arrayBuffer:async()=>{read=true;}}));
  assert.equal(read,false);assert.equal(run.calls.length,0);assert.match(run.node("offline-import-error").textContent,/16 MiB/);
  const old=defer(), current=defer();
  const reading=choose(run,file("old",{arrayBuffer:()=>old.promise}));
  response(run,{"/api/offline/import/preview":async()=>current.promise});
  const newer=choose(run,file("new"));await Promise.resolve();old.resolve(new TextEncoder().encode("old").buffer);await reading;
  current.resolve({...groups(),workspaceName:"New file"});await newer;
  assert.match(run.node("offline-import-message").textContent,/New file/);
  assert.equal(run.calls.filter(x=>x.path==="/api/offline/import/preview").length,1);
});

test("local mutation or mode change invalidates preview and late replies cannot restore it",async()=>{
  const run=setup();response(run);await choose(run);
  run.state.localRevision=1;run.state.localRequests=1;run.event("workbench-save-state");
  assert.equal(run.node("offline-import-review").hidden,true);assert.equal(run.node("offline-import-apply").disabled,true);
  run.state.localRequests=0;
  const late=defer();response(run,{"/api/offline/import/preview":async()=>late.promise});
  const request=run.click("offline-import-retry");await Promise.resolve();run.state.summary.portable=true;run.workspace.updateSummary();late.resolve(groups());await request;
  assert.equal(run.node("offline-import-review").hidden,true);assert.equal(run.node("offline-source-sections").hidden,true);
});

test("expired preview and page failure require explicit fresh review; no hidden retry",async()=>{
  const run=setup();response(run,{"/api/offline/import/preview/page":async()=>{throw Object.assign(Error("Preview stale"),{status:409,code:"preview_stale"});}});
  await choose(run);await run.click("offline-import-next");
  assert.equal(run.node("offline-import-review").hidden,true);assert.match(run.node("offline-import-error").textContent,/Review the file again/);
  await run.click("offline-import-retry");
  const expiry=[...run.timers.values()].find(x=>x.delay>500000);expiry.fn();
  assert.equal(run.node("offline-import-apply").disabled,true);assert.match(run.node("offline-import-message").textContent,/expired/);
  assert.equal(run.calls.some(x=>x.path==="/api/offline/import/apply"),false);
});

test("manual snapshots use source endpoint and private download, never restore or Sync",async()=>{
  const run=setup();let saved=false;
  response(run,{"/api/offline/state-snapshots":async options=>{saved=true;assert.equal(options.body.name,"Before");return {id:"snapshot"};},
    "/api/offline/packages":async()=>({workspaces:[],snapshots:saved?[{id:"snapshot",name:"<Before>",reason:"manual"}]:[]})});
  await run.click("offline-workspace-button");run.node("offline-snapshot-name").value="Before";await run.click("offline-snapshot-create");
  assert.ok(allNodes(run.node("offline-snapshot-list")).some(x=>x.href==="/api/offline/state-snapshots/snapshot/download"));
  assert.equal(run.calls.some(x=>/restore|sync/.test(x.path)),false);
});

test("closing during import preserves result without stealing newer focus or replaying",async()=>{
  const run=setup(), outcome=defer();response(run,{"/api/offline/import/apply":async()=>outcome.promise});
  await run.click("offline-workspace-button");await choose(run);const request=run.click("offline-import-apply");
  run.node("offline-workspace-dialog").close();run.node("another-control").focus();
  outcome.resolve({imported:2,skipped:0,snapshot:{id:"snapshot"}});await request;
  assert.equal(run.document.activeElement,run.node("another-control"));assert.equal(run.imported[0][1].navigate,false);
  assert.equal(run.calls.filter(x=>x.path==="/api/offline/import/apply").length,1);
});
