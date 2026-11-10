// Local dev server: static files + the Netlify functions that the app
// calls (currently /.netlify/functions/plenum). Production runs the same
// handlers on Netlify; locally `python3 -m http.server` cannot, so the
// plenum reader used to show its "live site only" fallback. Run:
//
//   node dev_server.mjs            # http://127.0.0.1:8094
//   PORT=8092 node dev_server.mjs  # custom port
//
// Dev-only: binds 127.0.0.1, serves this repo read-only. The function
// handlers are imported as-is — what you test here is the deployed code.
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize, resolve, sep } from "node:path";
import plenumHandler from "./netlify/functions/plenum.mjs";

const ROOT = resolve(process.cwd());
const PORT = Number(process.env.PORT || 8094);
const MIME = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".mjs": "text/javascript",
  ".json": "application/json", ".css": "text/css", ".svg": "image/svg+xml",
  ".png": "image/png", ".jpg": "image/jpeg", ".ico": "image/x-icon",
  ".pdf": "application/pdf", ".md": "text/plain; charset=utf-8",
};
const FUNCTIONS = { "/.netlify/functions/plenum": plenumHandler };

const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${PORT}`);
  const fn = FUNCTIONS[url.pathname];
  if (fn) {
    try {
      const request = new Request(url, { method: req.method, headers: req.headers });
      const response = await fn(request);
      res.writeHead(response.status, Object.fromEntries(response.headers));
      res.end(Buffer.from(await response.arrayBuffer()));
    } catch (e) {
      res.writeHead(500, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: String(e && e.message || e) }));
    }
    return;
  }
  // Static files, confined to the repo (CWE-22: no path traversal).
  const rel = normalize(decodeURIComponent(url.pathname)).replace(/^([/\\])+/, "");
  const file = join(ROOT, url.pathname === "/" ? "index.html" : rel);
  if (file !== ROOT && !file.startsWith(ROOT + sep)) { res.writeHead(403); return res.end("forbidden"); }
  try {
    const body = await readFile(file);
    res.writeHead(200, { "Content-Type": MIME[extname(file)] || "application/octet-stream" });
    res.end(body);
  } catch {
    res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    res.end("not found");
  }
});
server.listen(PORT, "127.0.0.1", () => console.log(`mandat121 dev: http://127.0.0.1:${PORT} (static + plenum function)`));
