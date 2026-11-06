const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const elements = new Map();
const element = id => {
    if (!elements.has(id)) elements.set(id, {value: id === 'player-volume' ? '0.7' : 0,
        style: {setProperty() {}}, classList: {add() {}}, replaceChildren() {},
        getBoundingClientRect: () => ({height: 180})});
    return elements.get(id);
};
let registered;
const context = vm.createContext({assert, console, Number, String, Math,
    document: {getElementById: element, body: element('body')},
    window: {Chart: true, addEventListener: (_, fn) => fn()},
    Chart: {register: plugin => {registered = plugin;}, helpers: {getRelativePosition: e => e}},
    ResizeObserver: class {constructor(fn) {this.fn = fn;} observe() {this.fn();}}
});
vm.runInContext(fs.readFileSync('web/assets/visualization-player.js', 'utf8'), context);
const api = context.window.AudioParkPlayer;
function player() {
    const handlers = {};
    return {handlers, position: 0, on: (event, fn) => { handlers[event] = fn; },
        setVolume(v) {this.volume = v;}, getDuration: () => 60,
        setTime(v) {this.position = v; handlers.timeupdate(v);},
        getCurrentTime() {return this.position;}, playPause: () => Promise.resolve()};
}
let draws = 0;
const chart = {canvas: {}, chartArea: {left: 10, right: 110, top: 0, bottom: 100},
    scales: {x: {getValueForPixel: x => (x - 10) * .6}}, draw: () => draws++};
api.reset();
const first = player();
api.bind(first, t => api.drawCharts({chart}, t));
api.seek(10);
assert.equal(first.position, 0, 'cannot seek until ready');
first.handlers.ready();
assert.equal(first.volume, .7);
api.attachChart(chart);
chart.canvas.onclick({x: 60, y: 50});
assert.equal(first.position, 30, 'chart click seeks audio');
assert.equal(element('player-progress').value, 30);
first.handlers.timeupdate(42);
assert.equal(element('player-progress').value, 42, 'audio drives progress');
assert(draws > 0, 'audio redraws charts');
element('player-forward').onclick();
assert.equal(first.position, 52);
api.seek(90); assert.equal(first.position, 60);
api.seek(-10); assert.equal(first.position, 0);
chart.canvas.onclick({x: 5, y: 50}); assert.equal(first.position, 0);
element('player-progress').oninput({target: {value: '12.5'}});
assert.equal(first.position, 12.5);
const calls = [];
const drawing = {ctx: new Proxy({}, {get: (_, k) => (...args) => calls.push([k, ...args]), set: () => true}),
    chartArea: chart.chartArea, scales: {x: {getPixelForValue: t => t + 10}}};
api.drawCharts({}, 12.5);
registered.afterDraw(drawing);
assert(calls.some(c => c[0] === 'moveTo' && c[1] === 22.5), 'playhead uses current audio time');
api.reset();
const second = player(); api.bind(second, () => {}); second.handlers.ready();
first.handlers.timeupdate(50);
assert.equal(element('player-progress').value, 0, 'old recording events are ignored');
second.handlers.error(new Error('missing'));
assert.equal(element('player-play').disabled, true);
const html = fs.readFileSync('web/visualization.html', 'utf8');
for (const [, code] of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) if (code.trim()) new vm.Script(code);
console.log('PASS: playback, seek, skip, bounds, chart clicks, playhead, stale events, errors, inline JS syntax');
