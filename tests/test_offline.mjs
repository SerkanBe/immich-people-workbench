// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
const source = readFileSync(new URL("../web/offline.js", import.meta.url), "utf8").replace(/^export /gm, "");
const defer = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise,resolve}; };
function setup({storage = new Map()} = {}) {
  const elements = new Map(), calls = [], timers = new Map(), redirects = [], toasts = [], imported = [], events = {};
  let status = {available:false,ready:false,offline:false,job:{phase:"idle"}}, reply = null, tick = 0;
  const makeNode = tag => ({tagName:tag,children:[],hidden:false,disabled:false,open:false,textContent:"",value:"",files:[],listeners:{},attributes:{},dataset:{},
    addEventListener(event, handler) { this.listeners[event] = handler; },
    removeAttribute(name) { delete this.attributes[name]; if (name === "href") this.href = ""; },
    setAttribute(name,value) { this.attributes[name] = value; },
    append(...children) { this.children.push(...children); }, replaceChildren(...children) { this.children = [...children]; },
    querySelectorAll(selector) { return this.children.flatMap(child => [...(selector === "input" && child.tagName === "input" ? [child] : []), ...child.querySelectorAll(selector)]); },
    showModal() { this.open = true; }, focus() { document.activeElement = this; },
    close() { this.open = false; this.listeners.close?.(); }});
  const node = id => { if (!elements.has(id)) elements.set(id,makeNode("div")); return elements.get(id); };
  const helpButtons = [node("scope-help"), node("name-help")];
  helpButtons[0].dataset.offlineHelp = "scope"; helpButtons[1].dataset.offlineHelp = "name";
  const back = node("help-back");
  const document = { querySelectorAll: selector => selector === "[data-offline-help]" ? helpButtons : selector === "[data-offline-help-back]" ? [back] : [], querySelector: selector => node(selector.slice(1)), createElement:makeNode,
    addEventListener(event,handler) { events[event] = handler; }, dispatchEvent(event) { events[event.type]?.(); } };
  const state = {view:"offline-create",summary:{connected:true,offline:false}};
  const api = async (path, options={}) => {
    calls.push({path,body:options.body});
    if (reply) return reply(path,options);
    if (path === "/api/offline/status") return status;
    if (path === "/api/offline/packages") return {portable:false,workspaces:[],snapshots:[]};
    return {};
  };
  const context = vm.createContext({document,state,api,TextEncoder,Uint8Array,btoa,Date,
    window:{sessionStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},location:{assign:path=>redirects.push(path)}},showToast:text=>toasts.push(text),onImported:async (...args)=>imported.push(args),onNavigate:view=>redirects.push(view),
    setTimeout(fn,delay) { const id=++tick; timers.set(id,{fn,delay}); return id; }, clearTimeout(id) { timers.delete(id); }});
  vm.runInContext(`${source}\nglobalThis.workspace = createOfflineWorkspace({api,state,document,window,showToast,onImported,onNavigate}); globalThis.bytes = formatBytes;`,context);
  return {storage,document,state,calls,node,timers,toasts,redirects,imported,workspace:context.workspace,bytes:context.bytes,
    status(value) { status=value; }, reply(fn) {reply=fn;},
    click:id=>node(id).listeners.click({preventDefault() { this.defaultPrevented=true; }}),
    change:id=>node(id).listeners.change(), event:type=>document.dispatchEvent({type})};
}

const readyStatus = {ready:true,job:{phase:"ready",planId:"synthetic-plan",assetCount:3,estimatedBytes:2048},snapshot:{id:"synthetic-plan",assetCount:3,totalBytes:2048}};
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
  await run.workspace.showView("offline-create"); run.node("offline-package-name").value="Family";
  const building=run.click("offline-download"); await run.click("offline-download");
  assert.equal(run.calls.filter(x=>x.path==="/api/offline/packages"&&x.body).length,1);
  creation.resolve(); await building;
  const nodes=allNodes(run.node("offline-package-list"));
  assert.ok(nodes.some(n=>n.textContent==="<Family>"));
  assert.ok(nodes.some(n=>n.href==="/api/offline/packages/export%20%2Fid/download"));
  assert.ok(nodes.some(n=>n.textContent.includes("1 changes imported")));
  assert.equal(run.calls.some(x=>x.path.includes("/download")),false);
});

test("a lost create response requires explicit history refresh before another create",async()=>{
  const run=setup(); let posts=0;
  response(run,{"/api/offline/packages":async options=>{if(options.method==="POST"){posts++;throw Error("Lost response");}return {workspaces:[],snapshots:[]};}});
  await run.workspace.showView("offline-create"); run.node("offline-package-name").value="Trip";
  await run.click("offline-download"); await run.click("offline-download");
  assert.equal(posts,1); assert.equal(run.node("offline-download").disabled,true);
  await run.click("offline-refresh"); assert.equal(run.node("offline-download").disabled,false);
});

test("preparing artifacts have explicit cancel, no premature link, and failure retains prior ready artifacts",async()=>{
  const run=setup(); let cancelled=false;
  response(run,{"/api/offline/packages":async()=>({workspaces:[{id:"old",name:"Old",status:"ready",downloadReady:true},{id:"new",name:"New",status:cancelled?"error":"preparing",downloadReady:false,error:cancelled?"Cancelled":""}],snapshots:[]}),
    "/api/offline/packages/new/cancel":async()=>{cancelled=true;return {};}});
  await run.workspace.showView("offline-create");
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
  assert.equal(run.node("offline-navigation").hidden,true); assert.equal(run.node("offline-reconnect").hidden,true);
  assert.equal(run.node("offline-results-download").hidden,false); assert.equal(run.node("offline-results-download").href,"/api/offline/results");
  await run.click("offline-plan"); await run.click("offline-snapshot-create"); assert.equal(run.calls.some(call=>call.body),false);
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
  assert.match(run.node("offline-import-selection").textContent,/1 selected/);
  await run.click("offline-import-previous"); assert.equal(run.node("offline-import-rows").querySelectorAll("input")[0].checked,false);
  await run.click("offline-import-none"); assert.equal(run.node("offline-import-apply").disabled,true);
  await run.click("offline-import-all"); assert.match(run.node("offline-import-selection").textContent,/2 selected/);
});

test("selected import runs once, records snapshot and offers Pending only for queued work",async()=>{
  const run=setup(), applying=defer();response(run,{"/api/offline/import/apply":async()=>applying.promise});
  await run.workspace.showView("offline-create");await choose(run);
  const checkbox=run.node("offline-import-rows").querySelectorAll("input")[0];checkbox.checked=false;checkbox.listeners.change();
  const applyingRequest=run.click("offline-import-apply");await run.click("offline-import-apply");
  const requests=run.calls.filter(x=>x.path==="/api/offline/import/apply");
  assert.equal(requests.length,1);assert.deepEqual(Array.from(requests[0].body.ids),["two"]);assert.equal(requests[0].body.confirmation,"IMPORT_LOCAL");
  applying.resolve({imported:1,skipped:0,snapshot:{id:"before-import"}});await applyingRequest;
  assert.equal(run.imported.length,1);assert.equal(run.imported[0][1].navigate,false);
  assert.equal(run.node("offline-import-message").textContent,"Imported 1 changes");
  assert.equal(run.node("offline-import-pending").hidden,true);
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
  assert.equal(run.node("offline-import-review").hidden,true);assert.equal(run.node("offline-navigation").hidden,true);
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
  await run.workspace.showView("offline-create");run.node("offline-snapshot-name").value="Before";await run.click("offline-snapshot-create");
  assert.ok(allNodes(run.node("offline-snapshot-list")).some(x=>x.href==="/api/offline/state-snapshots/snapshot/download"));
  assert.equal(run.calls.some(x=>/restore|sync/.test(x.path)),false);
});

test("leaving import preserves result without stealing newer focus or replaying",async()=>{
  const run=setup(), outcome=defer();response(run,{"/api/offline/import/apply":async()=>outcome.promise});
  await run.workspace.showView("offline-create");await choose(run);const request=run.click("offline-import-apply");
  run.workspace.setView("offline-help");run.node("another-control").focus();
  outcome.resolve({imported:2,skipped:0,snapshot:{id:"snapshot"}});await request;
  assert.equal(run.document.activeElement,run.node("another-control"));assert.equal(run.imported[0][1].navigate,false);
  assert.equal(run.calls.filter(x=>x.path==="/api/offline/import/apply").length,1);
});

test("Find photos reviews count/size before one explicit creation chains download and ZIP", async () => {
  const run=setup();let phase="idle",made=false;
  run.reply(async(path,options)=>{
    if(path==="/api/offline/status")return {snapshot:phase==="ready"?{id:"selection"}:null,ready:phase==="ready",job:{phase,planId:"selection",assetCount:3,estimatedBytes:4096,downloadedFiles:phase==="ready"?3:1,downloadedBytes:1024}};
    if(path==="/api/offline/packages"&&options.method==="POST"){made=true;assert.equal(options.body.name,"Offline copy");return{id:"copy"};}
    if(path==="/api/offline/packages")return{workspaces:made?[{id:"copy",status:"ready",downloadReady:true}]:[],snapshots:[]};
    if(path==="/api/offline/plan"){phase="planned";return{};}
    if(path==="/api/offline/download"){assert.equal(options.body.confirmation,"DOWNLOAD_ORIGINALS");assert.equal(options.body.planId,"selection");phase="downloading";return{};}
  });
  await run.workspace.refresh();assert.equal(run.node("offline-plan").textContent,"Find photos");
  await run.click("offline-plan");assert.match(run.node("offline-workspace-description").textContent,/3 photos.*4.0 KiB estimated/);
  assert.equal(run.calls.some(call=>call.path==="/api/offline/download"),false);
  assert.equal(run.node("offline-download").textContent,"Create offline copy");
  await run.click("offline-download");assert.equal(made,false);assert.equal(run.node("offline-scope-help").hidden,true);
  run.workspace.setView("offline-help");phase="ready";await run.workspace.refresh();
  assert.equal(run.calls.filter(call=>call.path==="/api/offline/packages"&&call.body).length,1);
  assert.equal(run.node("offline-package-download").href,"/api/offline/packages/copy/download");
  assert.equal(run.calls.some(call=>call.path==="/api/sync"),false);
});

test("unknown or missing estimates never imply zero; destination stays text in collapsed Storage details",async()=>{
  const run=setup();
  for(const value of [null,undefined,0,NaN]){
    run.status({storagePath:"<synthetic/path>",job:{phase:"planned",planId:"p",assetCount:3,estimatedBytes:value}});
    await run.workspace.refresh();assert.match(run.node("offline-workspace-description").textContent,/Size unknown/);
    assert.equal(run.node("offline-workspace-description").textContent.includes("0 B"),false);
  }
  assert.equal(run.node("offline-storage-path").textContent,"<synthetic/path>");
  assert.equal(run.node("offline-storage").open,false);
});

test("Cancel fences auto ZIP creation and Resume requires a fresh explicit action",async()=>{
  const run=setup();let phase="planned";
  run.reply(async(path,options)=>{
    if(path==="/api/offline/status")return{ready:phase==="ready",job:{phase,planId:"p",assetCount:2}};
    if(path==="/api/offline/packages")return{workspaces:[],snapshots:[]};
    if(path==="/api/offline/download"){phase="downloading";return{};}
    if(path==="/api/offline/cancel"){phase="cancelled";return{};}
  });
  await run.workspace.refresh();await run.click("offline-download");await run.click("offline-cancel");
  assert.equal(run.node("offline-download").textContent,"Resume");phase="ready";await run.workspace.refresh();
  assert.equal(run.calls.filter(call=>call.path==="/api/offline/packages"&&call.body).length,0);
});

test("reload restores only IDs and GET reconciliation, never mutation consent",async()=>{
  const storage=new Map([["immichPeopleWorkbench.offlineTransfer.v1",JSON.stringify({planId:"synthetic-plan",packageId:null})]]);
  const run=setup({storage});response(run);await run.workspace.showView("offline-create");
  assert.equal(run.node("offline-download").textContent,"Continue");
  assert.equal(run.calls.some(call=>call.body),false);
  assert.equal(JSON.parse(storage.get("immichPeopleWorkbench.offlineTransfer.v1")).name,undefined);
});

test("an ambiguous photo start reconciles without continuing into ZIP",async()=>{
  const run=setup();let phase="planned";
  run.reply(async(path,options)=>{
    if(path==="/api/offline/status")return{ready:phase==="ready",job:{phase,planId:"p",assetCount:2}};
    if(path==="/api/offline/packages")return{workspaces:[],snapshots:[]};
    if(path==="/api/offline/download"){phase="ready";throw TypeError("Lost response");}
  });
  await run.workspace.refresh();await run.click("offline-download");await run.workspace.refresh();
  assert.equal(run.calls.filter(call=>call.path==="/api/offline/download").length,1);
  assert.equal(run.calls.filter(call=>call.path==="/api/offline/packages"&&call.body).length,0);
  assert.match(run.node("offline-workspace-error").textContent,/Refresh status/);
});

test("Help and back preserve import token, selected exclusions and page without re-preview",async()=>{
  const run=setup();response(run);run.workspace.setView("offline-import");await choose(run);
  const checkbox=run.node("offline-import-rows").querySelectorAll("input")[0];checkbox.checked=false;checkbox.listeners.change();
  await run.workspace.showView("offline-help");await run.click("help-back");
  assert.equal(run.redirects.at(-1),"offline-import");await run.workspace.showView("offline-import");
  assert.equal(run.node("offline-import-rows").querySelectorAll("input")[0].checked,false);
  assert.equal(run.calls.filter(call=>call.path==="/api/offline/import/preview").length,1);
});

test("field help opens on click and closing returns focus; normal surfaces have no saved-input warning",async()=>{
  const run=setup();await run.click("name-help");
  assert.equal(run.node("offline-field-help").open,true);
  assert.match(run.node("offline-field-help-copy").textContent,/Optional/);
  run.node("offline-field-help").close();assert.equal(run.document.activeElement,run.node("name-help"));
  run.workspace.updateSummary();assert.equal(run.node("offline-save-notice").hidden,true);
});

test("Exports navigation is conditional and imported local progress does not force an empty Pending page",async()=>{
  const run=setup();response(run,{"/api/offline/import/apply":async()=>({imported:2,skipped:0,snapshot:{id:"before"}})});
  await run.workspace.refresh();assert.equal(run.node("offline-exports-link").hidden,true);
  await choose(run);await run.click("offline-import-apply");
  assert.equal(run.node("offline-import-pending").hidden,true);assert.equal(run.imported[0][1].navigate,false);
  run.state.summary.counts={pending:1};run.workspace.updateSummary();assert.equal(run.node("offline-import-pending").hidden,false);
});

test("stage progress avoids a completed-original bar during preview and unknown finishing work",async()=>{
  const run=setup();
  run.status({job:{phase:"downloading",step:"preview",planId:"p",assetCount:4,downloadedFiles:4,downloadedBytes:1024,stepDone:1,stepTotal:4}});
  await run.workspace.refresh();assert.equal(run.node("offline-workspace-progress").value,1);assert.match(run.node("offline-workspace-description").textContent,/Saving previews.*4 \/ 4 photos/);
  run.status({job:{phase:"downloading",step:"person_thumbnail",planId:"p",assetCount:4,downloadedFiles:4}});
  await run.workspace.refresh();assert.equal(run.node("offline-workspace-progress").attributes.value,undefined);
  assert.match(run.node("offline-workspace-description").textContent,/Saving portraits/);
});

test("a previous ready snapshot cannot satisfy a newly planned generation",async()=>{
  const run=setup();let phase="planned",snapshotId="previous";
  run.reply(async(path,options)=>{
    if(path==="/api/offline/status")return{ready:true,snapshot:{id:snapshotId},job:{phase,planId:"new-selection",assetCount:3,estimatedBytes:4096}};
    if(path==="/api/offline/packages")return options.method==="POST"?{id:"new-copy"}:{workspaces:[],snapshots:[]};
    if(path==="/api/offline/download"){phase="downloading";return{};}
  });
  await run.workspace.refresh();await run.click("offline-download");
  assert.equal(run.calls.filter(call=>call.path==="/api/offline/download").length,1);
  assert.equal(run.calls.filter(call=>call.path==="/api/offline/packages"&&call.body).length,0);
  phase="ready";await run.workspace.refresh();
  assert.equal(run.calls.filter(call=>call.path==="/api/offline/packages"&&call.body).length,0);
  snapshotId="new-selection";await run.workspace.refresh();
  assert.equal(run.calls.filter(call=>call.path==="/api/offline/packages"&&call.body).length,1);
});

test("single-page preview hides paging controls while additional pages expose them",async()=>{
  const run=setup();response(run,{"/api/offline/import/preview":async()=>({...groups(),changes:[groups().changes[0]],counts:{ready:1,conflict:0,applied:0},readyIds:["one"],nextCursor:null})});
  await choose(run);assert.equal(run.node("offline-import-pagination").hidden,true);
  response(run);await choose(run,file("second return"));assert.equal(run.node("offline-import-pagination").hidden,false);
});
