import { mkdtempSync, readdirSync, readFileSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterAll, describe, expect, it } from "vitest";
import { build } from "vite";
import { mockFetch } from "./helpers.js";
import { api } from "../src/api.js";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
// XML namespace identifiers are not network requests.
const ALLOWED_URLS = new Set(["http://www.w3.org/2000/svg"]);
const URL_RE = /(?:https?:)?\/\/[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}[^\s"'`)<>]*/gi;

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

function externalUrls(text) {
  return (text.match(URL_RE) ?? []).filter((u) => !ALLOWED_URLS.has(u.replace(/[`;]+$/, "")));
}

describe("no external resources", () => {
  it("source files reference no external hosts, fonts, CDNs or trackers", () => {
    const files = [join(root, "index.html"), ...walk(join(root, "src"))];
    for (const file of files) {
      const text = readFileSync(file, "utf-8");
      expect(externalUrls(text), file).toEqual([]);
      expect(text).not.toMatch(/@import\s+url\(/i);
      expect(text).not.toMatch(/googleapis|gstatic|jsdelivr|unpkg|cdnjs|analytics|gtag|sentry|segment\.io/i);
      expect(text).not.toMatch(/localStorage|sessionStorage|indexedDB|sendBeacon/);
    }
  });

  it("only calls relative same-origin API paths", async () => {
    const calls = mockFetch({ "GET /health": { status: "ok" } });
    await api.health();
    expect(calls[0].url).toBe("/api/v1/health");
  });
});

describe("production build", () => {
  const outDir = mkdtempSync(join(tmpdir(), "papercue-dash-"));
  afterAll(() => rmSync(outDir, { recursive: true, force: true }));

  it("builds locally and the bundle requests no external resources", async () => {
    await build({ root, logLevel: "silent", build: { outDir, emptyOutDir: true } });
    const files = walk(outDir);
    expect(files.some((f) => f.endsWith("index.html"))).toBe(true);
    expect(files.some((f) => f.endsWith(".js"))).toBe(true);
    const html = readFileSync(join(outDir, "index.html"), "utf-8");
    expect(html).toContain('lang="ko"');
    expect(html).not.toMatch(/<script>(?!<\/script>)/); // no inline scripts (CSP script-src 'self')
    for (const file of files) {
      expect(externalUrls(readFileSync(file, "utf-8")), file).toEqual([]);
    }
  }, 60_000);
});
