// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../web/pending.js", import.meta.url), "utf8");
const { createPending } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
function node() {
  const listeners = new Map();
  const classes = new Set();
  const attributes = new Map();
  return {
    listeners, children: [], value: "", dataset: {}, style: {}, scrollTop: 0, clientHeight: 600, clientWidth: 600, offsetHeight: 600,
    classList: {
      add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name),
      toggle: (name, force) => { if (force ?? !classes.has(name)) classes.add(name); else classes.delete(name); },
    },
    append(...items) { for (const item of items) { item.parent = this; this.children.push(item); } },
    replaceChildren(...items) { this.children = []; this.append(...items); },
    insertBefore(item, following) {
      item.parent = this;
      const index = following ? this.children.indexOf(following) : -1;
      if (index < 0) this.children.push(item); else this.children.splice(index, 0, item);
    },
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(item => item !== this); },
    querySelectorAll(selector) {
      if (selector === "input") return this.children.flatMap(item => [ ...(item.type === "checkbox" ? [item] : []), ...item.querySelectorAll(selector)]);
      if (!selector.startsWith(".")) return [];
      const matches = [];
      const visit = parent => {
        for (const item of parent.children) {
          if (item.className?.split(" ").includes(selector.slice(1))) matches.push(item);
          visit(item);
        }
      };
      visit(this);
      return matches;
    },
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; },
    addEventListener: (event, handler) => listeners.set(event, handler),
    setAttribute: (name, value) => attributes.set(name, String(value)),
    getAttribute: name => attributes.get(name) ?? null,
    removeAttribute: name => attributes.delete(name),
    focus() { globalThis.document.activeElement = this; }, closest: () => null,
    contains(target) { for (let current = target; current; current = current.parent) if (current === this) return true; return false; },
    showModal() { this.open = true; },
    close() { this.open = false; this.listeners.get("close")?.(); },
  };
}

const defer = () => { let resolve, reject; const promise = new Promise((yes,no) => {resolve=yes;reject=no;}); return {promise,resolve,reject}; };
const tick = () => new Promise(resolve => setImmediate(resolve));
const person = (id="source-A", operation="merge") => ({kind:"person", personId:id, operation,
  targetPersonId: operation === "merge" ? `target-${id}` : null,
  targetName: operation === "merge" ? `Named ${id}` : "", currentName:`Current ${id}`, name:`Named ${id}`, included:true, mergeStep:"ready"});

function setup(items=[person()], mode={}) {
  const previous = {document:globalThis.document, confirm:globalThis.confirm};
  const elements=new Map(), events=new Map();
  const el=selector=>{if(!elements.has(selector))elements.set(selector,node());return elements.get(selector);};
  globalThis.document={querySelector:el, createElement:()=>node(), addEventListener:(name,fn)=>events.set(name,fn)};
  globalThis.confirm=()=>true;
  const state={pages:{pending:1},pageSize:24,summary:{counts:{included:items.filter(i=>i.included).length,pending:items.length}},faceReviewSaving:false};
  const ui={pendingList:node(),detail:node()}, calls=[],toasts=[];
  let gets=0;
  const api=async(path,options={})=>{
    calls.push({path,body:options.body});
    if(path.startsWith("/api/pending?")) {
      gets++;
      if(mode.pending) return mode.pending(gets,items);
      return {pending:items.map(i=>({...i})),total:items.length,page:1,reviewToken:"synthetic-review-token"};
    }
    if(path.startsWith("/api/samples/"))return mode.samples?mode.samples(path.slice(13)):{samples:[]};
    if(path.endsWith("/include")) {
      if(mode.include) return mode.include(options.body,items);
      const item=items.find(i=>i.personId===options.body.personId||i.faceId===options.body.faceId);item.included=options.body.included;return{};
    }
    if(path==="/api/sync") {
      if(typeof mode.sync === "function") return mode.sync(options.body,items);
      if(mode.sync) await mode.sync.promise;
      calls.push({applied:items.filter(i=>i.included).map(i=>i.personId||i.faceId)});return{results:[],failed:0};
    }
    return{};
  };
  const refreshSummary=async()=>{if(mode.summary)await mode.summary();state.summary.counts.included=items.filter(i=>i.included).length;};
  const controls=createPending({api,assetThumb:id=>id,personThumb:id=>id,cleanName:name=>name.trim(),drawFace:async()=>{},
    loadNames:async()=>{},matchingNames:()=>[],openAssetPreview(){},refreshSummary,renderPagination(){},
    showToast:message=>toasts.push(message),state,ui,waitUntilReady:async()=>{},});
  controls.bindPendingControls();
  return{el,state,ui,calls,toasts,controls,items,restore:()=>Object.assign(globalThis,previous)};
}
const inspect=(run,index)=>run.ui.pendingList.children[index].children[4].children[0].listeners.get("click")();
const click=(run,id)=>run.el(id).listeners.get("click")();
const confirmSync=async run=>{await click(run,"#sync-button");return click(run,"#sync-confirm-apply");};

for(const kind of ["person","face"]) {
  test(`Pending ${kind} failed exclusion restores persisted selection and shows error`,async()=>{
    const item=kind==="person"?person():{kind:"face",faceId:"face-A",personId:"person-A",included:true};
    const run=setup([item],{include:async()=>{throw Error("synthetic exclusion failure");}});
    try {await run.controls.renderPending();const checkbox=run.ui.pendingList.children[0].children[0];checkbox.checked=false;
      await checkbox.listeners.get("change")();assert.equal(checkbox.checked,true);
      assert.equal(run.ui.pendingList.children[0].children[0].checked,true);
      assert.match(run.toasts.at(-1),/exclusion failure/);
    }finally{run.restore();}
  });
  test(`Pending ${kind} delayed exclusion blocks forced Sync until persistence`,async()=>{
    const wait=defer(),item=kind==="person"?person():{kind:"face",faceId:"face-A",personId:"person-A",included:true};
    const run=setup([item],{include:async body=>{await wait.promise;item.included=body.included;}});
    try {await run.controls.renderPending();const checkbox=run.ui.pendingList.children[0].children[0];checkbox.checked=false;
      const saving=checkbox.listeners.get("change")();assert.equal(run.el("#sync-button").disabled,true);
      assert.equal(checkbox.disabled,true);await click(run,"#sync-button");assert.equal(run.calls.some(c=>c.path==="/api/sync"),false);
      wait.resolve();await saving;assert.equal(run.ui.pendingList.children[0].children[0].checked,false);
      assert.equal(run.el("#sync-button").disabled,true);
    }finally{wait.resolve();run.restore();}
  });
}

test("Lost inclusion response uses authoritative persisted selection",async()=>{
  const item=person(),run=setup([item],{include:async body=>{item.included=body.included;throw Error("synthetic lost reply");}});
  try{await run.controls.renderPending();const cb=run.ui.pendingList.children[0].children[0];cb.checked=false;await cb.listeners.get("change")();
    assert.equal(run.ui.pendingList.children[0].children[0].checked,false);assert.equal(run.el("#sync-button").disabled,true);
  }finally{run.restore();}
});

test("A stale Pending read cannot verify selection after an ambiguous save",async()=>{
  const stale=defer(),write=defer(),items=[{...person(),included:false},person("other")];
  let summaryFails=true;
  const run=setup(items,{pending:(get,current)=>get===2?stale.promise:{pending:current.map(i=>({...i})),total:current.length,page:1,reviewToken:"synthetic-review-token"},
    include:async body=>{await write.promise;items[0].included=body.included;throw Error("synthetic lost reply");},
    summary:async()=>{if(summaryFails)throw Error("synthetic refresh failure");}});
  try{await run.controls.renderPending();const cb=run.ui.pendingList.children[0].children[0];cb.checked=true;
    const save=cb.listeners.get("change")(),read=run.controls.renderPending();write.resolve();await save;
    stale.resolve({pending:[{...items[0],included:false},items[1]],total:2,page:1,reviewToken:"synthetic-review-token"});await read;
    assert.equal(run.el("#sync-button").disabled,true);await click(run,"#sync-button");assert.equal(run.calls.some(c=>c.path==="/api/sync"),false);
    summaryFails=false;await run.controls.renderPending();assert.equal(run.ui.pendingList.children[0].children[0].checked,true);
    assert.equal(run.el("#sync-button").disabled,false);
  }finally{write.resolve();stale.resolve({pending:[],total:0,page:1,reviewToken:"synthetic-review-token"});run.restore();}
});

for(const order of ["A-first","B-first"]) {
  for(const action of ["Save","Return"]) {
    test(`Inspect ${order} ${action} targets only the displayed latest person`,async()=>{
      const a=defer(),b=defer(),run=setup([person("A"),person("B")],{samples:id=>id==="A"?a.promise:id==="B"?b.promise:{samples:[]}});
      try{await run.controls.renderPending();const first=inspect(run,0),second=inspect(run,1);
        if(order==="A-first"){a.resolve({samples:[]});await first;assert.equal(run.el("#detail-save").disabled,true);await click(run,"#detail-save");assert.equal(run.calls.some(c=>c.path==="/api/queue"),false);b.resolve({samples:[]});await second;}
        else{b.resolve({samples:[]});await second;a.resolve({samples:[]});await first;}
        assert.equal(run.state.detailItem.personId,"B");assert.equal(run.el("#detail-name").value,"Named B");
        if(action==="Save"){run.el("#detail-name").value="Corrected B";run.el("#detail-name").listeners.get("input")();await click(run,"#detail-save");
          assert.deepEqual(run.calls.find(c=>c.path==="/api/queue").body,{personId:"B",operation:"rename",name:"Corrected B",featureAssetId:undefined});}
        else{await click(run,"#return-button");assert.equal(run.calls.find(c=>c.path==="/api/pending/return").body.personId,"B");}
      }finally{a.resolve({samples:[]});b.resolve({samples:[]});run.restore();}
    });
  }
}

test("Closing and reopening Inspect rejects a previous photo load",async()=>{
  const a=defer(),run=setup([person("A"),person("B")],{samples:id=>id==="A"?a.promise:{samples:[]}});
  try{await run.controls.renderPending();const first=inspect(run,0);run.ui.detail.close();await inspect(run,1);
    a.resolve({samples:[]});await first;assert.equal(run.el("#detail-name").value,"Named B");assert.equal(run.state.detailItem.personId,"B");
    run.ui.detail.close();await click(run,"#return-button");assert.equal(run.calls.some(c=>c.path==="/api/pending/return"),false);
  }finally{a.resolve({samples:[]});run.restore();}
});

for(const operation of ["merge","hide","unhide"])test(`Unchanged Inspect Save preserves ${operation}`,async()=>{
  const run=setup([person("A",operation)]);
  try{await run.controls.renderPending();await inspect(run,0);await click(run,"#detail-save");assert.equal(run.calls.some(c=>c.path==="/api/queue"),false);assert.equal(run.ui.detail.open,false);}
  finally{run.restore();}
});

for(const mergeStep of ["complete","inflight","uncertain"])test(`Inspect cannot replace merge checkpoint ${mergeStep}`,async()=>{
  const run=setup([{...person(),mergeStep}]);
  try{await run.controls.renderPending();await inspect(run,0);assert.equal(run.el("#detail-save").disabled,true);
    run.el("#detail-name").value="Changed";await click(run,"#detail-save");assert.equal(run.calls.some(c=>c.path==="/api/queue"),false);
    assert.match(run.el("#detail-kind").textContent,mergeStep==="complete"?/photo update/:/uncertain/);
  }finally{run.restore();}
});

test("Pending mutation controls block while explicit Sync is running",async()=>{
  const wait=defer(),run=setup([person()],{sync:wait});
  try{await run.controls.renderPending();await inspect(run,0);await click(run,"#sync-button");const sync=click(run,"#sync-confirm-apply");
    assert.equal(run.el("#detail-save").disabled,true);assert.equal(run.el("#return-button").disabled,true);assert.equal(run.el("#reset-button").disabled,true);
    await click(run,"#return-button");assert.equal(run.calls.some(c=>c.path==="/api/pending/return"),false);
    wait.resolve();await sync;assert.equal(run.el("#return-button").disabled,false);
  }finally{wait.resolve();run.restore();}
});


test("Stale review token refreshes changed Pending and requires another explicit Sync",async()=>{
  let revision="first", persisted=false, syncCalls=0;
  const items=[{...person(),included:false},person("other")];
  const run=setup(items,{pending:(_get,current)=>({pending:current.map(i=>({...i})),total:current.length,page:1,reviewToken:revision}),
    include:async()=>{throw Error("synthetic lost inclusion reply");},
    sync:async body=>{
      syncCalls++;
      if(body.reviewToken!==revision)throw Error("Pending changed; review updated selections before syncing again.");
      persisted=true;return{results:[],failed:0};
    }});
  try{await run.controls.renderPending();const cb=run.ui.pendingList.children[0].children[0];cb.checked=true;
    await cb.listeners.get("change")(); // Fresh read still sees the old state.
    items[0].included=true;revision="late-inclusion"; // Original delayed save finishes afterwards.
    await confirmSync(run);assert.equal(persisted,false);assert.equal(syncCalls,1);
    assert.match(run.toasts.at(-1),/Pending changed/);
    assert.equal(run.ui.pendingList.children[0].children[0].checked,true);
    await confirmSync(run);assert.equal(persisted,true);assert.equal(syncCalls,2);
    assert.equal(run.calls.filter(c=>c.path==="/api/sync")[1].body.reviewToken,"late-inclusion");
  }finally{run.restore();}
});

test("Missing snapshot token cannot enable or invoke HTTP Sync",async()=>{
  const run=setup([person()],{pending:(_get,current)=>({pending:current,total:1,page:1})});
  try{await run.controls.renderPending();assert.equal(run.el("#sync-button").disabled,true);await click(run,"#sync-button");
    assert.equal(run.calls.some(c=>c.path==="/api/sync"),false);
  }finally{run.restore();}
});


test("A deferred native close event cannot cancel a reopened inspector",async()=>{
  const run=setup([person("A"),person("B")]);
  try{await run.controls.renderPending();await inspect(run,0);
    run.ui.detail.open=false; // Native close queued its event, but has not dispatched it.
    await inspect(run,1);
    run.ui.detail.listeners.get("close")(); // Old event is delivered after reopening.
    assert.equal(run.ui.detail.open,true);assert.equal(run.state.detailItem.personId,"B");
    assert.equal(run.el("#detail-name").value,"Named B");assert.equal(run.el("#detail-save").disabled,false);
    run.el("#detail-name").value="Corrected B";run.el("#detail-name").listeners.get("input")();
    await click(run,"#detail-save");assert.equal(run.calls.find(c=>c.path==="/api/queue").body.personId,"B");
    assert.equal(run.ui.detail.open,false);assert.equal(run.state.detailItem,null);
  }finally{run.restore();}
});

test("Sync opens an in-app dialog and cancellation never writes",async()=>{
  const run=setup([person()]);
  globalThis.confirm=()=>{throw Error("Sync must not open a browser confirmation");};
  try{await run.controls.renderPending();await click(run,"#sync-button");
    assert.equal(run.el("#sync-confirm-dialog").open,true);
    assert.equal(globalThis.document.activeElement,run.el("#sync-confirm-apply"));
    assert.equal(run.calls.some(c=>c.path==="/api/sync"),false);
    assert.equal(run.el("#reset-button").disabled,true);
    await click(run,"#sync-confirm-cancel");
    assert.equal(run.el("#sync-confirm-dialog").open,false);
    assert.equal(globalThis.document.activeElement,run.el("#sync-button"));
    assert.equal(run.el("#sync-button").disabled,false);
    assert.equal(run.calls.some(c=>c.path==="/api/sync"),false);
  }finally{run.restore();}
});

test("Escape cancels Sync confirmation and prevents a later Apply from writing",async()=>{
  const run=setup([person()]);
  try{await run.controls.renderPending();await click(run,"#sync-button");let prevented=false;
    run.el("#sync-confirm-dialog").listeners.get("cancel")({preventDefault(){prevented=true;}});
    assert.equal(prevented,true);assert.equal(run.el("#sync-confirm-dialog").open,false);
    await click(run,"#sync-confirm-apply");assert.equal(run.calls.some(c=>c.path==="/api/sync"),false);
  }finally{run.restore();}
});

test("Tab and Shift+Tab keep focus within the Sync dialog",async()=>{
  const run=setup([person()]);
  try{await run.controls.renderPending();await click(run,"#sync-button");
    const handler=run.el("#sync-confirm-dialog").listeners.get("keydown");
    for(const shiftKey of [false,false,true,true]){
      const previous=globalThis.document.activeElement;let prevented=false,stopped=false;
      handler({key:"Tab",shiftKey,preventDefault(){prevented=true;},stopPropagation(){stopped=true;}});
      assert.equal(prevented,true);assert.equal(stopped,true);
      assert.equal(globalThis.document.activeElement,previous===run.el("#sync-confirm-apply")?run.el("#sync-confirm-cancel"):run.el("#sync-confirm-apply"));
    }
    assert.equal(run.calls.some(c=>c.path==="/api/sync"),false);
  }finally{run.restore();}
});

test("Explicit modal Apply uses its original reviewed token and cannot double Sync",async()=>{
  let revision="reviewed";const wait=defer();
  const run=setup([person()],{pending:(_get,current)=>({pending:current,total:1,page:1,reviewToken:revision}),sync:wait});
  try{await run.controls.renderPending();await click(run,"#sync-button");
    revision="changed-while-open";await run.controls.renderPending();
    const applying=click(run,"#sync-confirm-apply");
    assert.equal(run.el("#sync-confirm-dialog").open,false);
    assert.equal(run.calls.find(c=>c.path==="/api/sync").body.reviewToken,"reviewed");
    await click(run,"#sync-confirm-apply");await click(run,"#sync-button");
    assert.equal(run.calls.filter(c=>c.path==="/api/sync").length,1);
    wait.resolve();await applying;
  }finally{wait.resolve();run.restore();}
});

test("A new Face-review save cancels Sync confirmation before writing",async()=>{
  const run=setup([person()]);
  try{await run.controls.renderPending();await click(run,"#sync-button");run.state.faceReviewSaving=true;
    await click(run,"#sync-confirm-apply");assert.equal(run.el("#sync-confirm-dialog").open,false);
    assert.equal(run.calls.some(c=>c.path==="/api/sync"),false);assert.match(run.toasts.at(-1),/local saves/);
  }finally{run.restore();}
});

test("Deferred old Sync-dialog close cannot cancel a reopened confirmation",async()=>{
  const run=setup([person()]);
  try{await run.controls.renderPending();await click(run,"#sync-button");await click(run,"#sync-confirm-cancel");
    await click(run,"#sync-button");run.el("#sync-confirm-dialog").listeners.get("close")();
    await click(run,"#sync-confirm-apply");assert.equal(run.calls.filter(c=>c.path==="/api/sync").length,1);
  }finally{run.restore();}
});
