const expr = process.argv[2] || "location.href";
const list = await (await fetch("http://127.0.0.1:9222/json/list")).json();
const pages = list.filter(p => p.type === "page");
const page = pages.find(p => p.url.includes("github.com")) || pages[0];
if (!page) throw new Error("no page");
const ws = new WebSocket(page.webSocketDebuggerUrl);
let seq = 0;
const pending = new Map();
ws.onmessage = event => {
  const msg = JSON.parse(event.data);
  if (msg.id && pending.has(msg.id)) {
    pending.get(msg.id)(msg);
    pending.delete(msg.id);
  }
};
await new Promise((resolve, reject) => {
  ws.onopen = resolve;
  ws.onerror = reject;
});
function call(method, params = {}) {
  const id = ++seq;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise(resolve => pending.set(id, resolve));
}
const result = await call("Runtime.evaluate", {
  expression: expr,
  returnByValue: true,
  awaitPromise: true,
  userGesture: true,
});
console.log(JSON.stringify({ url: page.url, result }, null, 2));
ws.close();
