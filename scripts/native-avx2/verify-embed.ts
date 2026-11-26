#!/usr/bin/env bun
import * as fs from "node:fs/promises";
import * as path from "node:path";
import { pathToFileURL } from "node:url";

const [nativeDirArg] = process.argv.slice(2);
const sourceDir = process.env.OMP_SOURCE_DIR;
if (!nativeDirArg || !sourceDir) {
  throw new Error("Usage: OMP_SOURCE_DIR=<v19.2.1 checkout> bun verify-embed.ts <native-dir>");
}

const nativeDir = path.resolve(nativeDirArg);
const addonFiles = (await fs.readdir(nativeDir)).filter(name => name.endsWith(".node"));
if (addonFiles.length !== 1 || addonFiles[0] !== "pi_natives.win32-x64-modern.node") {
  throw new Error("Expected exactly one modern addon in " + nativeDir + "; found: " + (addonFiles.join(", ") || "(none)"));
}

const moduleUrl = pathToFileURL(
  path.join(sourceDir, "packages/natives/scripts/embed-native.ts"),
).href;
const { embeddedAddonFiles } = await import(moduleUrl);
const overrides = await embeddedAddonFiles({
  platform: "win32",
  arch: "x64",
  nativeDir,
  version: "19.2.1",
});

const zstdFiles = Object.keys(overrides).filter(name => name.endsWith(".node.zst"));
if (
  zstdFiles.length !== 1 ||
  !zstdFiles[0].endsWith("pi_natives.win32-x64-modern.node.zst")
) {
  throw new Error("Single-modern embedding check failed: " + zstdFiles.join(", "));
}
const manifest = Object.entries(overrides).find(([name]) => name.endsWith("embedded-addon.js"))?.[1];
if (typeof manifest !== "string" || !manifest.includes('variant: "modern"') || manifest.includes('variant: "baseline"')) {
  throw new Error("Generated embedded-addon.js does not describe a modern-only addon");
}

console.log(JSON.stringify({ nativeDir, addonFiles, zstdFiles, singleModernEmbedding: true }, null, 2));
