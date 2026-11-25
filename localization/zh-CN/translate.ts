import { parse } from "@babel/parser";
import { readFile, writeFile, readdir } from "node:fs/promises";
import { join, relative } from "node:path";

type Node = { type: string; start: number; end: number; [key: string]: any };
type Dictionary = Readonly<Record<string, string>>;
type Edit = { start: number; end: number; text: string };
const attributes = new Set(["label", "title", "description", "placeholder", "text", "aria-label"]);
const headings: Dictionary = { USAGE: "用法", COMMANDS: "命令", ARGUMENTS: "参数", FLAGS: "选项", EXAMPLES: "示例" };

// Unknown text or changed technical tokens fall back to English.
const tokens = (s: string) =>
	(s.match(/https?:\/\/[^\s"'<>]+|--[a-z][a-z0-9-]*|\{\{[^}]+\}\}/g) ?? []).sort().join("\n");
export function lookup(dict: Dictionary, value: string): string {
	const result = Object.hasOwn(dict, value) ? dict[value] : undefined;
	return typeof result === "string" && result.trim() && tokens(value) === tokens(result) ? result : value;
}
function ast(source: string, file: string) {
	return parse(source, { sourceType: "module", plugins: file.endsWith(".tsx") ? ["typescript", "jsx"] : ["typescript"] });
}
function visit(node: Node, parent: Node | undefined, fn: (node: Node, parent?: Node) => void) {
	fn(node, parent);
	for (const value of Object.values(node)) {
		if (Array.isArray(value)) {
			for (const item of value) if (item?.type) visit(item, node, fn);
		} else if (value && typeof value === "object" && "type" in value) {
			visit(value as Node, node, fn);
		}
	}
}
const prop = (node: Node | undefined) => node?.type === "Identifier" ? node.name : node?.type === "StringLiteral" ? node.value : undefined;
function applyEdits(source: string, edits: Edit[]) {
	let previous = source.length;
	for (const edit of edits.sort((a, b) => b.start - a.start)) {
		if (edit.end > previous) throw new Error("Overlapping localization edits");
		source = source.slice(0, edit.start) + edit.text + source.slice(edit.end);
		previous = edit.start;
	}
	return source;
}
export function transform(source: string, file: string, dict: Dictionary) {
	const edits: Edit[] = [];
	const jsx = file.endsWith(".tsx");
	const command = /\/(?:commands\/[^/]+|slash-commands\/builtin-[^/]+)\.ts$/.test(file);
	const cli = file === "packages/utils/src/cli.ts";
	const settingsPages = file === "packages/coding-agent/src/ui/overlays/settings/pages.ts";
	function literal(node: Node, table: Dictionary = dict) {
		const translated = lookup(table, node.value);
		if (translated !== node.value) edits.push({ start: node.start, end: node.end, text: JSON.stringify(translated) });
	}
	function expression(node?: Node) {
		if (!node) return;
		if (node.type === "StringLiteral") literal(node);
		else if (node.type === "ConditionalExpression") {
			expression(node.consequent); expression(node.alternate);
		} else if (node.type === "ArrowFunctionExpression" && node.body?.type !== "BlockStatement") expression(node.body);
	}
	visit(ast(source, file) as unknown as Node, undefined, (node, parent) => {
		if (jsx && node.type === "JSXText") {
			const original = source.slice(node.start, node.end);
			const trimmed = original.trim();
			const translated = lookup(dict, trimmed);
			if (trimmed && translated !== trimmed) {
				const start = node.start + original.indexOf(trimmed);
				edits.push({ start, end: start + trimmed.length, text: "{" + JSON.stringify(translated) + "}" });
			}
		}
		if (jsx && node.type === "JSXAttribute" && attributes.has(node.name?.name)) {
			if (node.value?.type === "StringLiteral") {
				const translated = lookup(dict, node.value.value);
				if (translated !== node.value.value) edits.push({ start: node.value.start, end: node.value.end, text: "{" + JSON.stringify(translated) + "}" });
			} else if (node.value?.type === "JSXExpressionContainer") expression(node.value.expression);
		}
		if (jsx && node.type === "JSXExpressionContainer" && parent?.type === "JSXElement") expression(node.expression);
		if (settingsPages && node.type === "ObjectProperty" && !node.computed &&
			["label", "lead"].includes(prop(node.key) ?? "") && node.value?.type === "StringLiteral") literal(node.value);
		if (command && (node.type === "ObjectProperty" || node.type === "ClassProperty") &&
			!node.computed && prop(node.key) === "description" && node.value?.type === "StringLiteral") literal(node.value);
		if (cli && node.type === "CallExpression" && node.callee?.type === "MemberExpression" &&
			node.callee.object?.name === "lines" && node.callee.property?.name === "push" && !node.callee.computed) {
			for (const arg of node.arguments) if (arg.type === "StringLiteral") literal(arg, headings);
		}
	});
	const result = applyEdits(source, edits);
	if (edits.length) ast(result, file);
	return { source: result, count: edits.length };
}
export function replaceExactly(source: string, from: string, to: string, file: string) {
	if (source.split(from).length !== 2) throw new Error("Required anchor changed: " + file + " :: " + from);
	return source.replace(from, () => to);
}
async function walk(root: string): Promise<string[]> {
	const out: string[] = [];
	for (const entry of await readdir(root, { withFileTypes: true })) {
		const file = join(root, entry.name);
		if (entry.isDirectory()) out.push(...await walk(file));
		else if (entry.isFile()) out.push(file);
	}
	return out;
}
async function main() {
	const root = process.cwd();
	const dict: Dictionary = {
		...JSON.parse(await readFile(join(import.meta.dir, "dict-extra.json"), "utf8")),
		...JSON.parse(await readFile(join(import.meta.dir, "dict.json"), "utf8")),
		...JSON.parse(await readFile(join(import.meta.dir, "overrides.json"), "utf8")),
	};
	const paths = [
		...await walk(join(root, "packages/coding-agent/src/ui")),
		...await walk(join(root, "packages/coding-agent/src/commands")),
		...await walk(join(root, "packages/coding-agent/src/slash-commands")),
		...await walk(join(root, "packages/stats/src/client")),
		join(root, "packages/utils/src/cli.ts"),
	];
	let count = 0; let modified = 0;
	for (const file of paths) {
		const name = relative(root, file).replaceAll("\\", "/");
		if (!name.endsWith(".tsx") && !/\/(?:commands\/[^/]+|slash-commands\/builtin-[^/]+)\.ts$/.test(name) && name !== "packages/utils/src/cli.ts" && name !== "packages/coding-agent/src/ui/overlays/settings/pages.ts") continue;
		const source = await readFile(file, "utf8");
		const result = transform(source, name, dict);
		if (result.count) { await writeFile(file, result.source); count += result.count; modified++; }
	}
	// Translate only the settings display projection, not persisted values.
	const settings = join(root, "packages/coding-agent/src/config/settings-defs.ts");
	let src = await readFile(settings, "utf8");
	const entries = Object.entries(dict).filter(([k, v]) => k !== v && lookup(dict, k) === v);
	await writeFile(join(root, "packages/coding-agent/src/config/zh-display.generated.ts"),
		"// Generated at build time.\nconst entries = new Map<string, string>(" + JSON.stringify(entries) + ");\n" +
		"export function zhDisplay(value: string): string { return entries.get(value) ?? value; }\n");
	src = 'import { zhDisplay } from "./zh-display.generated";\n' + src;
	for (const field of ["label", "description"]) src = replaceExactly(src, field + ": ui." + field + ",", field + ": zhDisplay(ui." + field + "),", settings);
	src = replaceExactly(src, "warning: ui.warning,", "warning: ui.warning === undefined ? undefined : zhDisplay(ui.warning),", settings);
	src = replaceExactly(src, "return ui.options;", "return ui.options.map(option => ({ ...option, label: zhDisplay(option.label), description: option.description === undefined ? undefined : zhDisplay(option.description) }));", settings);
	ast(src, settings); await writeFile(settings, src);
	// Prevent the official updater from replacing the localized binary.
	const update = join(root, "packages/coding-agent/src/cli/update-cli.ts");
	src = await readFile(update, "utf8");
	const reason = "汉化版请从 https://github.com/WhiCCX0/oh-my-pi/releases 更新。";
	src = replaceExactly(src, "export async function canSelfUpdate(): Promise<boolean> {", "export async function canSelfUpdate(): Promise<boolean> {\n\tif (process.env.PI_COMPILED === \"true\") return false;", update);
	src = replaceExactly(src, "export async function runUpdateCommand(opts: UpdateOptions): Promise<UpdateOutcome> {",
		"export async function runUpdateCommand(opts: UpdateOptions): Promise<UpdateOutcome> {\n\tif (process.env.PI_COMPILED === \"true\") { const reason = " + JSON.stringify(reason) + "; console.log(reason); return { kind: \"unsupported\", reason }; }", update);
	ast(src, update); await writeFile(update, src);
	if (count < 50) throw new Error("Localization coverage unexpectedly low: " + count);
	console.log(JSON.stringify({ translatedLiterals: count, modifiedFiles: modified, dictionaryEntries: Object.keys(dict).length }));
}
if (import.meta.main) await main();
