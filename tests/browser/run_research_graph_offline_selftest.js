#!/usr/bin/env node
'use strict';
const assert = require('assert/strict');
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const base = path.resolve(__dirname, '../..');
const read = file => fs.readFileSync(path.join(base, file), 'utf8');
const app = read('frontend/js/app.js');
const api = read('frontend/js/api.js');
const runtime = read('frontend/js/offline-runtime.js');
const core = 'research-graph-core', people = 'research-graph-people';
const copy = value => JSON.parse(JSON.stringify(value));
const snapshot = {
    nodes: [
        {id:'concept:C', record_id:'C', type:'concept', label:'Concept', route:'#/concepts/C'},
        {id:'work:W', record_id:'W', type:'work', label:'Work', route:'#/works/W', doc_type:''},
        {id:'argument:A', record_id:'A', type:'argument', kind:'stance', label:'Stance', route:'#/arguments/A'},
        {id:'position:P', record_id:'P', type:'position', label:'Position', route:'#/positions/P'},
    ],
    edges: [{id:'mentions_concept:work:W>concept:C',type:'mentions_concept',source:'work:W',target:'concept:C',count:1}],
    meta:{node_count:4,edge_count:1,derived_note_edges_available:true,people_included:false},
};
function environment() {
    const entities = new Map();
    let request = async () => ({ok:true,status:200,json:async()=>copy(snapshot)});
    let sweep = async kind => { entities.delete(kind); return true; };
    const store = {
        putEntity: async (kind, id, value) => {entities.set(kind,{value,cachedAt:1000});return true;},
        getEntity: async kind => entities.get(kind) || null,
        deleteEntity: async kind => {entities.delete(kind);return true;},
        deleteEntitiesByKind: kind => sweep(kind),
    };
    const ctx = {console, setTimeout:()=>0, clearTimeout:()=>{}, createPrksOfflineStore:()=>store,
        prksRequest:(...args)=>request(...args)};
    ctx.window = ctx;
    vm.createContext(ctx);
    vm.runInContext(runtime,ctx);
    vm.runInContext(api,ctx);
    vm.runInContext(app.slice(app.indexOf('async function prksOfflineDetailFetch'),app.indexOf('const PRKS_CONCEPTS_LIST_KEY')),ctx);
    return {ctx,entities,setRequest:fn=>request=fn,setSweep:fn=>sweep=fn};
}
const tick = () => new Promise(resolve=>setImmediate(resolve));
async function run() {
    const {ctx} = environment();
    assert(ctx.prksIsResearchGraphSnapshot(snapshot,false));
    const degraded=copy(snapshot);degraded.meta.derived_note_edges_available=false;
    assert(ctx.prksIsResearchGraphSnapshot(degraded,false));
    const empty={nodes:[],edges:[],meta:{node_count:0,edge_count:0,derived_note_edges_available:false,people_included:true}};
    assert(ctx.prksIsResearchGraphSnapshot(empty,true));
    const corruptions = [
        s=>s.nodes={}, s=>s.edges={}, s=>s.meta=[], s=>s.meta.node_count++, s=>s.meta.edge_count++,
        s=>s.meta.people_included=true, s=>s.meta.derived_note_edges_available=0,
        s=>s.nodes.push(s.nodes[0]), s=>s.nodes[0].type='unknown', s=>s.nodes[0].type=['concept'],
        s=>s.nodes[0].id='concept:wrong',s=>s.nodes[0].record_id='',s=>s.nodes[0].label=null,
        s=>s.nodes[0].route='https://example.com',s=>s.nodes[0].route='#/works/C',
        s=>s.nodes[0].record_id='../C',s=>s.nodes[2].kind='unknown',s=>s.nodes[1].doc_type=1,
        s=>s.edges[0].source='work:missing',s=>s.edges[0].target='concept:missing',
        s=>s.edges[0].type='unknown',s=>s.edges[0].id='wrong',s=>s.edges.push(s.edges[0]),
        s=>s.edges[0].count=0,s=>s.edges[0].count=1.5,
        s=>{s.edges[0].target='work:W';s.edges[0].id='mentions_concept:work:W>work:W';},
        s=>{s.nodes[0]={id:'person:C',record_id:'C',type:'person',label:'P',route:'#/people/C'};s.edges=[];},
    ];
    for (const change of corruptions) {
        const s=copy(snapshot);change(s);
        // Preserve counts when testing structural corruption instead of count mismatch.
        if(Array.isArray(s.nodes) && s.nodes.length!==4) s.meta.node_count=s.nodes.length;
        if(Array.isArray(s.edges) && s.edges.length!==1) s.meta.edge_count=s.edges.length;
        assert.equal(ctx.prksIsResearchGraphSnapshot(s,false),false,change.toString());
    }
    for (const [type,pair] of Object.entries({concept_parent:['concept:C','concept:C'],argument_position:['argument:A','position:P'],argument_argument:['argument:A','argument:A'],argument_source:['argument:A','work:W']})) {
        const s=copy(snapshot);s.edges=[{type,id:type+':'+pair[0]+'>'+pair[1],source:pair[0],target:pair[1]}];
        assert(ctx.prksIsResearchGraphSnapshot(s,false));
        const field=type==='argument_source'?'pages':'verdict_label';
        if(type!=='concept_parent'){s.edges[0][field]=0;assert(!ctx.prksIsResearchGraphSnapshot(s,false));}
    }
    for (const [key,limit] of [['nodes',2500],['edges',7500]]) {
        const s=copy(snapshot);s[key]=Array(limit+1).fill(s[key][0]);s.meta[key==='nodes'?'node_count':'edge_count']=limit+1;
        assert(!ctx.prksIsResearchGraphSnapshot(s,false));
    }
    // A pending initial layout may complete after a failed People toggle.
    // Applying the original focus must not erase the actionable variant error.
    {
        const source=read('frontend/js/components/research-graph.js');
        const start=source.indexOf('        function applyFocusAfterLayout()');
        const end=source.indexOf('        function mountCytoscape',start);
        const focus={pendingFocus:'person:P',snapshot:{},statusMessage:'Core graph is not available offline on this device.',
            nodeById:()=>({id:'person:P'}),renderStatusMessage:()=>{},renderInspector:()=>{},selectNode:id=>focus.selected=id};
        vm.createContext(focus);vm.runInContext(source.slice(start,end)+';applyFocusAfterLayout();',focus);
        assert.equal(focus.selected,'person:P');
        assert.equal(focus.statusMessage,'Core graph is not available offline on this device.');
    }
    // App helpers invoke actual runtime generations and kind-only sweeps.
    for(const helper of ['prksMarkResearchGraphCoreChanged','prksMarkResearchGraphPeopleChanged']) {
        const e=environment();for(const k of [core,people,'concept','person','position','argument','work','person-group','playlist'])e.entities.set(k,{value:snapshot});
        const sweeps=[];const finishes=[];
        e.setSweep(kind=>{sweeps.push(kind);return new Promise(resolve=>finishes.push(()=>{e.entities.delete(kind);resolve(true);}));});
        const pending=e.ctx[helper]();
        assert.equal(e.ctx.prksOfflineDomainGeneration(people),1);
        assert.equal(e.ctx.prksOfflineDomainGeneration(core),helper.includes('Core')?1:0);
        assert(e.ctx.prksOfflineIsDomainBlocked(people));
        await tick();assert.deepEqual(sweeps,helper.includes('Core')?[core,people]:[people]);
        finishes.forEach(f=>f());await pending;
        assert(e.entities.has('concept'));assert(e.entities.has('work'));
    }
    // Failed cleanup stays local. Stale GETs never republish; People-only changes allow core GET.
    for(const variant of [false,true]) {
        for(const coreChange of [false,true]) {
            const e=environment();let resolve;
            e.setRequest(()=>new Promise(r=>resolve=r));
            const pending=e.ctx.prksOfflineResearchGraphFetch(variant);
            await (coreChange?e.ctx.prksMarkResearchGraphCoreChanged():e.ctx.prksMarkResearchGraphPeopleChanged());
            const s=copy(snapshot);s.meta.people_included=variant;
            resolve({ok:true,status:200,json:async()=>s});await pending;await tick();
            assert.equal(e.entities.has(variant?people:core),!variant&&!coreChange);
        }
        const e=environment();e.entities.set(core,{value:snapshot,cachedAt:1});
        e.entities.set(people,{value:{...snapshot,meta:{...snapshot.meta,people_included:true}},cachedAt:1});
        e.setSweep(async()=>false);
        await (variant?e.ctx.prksMarkResearchGraphPeopleChanged():e.ctx.prksMarkResearchGraphCoreChanged());
        e.setRequest(async()=>{throw new Error('offline');});
        assert.equal((await e.ctx.prksOfflineResearchGraphFetch(variant)).source,'unavailable');
        if(variant)assert.equal((await e.ctx.prksOfflineResearchGraphFetch(false)).source,'cache');
    }
    // Superseded sweeps cannot unblock either newer generation.
    {
        const e=environment(), finishes=[];
        e.setSweep(kind=>new Promise(resolve=>finishes.push(resolve)));
        e.ctx.prksMarkResearchGraphCoreChanged();
        e.ctx.prksMarkResearchGraphCoreChanged();
        finishes[0](true);finishes[1](true);await tick();
        assert(e.ctx.prksOfflineIsDomainBlocked(core));
        assert(e.ctx.prksOfflineIsDomainBlocked(people));
        finishes[2](true);finishes[3](true);await tick();
        assert(!e.ctx.prksOfflineIsDomainBlocked(core));
        assert(!e.ctx.prksOfflineIsDomainBlocked(people));
    }
    // One failed kind sweep does not stop its sibling's cleanup or other entities.
    {
        const e=environment();
        for(const k of [core,people,'concept','position','argument','person','person-group','playlist','work'])e.entities.set(k,{value:snapshot});
        e.setSweep(async kind=>{if(kind===core)return false;e.entities.delete(kind);return true;});
        e.ctx.prksMarkResearchGraphCoreChanged();await tick();
        assert(e.ctx.prksOfflineIsDomainBlocked(core));assert(!e.ctx.prksOfflineIsDomainBlocked(people));
        assert(!e.entities.has(people));
        for(const k of ['concept','position','argument','person','person-group','playlist','work'])assert(e.entities.has(k));
    }
    // Durable writes issue no canonical request. Family-specific optimistic
    // Graph behavior belongs to each state module's selftest, not this helper
    // environment (which intentionally does not load those modules).
    // This test pins only that API adapters cannot bypass the queue.
    // And the durable half of that rule, asserted rather than assumed: a
    // Concept write issues no canonical request and moves neither Graph
    // generation, whatever the (unused) transport would have answered.
    for(const fn of ['createConcept','updateConcept','deleteConcept','putConceptParents','putConceptAliases','createPosition','updatePosition','deletePosition','createArgument','updateArgument','deleteArgument','putArgumentSources','putArgumentTargets']) {
        const e=environment();
        let requests=0;
        e.setRequest(async()=>{requests+=1;return {ok:true,status:200,json:async()=>({})};});
        try {await e.ctx[fn]('id',[]);}catch(err){/* an unknown base is a refusal, not a request */}
        assert.equal(requests,0,fn+' must not reach the network');
        assert.equal(e.ctx.prksOfflineDomainGeneration(core),0,fn);
        assert.equal(e.ctx.prksOfflineDomainGeneration(people),0,fn);
    }
    /* A role change never stales the CORE graph, and no longer stales the
     * People graph either: a role acknowledgement patches that edge exactly --
     * both node and edge shapes are fully determined -- so invalidating would
     * throw the patch away and leave the Graph unavailable offline for a
     * change PRKS can draw. */
    for(const role of ['Author','Reviewer','Editor','Translator','Mentioned']) {
        const e=environment();e.ctx.prksMarkWorkRoleChanged('W',role);
        assert.equal(e.ctx.prksOfflineDomainGeneration(core),0);
        assert.equal(e.ctx.prksOfflineDomainGeneration(people),0,role);
    }
    const e=environment();e.ctx.prksMarkWorkTitleChanged('W');
    assert.equal(e.ctx.prksOfflineDomainGeneration(core),1);assert.equal(e.ctx.prksOfflineDomainGeneration(people),1);
    console.log('Research Graph offline validators, helpers, isolation and stale reads passed');
}
run().catch(err=>{console.error(err);process.exitCode=1;});
