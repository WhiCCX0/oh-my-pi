import { parse } from "@babel/parser";
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { createHash } from "node:crypto";
import { replaceExactly } from "./translate";

// The localized build has a different viewer content hash and must not
// point to a nonexistent asset on the official Stencil CDN. Embed the
// verified generated viewer using the same HTML escaping as upstream.
export function rewriteViewer(source: string, file: string): string {
	const eol = source.includes("\r\n") ? "\r\n" : "\n";
	const importLine = 'import viewerAsset from "./viewer.generated.json" with { type: "json" };';
	source = replaceExactly(source, importLine, importLine + eol + 'import zhViewer from "./zh-viewer.generated.txt" with { type: "text" };', file);
	const lines = source.split(/\r?\n/);
	const unique = (predicate: (line: string) => boolean): number => {
		const positions = lines.flatMap((line, index) => predicate(line) ? [index] : []);
		if (positions.length !== 1) throw new Error("Required export viewer anchor changed: " + file);
		return positions[0];
	};
	const url = unique(line => line.includes("const viewerUrl = ") && line.includes("https://build.stencil.so/web/omp/"));
	const escape = unique(line => line.includes("const escaped = viewer.replace("));
	const inline = unique(line => line.includes("viewerScript = ") && line.includes("DOMContentLoaded"));
	const compiled = unique(line => line.includes("viewerScript = ") && line.includes("${viewerUrl}"));
	if (escape >= inline || inline >= url || compiled <= url || lines.slice(url + 1, compiled).some(line => line.trim() && !line.trimStart().startsWith("//"))) {
		throw new Error("Unexpected export viewer control flow: " + file);
	}
	const indent = (line: string) => line.match(/^\s*/)?.[0] ?? "";
	lines[url] = indent(lines[url]) + lines[escape].trimStart().replace("viewer.replace(", "zhViewer.replace(");
	lines[compiled] = indent(lines[compiled]) + lines[inline].trimStart();
	const result = lines.join(eol);
	parse(result, { sourceType: "module", plugins: ["typescript"] });
	return result;
}
async function main() {
	const dir = "packages/coding-agent/src/export/html";
	const manifest = JSON.parse(await readFile(join(dir, "viewer.generated.json"), "utf8"));
	if (!/^export-viewer-[a-f0-9]{64}\.js$/.test(manifest.file)) throw new Error("Unexpected viewer asset name");
	const bytes = await readFile(join("packages/coding-agent/export-assets", manifest.file));
	const digest = createHash("sha256").update(bytes);
	if ("sha256-" + digest.digest("base64") !== manifest.integrity || bytes.length !== manifest.size) {
		throw new Error("Localized export viewer integrity mismatch");
	}
	const file = join(dir, "index.ts");
	const source = rewriteViewer(await readFile(file, "utf8"), file);
	await writeFile(join(dir, "zh-viewer.generated.txt"), bytes);
	await writeFile(file, source);
}
if (import.meta.main) await main();
