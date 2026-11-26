import { expect, test } from "bun:test";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { applyEdits, lookup, replaceExactly, transform , settingDisplayValues } from "./translate";
import { rewriteViewer } from "./embed-viewer";
const dict = { Approve: "批准", Deny: "拒绝", "Use --model": "使用 --model", "Bad --model": "错误", "AI coding assistant": "AI 编程助手" };
test("only display JSX positions change", () => {
	const source = 'const x = <item key="Approve" id="Deny" label="Approve" onAction={() => "Deny"}>{ok ? "Approve" : "Deny"}</item>; const y = id === "Approve";';
	const result = transform(source, "packages/coding-agent/src/ui/test.tsx", dict);
	expect(result.count).toBe(3);
	expect(result.source).toContain('key="Approve" id="Deny"');
	expect(result.source).toContain('onAction={() => "Deny"}');
	expect(result.source).toContain('id === "Approve"');
	expect(result.source).toContain('label={"批准"}');
	expect(transform(result.source, "packages/coding-agent/src/ui/test.tsx", dict).count).toBe(0);
});
test("JSX text retains whitespace", () => {
	expect(transform("const x = <text> Approve </text>;", "ui.tsx", dict).source).toContain(' {"批准"} ');
});
test("only CLI descriptions change", () => {
	const source = 'const x = { description: "AI coding assistant", name: "Approve", options: ["Deny"] };';
	const result = transform(source, "packages/coding-agent/src/commands/test.ts", dict);
	expect(result.count).toBe(1);
	expect(result.source).toContain('description: "AI 编程助手"');
	expect(result.source).toContain('options: ["Deny"]');
});
test("unknown text and missing technical tokens fall back", () => {
	expect(lookup(dict, "unknown")).toBe("unknown");
	expect(lookup(dict, "Use --model")).toBe("使用 --model");
	expect(lookup(dict, "Bad --model")).toBe("Bad --model");
});
test("critical anchors fail closed", () => {
	expect(replaceExactly("abc", "b", "B", "x")).toBe("aBc");
	expect(() => replaceExactly("aaa", "a", "b", "x")).toThrow();
	expect(() => replaceExactly("abc", "d", "b", "x")).toThrow();
});

test("JSX safely encodes quotes, backslashes, newlines and markup", () => {
	const special = {
		"Title marker": '他说："C:\\temp"\n继续',
		"Text marker": "显示 <标签> {}",
	};
	const result = transform('<item title="Title marker">Text marker</item>;', "packages/coding-agent/src/ui/test.tsx", special);
	expect(result.count).toBe(2);
	expect(result.source).toContain("title={" + JSON.stringify(special["Title marker"]) + "}");
	expect(result.source).toContain("{" + JSON.stringify(special["Text marker"]) + "}");
});

test("URL sentence punctuation and balanced delimiters are normalized", () => {
	const source = "Open https://example.com/docs_(v1).";
	const translated = "打开 https://example.com/docs_(v1)。";
	expect(lookup({ [source]: translated }, source)).toBe(translated);
	const fullwidth = "Read https://example.com/path";
	expect(lookup({ [fullwidth]: "阅读 https://example.com/path）。" }, fullwidth)).toBe("阅读 https://example.com/path）。");
	expect(lookup({ "[Must start with http:// or https://]": "[必须以 http:// 或 https:// 开头]" }, "[Must start with http:// or https://]")).toBe("[必须以 http:// 或 https:// 开头]");
	const cdp = "Default HTTP CDP discovery endpoint (for example http://127.0.0.1:9222) to attach to instead of launching a browser.";
	const cdpZh = "默认 HTTP CDP 发现端点（例如 http://127.0.0.1:9222），用于连接现有浏览器而不是启动新浏览器。";
	expect(lookup({ [cdp]: cdpZh }, cdp)).toBe(cdpZh);
});

test("printf directives, flags and named placeholders are protected", () => {
	expect(lookup({ "Progress %s for {name}": "进度 %s：{name}" }, "Progress %s for {name}")).toBe("进度 %s：{name}");
	expect(lookup({ "Progress %s": "进度 %d" }, "Progress %s")).toBe("Progress %s");
	expect(lookup({ "100% used": "100% 已用" }, "100% used")).toBe("100% 已用");
	expect(lookup({ "20% of quota": "配额的 20%" }, "20% of quota")).toBe("配额的 20%");
	expect(lookup({ "Use --Model": "使用 --Model" }, "Use --Model")).toBe("使用 --Model");
	expect(lookup({ "Use --Model": "使用 --model" }, "Use --Model")).toBe("Use --Model");
	expect(lookup({ "Open {{path}}": "打开 {{file}}" }, "Open {{path}}")).toBe("Open {{path}}");
	expect(lookup({ "Open ${path}": "打开 ${file}" }, "Open ${path}")).toBe("Open ${path}");
});

test("identical edits are deduplicated and conflicting overlaps fail with context", () => {
	expect(applyEdits("abc", [
		{ start: 1, end: 2, text: "x" },
		{ start: 1, end: 2, text: "x" },
	])).toBe("axc");
	expect(() => applyEdits("abcdef", [
		{ start: 1, end: 4, text: "x" },
		{ start: 2, end: 3, text: "y" },
	], "overlap.ts")).toThrow("overlap.ts");
	expect(() => applyEdits("abc", [{ start: -1, end: 1, text: "x" }], "range.ts")).toThrow("range.ts");
});

test("parse failures include the source path", () => {
	expect(() => transform("const = ;", "broken.ts", {})).toThrow("broken.ts");
});

test("every shipped dictionary entry preserves technical tokens", async () => {
	const names = ["dict-extra.json", "dict.json", "overrides.json"];
	const layers = await Promise.all(names.map(async name =>
		JSON.parse(await readFile(join(import.meta.dir, name), "utf8")) as Record<string, string>
	));
	const dictionary = Object.assign({}, ...layers) as Record<string, string>;
	const mismatches = Object.entries(dictionary)
		.filter(([source, translation]) => lookup(dictionary, source) !== translation);
	if (mismatches.length > 0) {
		throw new Error("Dictionary token mismatches: " + JSON.stringify(mismatches.slice(0, 10)));
	}
	expect(Object.keys(dictionary).length).toBeGreaterThan(6000);
});

test("shared command-help descriptions, nested JSX and fragments", () => {
	const help = transform('export const x = {description: "Approve", name: "Deny"};', "packages/coding-agent/src/cli/command-help.ts", dict);
	expect(help.source).toContain('description: "批准"');
	expect(help.source).toContain('name: "Deny"');
	const jsx = transform('const x = <item title={ok ? <span>Approve</span> : "Deny"}>{<>{"Approve"}</>}</item>;', "ui.tsx", dict);
	expect(jsx.count).toBe(3);
	expect(jsx.source).toContain('<span>{"批准"}</span>');
	expect(jsx.source).toContain('<>{"批准"}</>');
});
test("ordered printf and numeric/dotted placeholders", () => {
	const source = "Move %s to %d";
	expect(lookup({[source]: "将 %d 移至 %s"}, source)).toBe(source);
	expect(lookup({"Read {0} {user.name}": "读取 {1} {user.name}"}, "Read {0} {user.name}")).toBe("Read {0} {user.name}");
	const url = "(see https://example.com.)";
	expect(lookup({[url]: "参见 https://example.com"}, url)).toBe("参见 https://example.com");
});

test("CLI angle-bracket arguments retain their exact spelling", () => {
	const source = "Use /rename <title>";
	expect(lookup({[source]: "使用 /rename <标题>"}, source)).toBe(source);
	expect(lookup({[source]: "使用 /rename <title>"}, source)).toBe("使用 /rename <title>");
});

test("decoded JSX entities translate without changing boundary whitespace", () => {
	const d = {"Approve & continue": "批准并继续"};
	expect(transform("<text> Approve &amp; continue </text>;", "ui.tsx", d).source).toContain(' {"批准并继续"} ');
	expect(transform("<text>&nbsp;Approve &amp; continue</text>;", "ui.tsx", d).count).toBe(0);
});

test("runtime settings projection collects only display metadata", () => {
	const values = settingDisplayValues([
		{ui: {label: "Yolo", description: "Describe", warning: "Risk", options: [{label: "Option", description: "Detail", value: "internal"} as {label: string; description: string}]}},
		{ui: {label: "Theme", options: "runtime"}}, {},
	]);
	expect([...values].sort()).toEqual(["Describe", "Detail", "Option", "Risk", "Theme", "Yolo"]);
});

test("viewer rewrite preserves CRLF and accepts comments between checked anchors", () => {
	const fixture = [
		'import viewerAsset from "./viewer.generated.json" with { type: "json" };',
		'let viewerScript: string; declare const viewer: string; declare const sourceCheckout: boolean;',
		'if (sourceCheckout) {',
		'  const escaped = viewer.replace(/x/g, "y");',
		'  viewerScript = `<script>window.addEventListener("DOMContentLoaded",function(){${escaped}});</script>`;',
		'} else {',
		'  const viewerUrl = "https://build.stencil.so/web/omp/fixture";',
		'  // retained layout comment',
		'  viewerScript = `<script src="${viewerUrl}"></script>`;',
		'}',
	].join("\r\n");
	const result = rewriteViewer(fixture, "fixture.ts");
	expect(result).toContain("zhViewer.replace(");
	expect(result).toContain("// retained layout comment");
	expect(result).not.toContain("https://build.stencil.so/web/omp/");
	expect(result.replaceAll("\r\n", "")).not.toContain("\n");
	expect(() => rewriteViewer(fixture.replace("viewerUrl =", "unexpected ="), "fixture.ts")).toThrow();
});
