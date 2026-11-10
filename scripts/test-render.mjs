#!/usr/bin/env node
// Isolated DOM test: real React, React Query, installed SDK UI and route classifier.
// npm install --prefix "$RECEIPT_TEST_DEPS" react@19 react-dom@19 @tanstack/react-query@5 esbuild@0.25 jsdom@26 class-variance-authority radix-ui clsx tailwind-merge @tabler/icons-react nanostores
import { createRequire } from 'node:module'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve, join } from 'node:path'
import { pathToFileURL } from 'node:url'
const deps = process.env.RECEIPT_TEST_DEPS
const host = process.env.HERMES_DESKTOP_SOURCE
if (!deps || !host) throw new Error('Set RECEIPT_TEST_DEPS (npm prefix) and HERMES_DESKTOP_SOURCE (apps/desktop/src)')
const require = createRequire(join(resolve(deps), 'package.json'))
const { build } = require('esbuild')
const { JSDOM } = require('jsdom')
const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost/' })
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, IS_REACT_ACT_ENVIRONMENT: true })
Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true })
const root = mkdtempSync(join(tmpdir(), 'receipt-render-'))
const sdk = readFileSync(join(host, 'sdk/index.ts'), 'utf8')
if (!sdk.includes("export { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'")) throw new Error('Host query contract changed; review test adapter')
const entry = `
import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import plugin from '${resolve('desktop/plugin.js')}';
import {contributedRoutes, appViewForPath} from '${join(host, 'app/routes.ts')}';
import {registry} from '@/contrib/registry';
const assert = (ok, message) => {if (!ok) throw new Error(message)};
const calls=[];
let status={configured:false,mode:'securo',settings:{securo_url:'http://synthetic.invalid'}};
let failStatus=false;
let ledger={counts:{charges:1,with_paper:0,missing:1},transactions:[{id:'test:1',date:'2026-01-01',vendor:'Synthetic Vendor',amount:10,status:'missing'}],review_queue:[{id:'test:1',vendor:'Synthetic Vendor',amount:10}],classification_summary:{reconciles:true}};
const ctx={register:c=>registry.items.push(c),rest:async(path,opts)=>{calls.push({path,opts});await new Promise(r=>setTimeout(r,5)); if(path==='/status'){if(failStatus)throw new Error('synthetic status unavailable');return status}if(path==='/ledger')return ledger;if(path==='/settings')return {...status,ledger};return {ok:true}}};
plugin.register(ctx);
assert(appViewForPath('/receipt-ledger')==='extension','real host classifies plugin route as extension');
const route=contributedRoutes().find(r=>r.path==='/receipt-ledger');assert(route,'real host discovers render route');
const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:Infinity}}});
const root=createRoot(document.getElementById('root'));
const mount=async()=>{await act(async()=>{root.render(React.createElement(QueryClientProvider,{client},route.render()));await new Promise(r=>setTimeout(r,20))});await act(async()=>{await new Promise(r=>setTimeout(r,20))})};
await mount();
assert(document.body.textContent.includes('Receipt ledger'),'page title mounts');
assert(document.body.textContent.includes('Synthetic Vendor'),'async real query renders row');
const button = text => [...document.querySelectorAll('button')].find(b=>b.textContent===text);
for(const label of ['Business','Personal','Mixed','LLC owes me','Show','Federal','Show the journal'])assert(button(label),'missing visible button: '+label);
assert(document.querySelector('[aria-label="Securo URL"]').value==='http://synthetic.invalid','setup hydrated from async status');
await act(async()=>button('CSV + folder').click());
await act(async()=>{button('Save and scan').click();await new Promise(r=>setTimeout(r,10))});
const saved=calls.find(c=>c.path==='/settings');
assert(saved.opts.body.securo_url==='','files mode explicitly clears Securo source');
assert(saved.opts.body.from_date!==undefined,'files mode preserves editable date window');
await act(async()=>{client.clear();failStatus=true});
await mount();
assert(document.body.textContent.includes('synthetic status unavailable'),'status failure is visible, not empty setup');
assert(button('Retry'),'failed queries offer retry');
failStatus=false;
await act(async()=>{button('Retry').click();await new Promise(r=>setTimeout(r,30))});
await act(async()=>{await new Promise(r=>setTimeout(r,30))});
assert(document.body.textContent.includes('Receipt ledger'),'retry restores page');
await act(async()=>root.unmount());client.clear();
console.log('PASS: real host route classifier, React DOM, React Query, SDK components, button labels, hydrated setup, source switch, status error and retry');
`
try {
 await build({stdin:{contents:entry,resolveDir:process.cwd(),loader:'tsx'},outfile:join(root,'test.mjs'),bundle:true,format:'esm',platform:'node',jsx:'automatic',nodePaths:[join(resolve(deps),'node_modules')],plugins:[{name:'installed-sdk-adapter',setup(b){
 b.onResolve({filter:/^@hermes\/plugin-sdk$/},()=>({path:'sdk',namespace:'fixture'}))
 b.onResolve({filter:/^@\/contrib\/registry$/},()=>({path:'registry',namespace:'fixture'}))
 b.onResolve({filter:/^@\/components\/pane-shell\/tree\/store$/},()=>({path:'tree',namespace:'fixture'}))
 b.onLoad({filter:/.*/,namespace:'fixture'},a=>({contents:a.path==='registry'?`export const registry={items:[],getArea(area){return this.items.filter(c=>c.area===area)},subscribeArea(){return ()=>{}}}`:a.path==='tree'?`export const noteActiveTreeGroup=()=>{};export const revealTreePane=()=>{}`:`export {useQuery,useQueryClient} from '@tanstack/react-query';export {ROUTES_AREA,SIDEBAR_NAV_AREA} from '${join(host,'app/routes.ts')}';export const PALETTE_AREA='palette';export const host={notify(){}};${['Badge','Button','Codicon','EmptyState','Input','SegmentedControl'].map(n=>`export {${n}} from '${join(host,'components/ui',n.replace(/[A-Z]/g,(c,i)=>(i?'-':'')+c.toLowerCase())+'.tsx')}';`).join('\n')}`,resolveDir:process.cwd(),loader:'ts'}))
 b.onResolve({filter:/^@\//},a=>({path:join(host,a.path.slice(2))+ '.ts'}))
 }}]})
 await import(pathToFileURL(join(root,'test.mjs')).href)
} finally {rmSync(root,{recursive:true,force:true});dom.window.close()}
// React's development Scheduler uses a MessageChannel in a DOM-like Node
// environment. Assertions and component teardown are complete; close the runner.
process.exit(0)
