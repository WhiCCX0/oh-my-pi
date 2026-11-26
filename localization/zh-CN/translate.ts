import { parse } from "@babel/parser";
import { readFile, writeFile, readdir } from "node:fs/promises";
import { dirname, join, relative, resolve } from "node:path";

type Node = { type: string; start: number; end: number; [key: string]: any };
type Dictionary = Readonly<Record<string, string>>;
type Edit = { start: number; end: number; text: string };
const attributes = new Set(["label", "title", "description", "placeholder", "text", "aria-label"]);
const headings: Dictionary = { USAGE: "用法", COMMANDS: "命令", ARGUMENTS: "参数", FLAGS: "选项", EXAMPLES: "示例" };

// Unknown text or changed technical tokens fall back to English.
const URL_PATTERN = /https?:\/\/[^\s"'`<>。，；：！？、）】》」』]*/giu;
const URL_TRAILING_PUNCTUATION = /[.,;:!?。，；：！？、）】》」』]+$/u;
const TOKEN_PATTERNS: ReadonlyArray<readonly [string, RegExp]> = [
	["argument", /<[A-Za-z][A-Za-z0-9_.|:-]*(?:\.\.\.)?>/g],
	["flag", /(?<![\w-])--[A-Za-z][A-Za-z0-9-]*/g],
	["printf", /%(?:\d+\$)?[-+#0]*(?:\d+|\*)?(?:\.(?:\d+|\*))?(?:hh|h|ll|l|z|t|j|L)?[diuoxXfFeEgGaAcspnOj%]/g],
	["placeholder", /\{\{[^{}]+\}\}|\$\{[^{}]+\}|\{(?:[0-9]+|[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\}/g],
];
function countCharacter(value: string, character: string): number {
	return value.split(character).length - 1;
}
function normalizeUrl(value: string): string {
	let result = value.replace(URL_TRAILING_PUNCTUATION, "");
	let changed = true;
	while (changed) {
		const stripped = result.replace(URL_TRAILING_PUNCTUATION, "");
		changed = stripped !== result;
		result = stripped;
		for (const [open, close] of [["(", ")"], ["[", "]"], ["{", "}"]] as const) {
			if (result.endsWith(close) && countCharacter(result, close) > countCharacter(result, open)) {
				result = result.slice(0, -1);
				changed = true;
			}
		}
	}
	return result;
}
// Compare technical tokens as a multiset while ignoring sentence punctuation around URLs.
export function tokens(value: string): string {
	const found: string[] = [];
	const orderedPrintf: string[] = [];
	const withoutUrls = value.replace(URL_PATTERN, url => {
		found.push("url:" + normalizeUrl(url));
		return " ".repeat(url.length);
	});
	for (const [kind, pattern] of TOKEN_PATTERNS) {
		for (const match of withoutUrls.matchAll(pattern)) {
			if (kind === "printf") orderedPrintf.push(match[0]);
			else found.push(kind + ":" + match[0]);
		}
	}
	return [...found.sort(), ...orderedPrintf.map(value => "printf:" + value)].join("\n");
}
export function lookup(dict: Dictionary, value: string): string {
	const result = Object.hasOwn(dict, value) ? dict[value] : undefined;
	return typeof result === "string" && result.trim() && tokens(value) === tokens(result) ? result : value;
}
function ast(source: string, file: string) {
	try {
		return parse(source, { sourceType: "module", plugins: file.endsWith(".tsx") ? ["typescript", "jsx"] : ["typescript"] });
	} catch (error) {
		const detail = error instanceof Error ? error.message : String(error);
		throw new Error("Unable to parse localization target " + file + ": " + detail, { cause: error });
	}
}
function visit(node: Node, parent: Node | undefined, fn: (node: Node, parent?: Node) => boolean | void) {
	if (fn(node, parent) === false) return;
	for (const value of Object.values(node)) {
		if (Array.isArray(value)) {
			for (const item of value) if (item?.type) visit(item, node, fn);
		} else if (value && typeof value === "object" && "type" in value) {
			visit(value as Node, node, fn);
		}
	}
}
const prop = (node: Node | undefined) => node?.type === "Identifier" ? node.name : node?.type === "StringLiteral" ? node.value : undefined;
export function applyEdits(source: string, edits: Edit[], file = "<source>") {
	const unique = new Map<string, Edit>();
	for (const edit of edits) {
		if (edit.start < 0 || edit.end < edit.start || edit.end > source.length) {
			throw new Error("Invalid localization edit range in " + file + ": " + edit.start + "-" + edit.end);
		}
		const key = String(edit.start) + ":" + String(edit.end);
		const existing = unique.get(key);
		if (existing) {
			if (existing.text !== edit.text) throw new Error("Conflicting localization edits in " + file + " at " + key);
			continue;
		}
		unique.set(key, edit);
	}
	const ordered = [...unique.values()].sort((a, b) => b.start - a.start || b.end - a.end);
	let previousStart = source.length;
	for (const edit of ordered) {
		if (edit.end > previousStart) {
			throw new Error("Overlapping localization edits in " + file + " at " + edit.start + "-" + edit.end);
		}
		source = source.slice(0, edit.start) + edit.text + source.slice(edit.end);
		previousStart = edit.start;
	}
	return source;
}
export function transform(source: string, file: string, dict: Dictionary) {
	const edits: Edit[] = [];
	const jsx = file.endsWith(".tsx");
	const commandHelp = file === "packages/coding-agent/src/cli/command-help.ts";
	const command = commandHelp || /\/(?:commands\/[^/]+|slash-commands\/builtin-[^/]+)\.ts$/.test(file);
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
			const decoded = String(node.value);
			const display = decoded.trim();
			// Babel decodes HTML entities. Keep raw surrounding whitespace, and
			// skip ambiguous whitespace entities at the boundaries.
			const edges = (value: string) => [value.slice(0, value.length - value.trimStart().length), value.slice(value.trimEnd().length)];
			const translated = lookup(dict, display);
			if (trimmed && display && translated !== display && JSON.stringify(edges(original)) === JSON.stringify(edges(decoded))) {
				const start = node.start + original.indexOf(trimmed);
				edits.push({ start, end: start + trimmed.length, text: "{" + JSON.stringify(translated) + "}" });
			}
		}
		if (jsx && node.type === "JSXAttribute" && attributes.has(node.name?.name)) {
			if (node.value?.type === "StringLiteral") {
				const translated = lookup(dict, node.value.value);
				if (translated !== node.value.value) {
					// JS string expressions safely encode quotes, backslashes and newlines.
					edits.push({ start: node.value.start, end: node.value.end, text: "{" + JSON.stringify(translated) + "}" });
				}
			} else if (node.value?.type === "JSXExpressionContainer") expression(node.value.expression);
		}
		if (jsx && node.type === "JSXExpressionContainer" && (parent?.type === "JSXElement" || parent?.type === "JSXFragment")) {
			expression(node.expression);
		}
		if (settingsPages && node.type === "ObjectProperty" && !node.computed &&
			["label", "lead"].includes(prop(node.key) ?? "") && node.value?.type === "StringLiteral") {
			const before = edits.length;
			literal(node.value);
			if (edits.length > before) return false;
		}
		if (command && (node.type === "ObjectProperty" || node.type === "ClassProperty") &&
			!node.computed && prop(node.key) === "description" && node.value?.type === "StringLiteral") {
			const before = edits.length;
			literal(node.value);
			if (edits.length > before) return false;
		}
		if (cli && node.type === "CallExpression" && node.callee?.type === "MemberExpression" &&
			node.callee.object?.name === "lines" && node.callee.property?.name === "push" && !node.callee.computed) {
			const before = edits.length;
			for (const arg of node.arguments) if (arg.type === "StringLiteral") literal(arg, headings);
			if (edits.length > before) return false;
		}
	});
	const result = applyEdits(source, edits, file);
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
// No application module is executed on the cross-compilation host. The
// settings registry imports native-dependent modules even during initialization.
export function staticDisplayCandidates(source: string, file: string): Set<string> {
	const values = new Set<string>();
	function value(node: Node | undefined): string | undefined {
		if (!node) return;
		if (node.type === "StringLiteral") return node.value;
		if (node.type === "TSAsExpression" || node.type === "TSSatisfiesExpression" || node.type === "TSNonNullExpression" || node.type === "ParenthesizedExpression") return value(node.expression);
		if (node.type === "BinaryExpression" && node.operator === "+") {
			const left = value(node.left), right = value(node.right);
			if (left !== undefined && right !== undefined) return left + right;
		}
		if (node.type === "TemplateLiteral") {
			const parts = node.expressions.map((part: Node) => value(part));
			if (parts.every((part: string | undefined) => part !== undefined) && node.quasis.every((part: Node) => part.value?.cooked != null)) {
				return node.quasis.map((part: Node, index: number) => part.value.cooked + (parts[index] ?? "")).join("");
			}
		}
	}
	visit(ast(source, file) as unknown as Node, undefined, node => {
		const candidate = value(node);
		if (candidate !== undefined) values.add(candidate);
	});
	return values;
}

async function settingsDictionaryCandidates(root: string): Promise<Set<string>> {
	const src = join(root, "packages/coding-agent/src");
	const registry = join(src, "config/all-settings.ts");
	const source = await readFile(registry, "utf8");
	const files = new Set<string>([registry]);
	// Discover the real domain list from the same module used by settings-ui.
	for (const item of ast(source, registry).program.body) {
		if (item.type !== "ImportDeclaration" || !item.source.value.startsWith(".")) continue;
		const target = resolve(dirname(registry), item.source.value.replace(/\.js$/, ""));
		const candidates = [target.endsWith(".ts") || target.endsWith(".tsx") ? target : target + ".ts", target + ".tsx", join(target, "index.ts")];
		let found = false;
		for (const candidate of candidates) {
			try { await readFile(candidate, "utf8"); files.add(candidate); found = true; break; }
			catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
		}
		if (!found) throw new Error("Unable to resolve settings domain " + item.source.value);
	}
	// Include helper settings modules, including platform-conditional literals.
	for (const file of await walk(src)) {
		if (/(?:^|[/\\])(?:[^/\\]*settings[^/\\]*|registry)\.ts$/.test(file)) files.add(file);
	}
	const values = new Set<string>();
	for (const file of [...files].sort()) {
		for (const candidate of staticDisplayCandidates(await readFile(file, "utf8"), file)) values.add(candidate);
	}
	return values;
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
		join(root, "packages/coding-agent/src/cli/command-help.ts"),
	];
	let count = 0; let modified = 0;
	for (const file of paths) {
		const name = relative(root, file).replaceAll("\\", "/");
		if (!name.endsWith(".tsx") && !/\/(?:commands\/[^/]+|slash-commands\/builtin-[^/]+)\.ts$/.test(name) && name !== "packages/utils/src/cli.ts" && name !== "packages/coding-agent/src/cli/command-help.ts" && name !== "packages/coding-agent/src/ui/overlays/settings/pages.ts") continue;
		const source = await readFile(file, "utf8");
		const result = transform(source, name, dict);
		if (result.count) { await writeFile(file, result.source); count += result.count; modified++; }
	}
	// Translate only the settings display projection, not persisted values.
	const settings = join(root, "packages/coding-agent/src/config/settings-defs.ts");
	let src = await readFile(settings, "utf8");
	const validEntries = Object.entries(dict).filter(([k, v]) => k !== v && lookup(dict, k) === v);
	// Conservatively extract candidates from the actual registry domain sources;
	// the Windows smoke test proves every actual registered display value.
	const values = await settingsDictionaryCandidates(root);
	const entries = validEntries.filter(([key]) => values.has(key));
	if (entries.length < 100 || entries.length >= validEntries.length) throw new Error("Unexpected settings dictionary coverage: " + entries.length);
	// An external CI-only proof checks every real Windows registry value against
	// the full dictionary without embedding the full dictionary in the EXE.
	const proof = process.env.OMP_ZH_SMOKE_DICTIONARY_OUT;
	if (proof) await writeFile(proof, JSON.stringify(Object.fromEntries(validEntries)));
	console.log(JSON.stringify({ runtimeDictionaryEntries: entries.length, fullDictionaryEntries: validEntries.length, runtimeDictionaryBytes: Buffer.byteLength(JSON.stringify(entries)), fullDictionaryBytes: Buffer.byteLength(JSON.stringify(validEntries)) }));
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
	// Compiled localized builds must not advertise the disabled official updater.
	const startup = join(root, "packages/coding-agent/src/main.ts");
	src = await readFile(startup, "utf8");
	src = replaceExactly(src, "async function checkForNewVersion(currentVersion: string): Promise<string | undefined> {",
		"async function checkForNewVersion(currentVersion: string): Promise<string | undefined> {\n\tif (process.env.PI_COMPILED === \"true\") return;", startup);
	ast(src, startup); await writeFile(startup, src);
	// Extend the existing compiled distribution smoke path; no new public command.
	const cliSource = join(root, "packages/coding-agent/src/cli.ts");
	src = await readFile(cliSource, "utf8");
	const smokeAnchor = '\tprocess.stdout.write("smoke-test: ok\\n");';
	const smokeChecks = [
		'\tconst { getTemplate } = await import("./export/html/index");',
		'\tconst template = getTemplate();',
		'\tif (!template.includes(\'window.addEventListener("DOMContentLoaded",function(){\') || template.includes(\'src="https://build.stencil.so/web/omp/\')) throw new Error("Localized inline viewer missing");',
		'\tconst { getAllSettingDefs } = await import("./config/settings-defs");',
		'\tconst defs = getAllSettingDefs([{ path: "audit.setting", type: "enum", defaultValue: "yolo", enumValues: ["yolo"], ui: { tab: "interaction", label: "Yolo", description: "Yolo", options: [{ value: "yolo", label: "Yolo" }] } }]);',
		'\tconst def = defs[0];',
		'\tif (!def || def.path !== "audit.setting" || def.defaultValue !== "yolo" || def.label !== "Yolo（全自动）" || def.description !== "Yolo（全自动）" || def.type !== "submenu" || def.options[0]?.value !== "yolo" || def.options[0]?.label !== "Yolo（全自动）") throw new Error("Localized settings projection failed");',
		'\tconst dictionaryPath = process.env.OMP_ZH_SMOKE_DICTIONARY;',
		'\tif (dictionaryPath) {',
		'\t\tconst full = await Bun.file(dictionaryPath).json();',
		'\t\tconst { orderedSettings } = await import("./config/all-settings");',
		'\t\tconst { zhDisplay } = await import("./config/zh-display.generated");',
		'\t\tlet matched = 0; const missing = new Set<string>();',
		'\t\tconst check = (value: unknown) => { if (typeof value !== "string") return; const expected = Object.hasOwn(full, value) ? full[value] : value; if (zhDisplay(value) !== expected) missing.add(value); if (expected !== value) matched++; };',
		'\t\tfor (const { ui } of orderedSettings()) { if (!ui) continue; check(ui.label); check(ui.description); check(ui.warning); if (Array.isArray(ui.options)) for (const option of ui.options) { check(option.label); check(option.description); } }',
		'\t\tif (missing.size) throw new Error("Settings dictionary mismatches (" + missing.size + "): " + JSON.stringify([...missing].slice(0, 50)));',
		'\t\tif (matched < 100) throw new Error("Insufficient runtime settings dictionary verification: " + matched);',
		'\t\tprocess.stdout.write("settings-dictionary-smoke: " + matched + " matches\\n");',
		'\t}',
		'\tprocess.stdout.write("localization-smoke: ok\\n");',
	].join("\n");
	src = replaceExactly(src, smokeAnchor, smokeChecks + "\n" + smokeAnchor, cliSource);
	ast(src, cliSource); await writeFile(cliSource, src);
	if (count < 50) throw new Error("Localization coverage unexpectedly low: " + count);
	console.log(JSON.stringify({ translatedLiterals: count, modifiedFiles: modified, dictionaryEntries: Object.keys(dict).length }));
}
if (import.meta.main) await main();
