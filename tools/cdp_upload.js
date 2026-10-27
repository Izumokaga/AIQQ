const files = process.argv.slice(2);
if (!files.length) throw new Error("usage: node cdp_upload.js <file> [...]");
const list = await (await fetch("http://127.0.0.1:9222/json/list")).json();
const page = list.find(p => p.type === "page" && p.url.includes("/upload/"));
if (!page) throw new Error("upload page not found");
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
await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
function call(method, params = {}) {
  const id = ++seq;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise(resolve => pending.set(id, resolve));
}
await call("DOM.enable");
const doc = await call("DOM.getDocument", { depth: -1, pierce: true });
const root = doc.result.root.nodeId;
const found = await call("DOM.querySelector", { nodeId: root, selector: "#upload-manifest-files-input" });
if (!found.result || !found.result.nodeId) throw new Error("file input not found");
const set = await call("DOM.setFileInputFiles", { nodeId: found.result.nodeId, files });
console.log(JSON.stringify({ set, url: page.url }, null, 2));
ws.close();
