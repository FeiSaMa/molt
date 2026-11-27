#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { lstatSync, readFileSync, realpathSync } from "node:fs";
import { isAbsolute, relative, resolve, sep } from "node:path";
import { pathToFileURL } from "node:url";

function isInside(root, candidate) {
  const path = relative(root, candidate);
  return path === "" || (path !== ".." && !path.startsWith(`..${sep}`) && !isAbsolute(path));
}

export function readPinnedProject(moltBin, configPath, requestedFiles) {
  if (typeof moltBin !== "string" || moltBin.length === 0) {
    throw new TypeError("moltBin must be a non-empty executable path");
  }

  const absoluteConfig = resolve(configPath);
  const config = JSON.parse(readFileSync(absoluteConfig, "utf8"));
  if (!Array.isArray(config.files) || !config.files.every((file) => typeof file === "string")) {
    throw new TypeError("project config must contain a files array of relative paths");
  }

  const files = requestedFiles ?? config.files;
  if (!Array.isArray(files) || files.length === 0) {
    throw new TypeError("at least one project file must be selected");
  }
  for (const file of files) {
    if (typeof file !== "string" || isAbsolute(file) || !config.files.includes(file)) {
      throw new Error(`file is not listed in project config: ${file}`);
    }
  }

  const result = spawnSync(
    moltBin,
    ["project", "--config", absoluteConfig, "read-tree"],
    { encoding: "utf8", timeout: 15_000, maxBuffer: 1024 * 1024 },
  );
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(result.stderr.trim() || `molt exited with status ${result.status}`);
  }

  const pin = JSON.parse(result.stdout);
  if (typeof pin.snapshot !== "string" || typeof pin.tree !== "string") {
    throw new TypeError("molt project read-tree returned an invalid pin");
  }

  const tree = realpathSync(pin.tree);
  const contents = {};
  for (const file of files) {
    const target = resolve(tree, file);
    if (!isInside(tree, target)) throw new Error(`project file escapes pinned tree: ${file}`);

    const stat = lstatSync(target);
    const actual = realpathSync(target);
    if (!stat.isFile() || stat.isSymbolicLink() || !isInside(tree, actual)) {
      throw new Error(`project file is not a regular file in the pinned tree: ${file}`);
    }
    contents[file] = readFileSync(actual, "utf8");
  }

  return { snapshot: pin.snapshot, tree, files: contents };
}

function main() {
  const [moltBin, configPath, ...files] = process.argv.slice(2);
  if (!moltBin || !configPath) {
    console.error("Usage: node examples/project-reader.mjs /path/to/molt project.json [relative-file ...]");
    process.exitCode = 2;
    return;
  }

  try {
    const pin = readPinnedProject(moltBin, configPath, files.length ? files : undefined);
    process.stdout.write(`${JSON.stringify(pin, null, 2)}\n`);
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) main();
