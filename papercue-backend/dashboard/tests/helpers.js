import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { vi } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));

export function fixture(name) {
  return JSON.parse(readFileSync(join(here, "fixtures", `${name}.json`), "utf-8"));
}

export function textOf(node) {
  return node.textContent.replace(/\s+/g, " ");
}

/** Replace global fetch with a router: { "GET /path": response | (body) => response }. */
export function mockFetch(routes) {
  const calls = [];
  const fn = vi.fn(async (url, init = {}) => {
    const method = init.method ?? "GET";
    const path = String(url).replace(/^\/api\/v1/, "");
    calls.push({ method, path, url: String(url) });
    const handler = routes[`${method} ${path}`];
    if (handler === undefined) {
      return new Response(JSON.stringify({ error: { code: "not_found", message: "not found" } }), { status: 404 });
    }
    const value = typeof handler === "function" ? handler(init.body ? JSON.parse(init.body) : undefined) : handler;
    const status = value?.__status ?? 200;
    return new Response(JSON.stringify(value?.__body ?? value), { status, headers: { "Content-Type": "application/json" } });
  });
  globalThis.fetch = fn;
  return calls;
}
