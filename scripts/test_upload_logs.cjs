// Real page controller with a minimal DOM: filters, pagination, safe text and export.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../web/upload-logs.html'), 'utf8');
const ids = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]));
const nodes = new Map(), listeners = {}, requests = [];
class Element {
    constructor(tag = 'div') {this.tag = tag; this.children = []; this.value = ''; this.checked = false; this.hidden = false; this.disabled = false; this.events = {};}
    append(...items) {this.children.push(...items);}
    replaceChildren(...items) {this.children = items;}
    addEventListener(name, callback) {this.events[name] = callback;}
    set innerHTML(_) {throw new Error('Unsafe HTML rendering');}
}
const item = {id:101, created_at:1794993977, upload_id:'a'.repeat(32), user_id:22,
    login:'example@local.test', event:'rejected', stage:'validating', http_status:400,
    message:'<img src=x onerror=alert(1)>', part_number:null, byte_count:null, elapsed_seconds:null,
    platform:'Android', browser:'Яндекс 25', current_state:'uploading', result_id:'AP-test',
    last_activity:1794993977, received_parts:6, received_bytes:19667672, source:'live'};
let reply = {events:[item], next_before:101, has_more:true, upload_counts:{uploading:1}}, copied;
let status = 200;
const context = vm.createContext({console, Date, URLSearchParams, encodeURIComponent,
    AudioPark:{url:path=>'/audio_park'+path, ready:Promise.resolve({role:'admin'})},
    navigator:{clipboard:{writeText:async text=>{copied = text;}}},
    document:{createElement:tag=>new Element(tag),
        addEventListener:(name, callback)=>{listeners[name] = callback;},
        getElementById(id) {assert(ids.has(id), 'Missing HTML element '+id); if(!nodes.has(id)) nodes.set(id,new Element()); return nodes.get(id);}},
    fetch:async url=>{requests.push(new URL(url, 'https://local.test')); return {ok:status===200, json:async()=>structuredClone(reply)};}
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/assets/upload-logs.js'), 'utf8'), context);
const tick = async()=>{await new Promise(resolve=>setImmediate(resolve));};
const all = node=>[node,...node.children.flatMap(all)];
(async()=>{
    listeners.DOMContentLoaded(); await tick();
    assert.equal(nodes.get('log-events').children.length,1);
    assert(all(nodes.get('log-events')).some(node=>node.textContent===item.message), 'Server text must remain literal');
    assert.equal(nodes.get('log-more').hidden,false);
    nodes.get('log-query').value='edited-but-not-submitted';
    reply={events:[{...item,id:100}], has_more:false, next_before:null, upload_counts:{uploading:1}};
    await nodes.get('log-more').events.click(); await tick();
    assert.equal(requests.at(-1).searchParams.get('before'),'101');
    assert.equal(requests.at(-1).searchParams.get('q'),'');
    assert.equal(nodes.get('log-events').children.length,2);
    nodes.get('log-query').value='example@local.test'; nodes.get('log-errors').checked=true;
    nodes.get('log-filters').events.submit({preventDefault(){}}); await tick();
    assert.equal(requests.at(-1).searchParams.get('q'),'example@local.test');
    assert.equal(requests.at(-1).searchParams.get('errors'),'1');
    assert.equal(nodes.get('log-events').children.length,1);
    const filter=all(nodes.get('log-events')).find(node=>node.tag==='button');
    filter.events.click(); await tick();
    assert.equal(requests.at(-1).searchParams.get('q'),item.upload_id);
    assert.equal(requests.at(-1).searchParams.get('errors'),'0');
    await nodes.get('log-copy').events.click();
    assert.equal(JSON.parse(copied).events.length,1);
    status=403;reply={error:'Недостаточно прав'};
    nodes.get('log-filters').events.submit({preventDefault(){}}); await tick();
    assert.equal(nodes.get('log-status').textContent,'Недостаточно прав');
    assert.equal(nodes.get('log-refresh').disabled,false);
    console.log('upload diagnostics UI: safe text, filters, stable pagination, upload history, export and denied access: ok');
})().catch(error=>{console.error(error);process.exitCode=1;});
