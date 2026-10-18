import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import { setImmediate } from 'node:timers/promises';

const source = readFileSync(new URL('../scripts/viewer.js', import.meta.url), 'utf8');
const response = () => ({ok: true, json: async () => ({project: 'Project', deadline: 999999})});
const flush = async () => { await setImmediate(); await setImmediate(); };

function page(fetch = async () => response()) {
  const elements = Object.fromEntries(['image', 'state', 'name', 'home', 'rotate', 'reconnect']
    .map(id => [id, {textContent: '', removeAttribute(name) { delete this[name]; }}]));
  Object.assign(elements.image, {naturalWidth: 1000, naturalHeight: 2000,
    getBoundingClientRect: () => ({left: 0, top: 0, width: 390, height: 700}),
    setPointerCapture() {}});
  const events = {}, sockets = [];
  class Socket {
    static OPEN = 1;
    readyState = 1;
    messages = [];
    constructor() { sockets.push(this); queueMicrotask(() => this.onopen?.()); }
    send(data) { this.messages.push([data[0], JSON.parse(Buffer.from(data).subarray(1).toString())]); }
    close() { this.readyState = 3; this.onclose?.(); }
  }
  const document = {hidden: false, querySelector: selector => elements[selector.slice(1)],
    addEventListener: (name, handler) => { events[name] = handler; }};
  const context = {document, window: {addEventListener() {}}, WebSocket: Socket, fetch,
    history: {replaceState() {}}, location: {hash: '', pathname: '/', protocol: 'https:', host: 'example.test'},
    TextEncoder, URLSearchParams, setInterval: () => 1, clearInterval() {},
    setTimeout: () => 2, clearTimeout() {}};
  vm.runInNewContext(source, context);
  return {elements, events, sockets, document};
}

test('touch coordinates map to the fitted frame, excluding letterbox space', async () => {
  const p = page(); await flush();
  const event = {pointerId: 1, clientX: 300, clientY: 350, preventDefault() {}};
  p.elements.image.onpointerdown(event);
  p.elements.image.onpointerup(event);
  const messages = p.sockets[0].messages;
  assert.equal(messages[0][0], 3);
  assert.equal(messages[0][1].type, 'begin');
  assert.equal(messages[0][1].x, 0.8);
  assert.equal(messages[0][1].y, 0.5);
  assert.equal(messages[1][1].type, 'end');
  p.elements.image.onpointerdown({...event, clientX: 1});
  assert.equal(messages.length, 2);
});

test('backgrounding releases a pressed finger, closes input and stops video', async () => {
  const p = page(); await flush();
  p.elements.image.onpointerdown({pointerId: 1, clientX: 200, clientY: 350, preventDefault() {}});
  p.document.hidden = true;
  p.events.visibilitychange();
  assert.equal(p.sockets[0].messages.at(-1)[1].type, 'end');
  assert.equal(p.sockets[0].readyState, 3);
  assert.equal(p.elements.image.src, undefined);
});

test('a stale async connection cannot resurrect video after visibility changes', async () => {
  let resolveFirst, calls = 0;
  const p = page(async () => ++calls === 1 ? new Promise(resolve => { resolveFirst = resolve; }) : response());
  await flush();
  p.document.hidden = true; p.events.visibilitychange();
  p.document.hidden = false; p.events.visibilitychange();
  await flush();
  assert.equal(p.sockets.length, 1);
  resolveFirst(response()); await flush();
  assert.equal(p.sockets.length, 1);
  assert.equal(p.sockets[0].readyState, 1);
});
