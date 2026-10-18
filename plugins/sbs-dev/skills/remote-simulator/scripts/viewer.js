/* The upstream HID wire format is pinned with serve-sim 0.1.48. */
const image = document.querySelector('#image');
const status = document.querySelector('#state');
const pointers = new Map();
let socket, timer, reconnectTimer, landscape = false, started = false, generation = 0;
function send(tag, payload) {
  if (socket?.readyState !== WebSocket.OPEN) return;
  const body = new TextEncoder().encode(JSON.stringify(payload));
  const message = new Uint8Array(body.length + 1);
  message[0] = tag; message.set(body, 1); socket.send(message);
}
function touch(type, event) {
  const rect = image.getBoundingClientRect();
  const scale = Math.min(rect.width / image.naturalWidth, rect.height / image.naturalHeight);
  const width = image.naturalWidth * scale, height = image.naturalHeight * scale;
  if (!width || !height) return;
  const x = (event.clientX - rect.left - (rect.width - width) / 2) / width;
  const y = (event.clientY - rect.top - (rect.height - height) / 2) / height;
  if (type === 'begin' && (x < 0 || x > 1 || y < 0 || y > 1)) return;
  const point = {x: Math.max(0, Math.min(1, x)), y: Math.max(0, Math.min(1, y))};
  pointers.set(event.pointerId, point);
  const points = [...pointers.values()];
  if (points.length === 1) send(3, {type, ...point});
  else if (points.length === 2) send(5, {type, x1: points[0].x, y1: points[0].y,
    x2: points[1].x, y2: points[1].y});
}
image.onpointerdown = event => {
  event.preventDefault(); image.setPointerCapture(event.pointerId);
  // End the single-finger gesture before beginning a pinch.
  if (pointers.size === 1) send(3, {type: 'end', ...pointers.values().next().value});
  touch('begin', event);
};
image.onpointermove = event => { if (pointers.has(event.pointerId)) touch('move', event); };
function end(event) {
  if (!pointers.has(event.pointerId)) return;
  touch('end', event); pointers.delete(event.pointerId);
  if (pointers.size === 1) send(3, {type: 'begin', ...pointers.values().next().value});
}
image.onpointerup = end; image.onpointercancel = end;
async function heartbeat() {
  if (document.hidden) return;
  const response = await fetch('/heartbeat', {method: 'POST'});
  if (!response.ok) throw new Error('Session expired. Ask Codex to start a new session.');
  const state = await response.json();
  document.querySelector('#name').textContent = state.project;
}
function pause() {
  generation += 1;
  started = false;
  clearInterval(timer); clearTimeout(reconnectTimer);
  const points = [...pointers.values()];
  if (points.length === 1) send(3, {type: 'end', ...points[0]});
  else if (points.length >= 2) send(5, {type: 'end', x1: points[0].x, y1: points[0].y,
    x2: points[1].x, y2: points[1].y});
  pointers.clear();
  if (socket) { socket.onclose = null; socket.close(); }
  image.removeAttribute('src');
}
async function connect() {
  pause(); if (document.hidden) return;
  const attempt = generation;
  status.textContent = 'Connecting…';
  try {
    const token = new URLSearchParams(location.hash.slice(1)).get('token');
    if (token) {
      const response = await fetch('/auth', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({token})});
      if (!response.ok) throw new Error('Invalid session link.');
      history.replaceState(null, '', location.pathname);
    }
    await heartbeat();
    if (document.hidden || generation !== attempt) return;
    started = true;
    image.src = '/stream';
    socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/input`);
    socket.onopen = () => { status.textContent = 'Live · drag to swipe, use two fingers to pinch'; };
    socket.onclose = () => {
      if (started && !document.hidden && generation === attempt) {
        status.textContent = 'Disconnected · reconnecting…';
        reconnectTimer = setTimeout(connect, 3000);
      }
    };
    timer = setInterval(() => heartbeat().catch(error => {
      if (generation === attempt) { pause(); status.textContent = error.message; }
    }), 15000);
  } catch (error) { if (generation === attempt) status.textContent = error.message; }
}
image.onerror = () => { status.textContent = 'Video unavailable · try Reconnect'; };
document.querySelector('#home').onclick = () => send(4, {button: 'home'});
document.querySelector('#rotate').onclick = () => {
  landscape = !landscape; send(7, {orientation: landscape ? 'landscape_left' : 'portrait'});
};
document.querySelector('#reconnect').onclick = connect;
document.addEventListener('visibilitychange', () => document.hidden ? pause() : connect());
window.addEventListener('pagehide', pause);
connect();
