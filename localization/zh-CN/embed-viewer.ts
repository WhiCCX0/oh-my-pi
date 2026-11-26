import { parse } from "@babel/parser";
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { createHash } from "node:crypto";
import { replaceExactly } from "./translate";

// The localized build has a different viewer content hash and must not
// point to a nonexistent asset on the official Stencil CDN. Embed the
// verified generated viewer using the same HTML escaping as upstream.
const dir = "packages/coding-agent/src/export/html";
const manifest = JSON.parse(await readFile(join(dir, "viewer.generated.json"), "utf8"));
if (!/^export-viewer-[a-f0-9]{64}\.js$/.test(manifest.file)) throw new Error("Unexpected viewer asset name");
const bytes = await readFile(join("packages/coding-agent/export-assets", manifest.file));
const digest = createHash("sha256").update(bytes);
if ("sha256-" + digest.digest("base64") !== manifest.integrity || bytes.length !== manifest.size) {
	throw new Error("Localized export viewer integrity mismatch");
}
await writeFile(join(dir, "zh-viewer.generated.txt"), bytes);
const file = join(dir, "index.ts");
let source = await readFile(file, "utf8");
const importLine = 'import viewerAsset from "./viewer.generated.json" with { type: "json" };';
source = replaceExactly(source, importLine, importLine + '\nimport zhViewer from "./zh-viewer.generated.txt" with { type: "text" };', file);
const lines = source.split("\n");
const start = lines.findIndex(line => line.includes("const viewerUrl = ") && line.includes("https://build.stencil.so/web/omp/"));
const escapeLine = lines.find(line => line.includes("const escaped = viewer.replace("));
const inlineLine = lines.find(line => line.includes('viewerScript = ') && line.includes('DOMContentLoaded'));
if (start < 0 || !escapeLine || !inlineLine || !lines[start + 1]?.includes('viewerScript = ')) {
	throw new Error("Required export viewer anchor changed");
}
source = replaceExactly(source, lines.slice(start, start + 2).join("\n"),
	escapeLine.replace("viewer.replace(", "zhViewer.replace(") + "\n" + inlineLine, file);
parse(source, { sourceType: "module", plugins: ["typescript"] });
await writeFile(file, source);
