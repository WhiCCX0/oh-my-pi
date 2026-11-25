import { expect, test } from "bun:test";
import { lookup, replaceExactly, transform } from "./translate";
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
