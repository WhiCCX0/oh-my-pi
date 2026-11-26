import { expect, test } from "bun:test";
import { applyEdits, lookup, replaceExactly, transform } from "./translate";
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
