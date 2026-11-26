#!/usr/bin/env bun
import * as fs from "node:fs";
import * as path from "node:path";
import { createHash } from "node:crypto";

const expectedFile = process.env.EXPECTED_NATIVE_FILE;
const expectedVariant = process.env.EXPECTED_NATIVE_VARIANT;
const expectedVersion = process.env.EXPECTED_NATIVE_VERSION ?? "19.2.1";
if (!expectedFile || !expectedVariant) {
  throw new Error("EXPECTED_NATIVE_FILE and EXPECTED_NATIVE_VARIANT must be set");
}

const nativeDir = path.resolve("packages/natives/native");
const present = fs.readdirSync(nativeDir).filter(name => name.endsWith(".node"));
if (present.length !== 1 || present[0] !== expectedFile) {
  throw new Error("Physical isolation failed: expected only " + expectedFile + "; found " + present.join(", "));
}

const expectedHash = process.env.EXPECTED_NATIVE_SHA256;
const expectedPath = path.resolve(nativeDir, expectedFile);
const before = createHash("sha256").update(fs.readFileSync(expectedPath)).digest("hex");
if (!expectedHash || before !== expectedHash) throw new Error("Pre-load native SHA256 mismatch");
await import("../../packages/natives/native/index.js");
const { nativeAddonStatus } = await import("../../packages/natives/native/loader-state.js");
const status = nativeAddonStatus();
if (!status) throw new Error("The native loader did not report a loaded addon");
if (path.resolve(status.path) !== expectedPath) {
  throw new Error("Loader selected " + status.path + "; expected exactly " + expectedPath);
}
if (status.version !== expectedVersion || status.packageVersion !== expectedVersion || status.stale) {
  throw new Error("Loaded addon release mismatch: " + JSON.stringify(status));
}
if (
  (expectedVariant === "modern" && !expectedFile.endsWith("-modern.node")) ||
  (expectedVariant === "baseline" && !expectedFile.endsWith("-baseline.node"))
) {
  throw new Error("Filename " + expectedFile + " does not match expected variant " + expectedVariant);
}

const sha256 = createHash("sha256").update(fs.readFileSync(status.path)).digest("hex");
if (!expectedHash || sha256 !== expectedHash) throw new Error("Loaded addon SHA256 mismatch");
console.log(JSON.stringify({ ...status, expectedVariant, sha256 }, null, 2));
