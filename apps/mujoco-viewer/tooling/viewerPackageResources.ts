import {
  existsSync,
  readdirSync,
  readFileSync,
  realpathSync,
  statSync,
} from "node:fs";
import { isAbsolute, relative, resolve, sep } from "node:path";
import type { Plugin } from "vite";

import { decodeViewerRobotDeclaration } from "../src/robot-profiles/declaration.js";


export const VIEWER_PACKAGE_RESOURCE_BINDINGS_SCHEMA_VERSION =
  "viewer-package-resource-bindings/v1" as const;

const roles = new Set([
  "model_entrypoint",
  "model_include",
  "model_dependency",
  "viewer_declaration",
  "fixture",
  "configuration",
] as const);
const publicRoles: ReadonlySet<ViewerPackageResourceRole> = new Set(
  [...roles].filter((role) => role !== "configuration"),
);
const bundleRoles: ReadonlySet<ViewerPackageResourceRole> = new Set([
  "model_entrypoint",
  "model_include",
  "model_dependency",
]);

export type ViewerPackageResourceRole =
  | "model_entrypoint"
  | "model_include"
  | "model_dependency"
  | "viewer_declaration"
  | "fixture"
  | "configuration";

export interface ViewerPackageResourceBinding {
  readonly role: ViewerPackageResourceRole;
  readonly logicalIdentifier: string;
  readonly url: string | null;
  readonly package: string;
  readonly packageResourcePath: string;
  readonly bundlePath: string | null;
}

export interface ViewerPackageResourceManifest {
  readonly schemaVersion: typeof VIEWER_PACKAGE_RESOURCE_BINDINGS_SCHEMA_VERSION;
  readonly resources: readonly ViewerPackageResourceBinding[];
}

export interface ResolvedViewerPackageResource extends ViewerPackageResourceBinding {
  readonly sourcePath: string;
}

function requireRecord(value: unknown, name: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${name} must be an object`);
  }
  return value as Record<string, unknown>;
}

function requireExactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
  name: string,
): void {
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  if (actual.length !== wanted.length || actual.some((key, index) => key !== wanted[index])) {
    throw new Error(`${name} keys mismatch`);
  }
}

function requireString(value: unknown, name: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${name} must be a non-empty string`);
  }
  return value;
}

function requireRelativePosixPath(value: unknown, name: string): string {
  const path = requireString(value, name);
  const parts = path.split("/");
  if (path.startsWith("/") || path.includes("\\") || parts.some((part) => !part || part === "." || part === "..")) {
    throw new Error(`${name} must be a non-empty relative POSIX path`);
  }
  return path;
}

function requireLogicalIdentifier(value: unknown, name: string): string {
  const path = requireRelativePosixPath(value, name);
  const parts = path.split("/");
  if (!(["assets", "configs"].includes(parts[0] ?? "")) || parts.length < 3) {
    throw new Error(`${name} must use the assets/ or configs/ stable namespace`);
  }
  return path;
}

function publicUrlForLogicalIdentifier(logicalIdentifier: string): string {
  if (!logicalIdentifier.startsWith("assets/")) {
    throw new Error("public viewer resources must use the assets/ logical namespace");
  }
  if (logicalIdentifier.split("/").some((part) => !/^[A-Za-z0-9._~-]+$/.test(part))) {
    throw new Error("public viewer resource logical identifier contains an unsafe URL segment");
  }
  return `/${logicalIdentifier.slice("assets/".length)}`;
}

function requireNullablePath(value: unknown, name: string): string | null {
  return value === null ? null : requireRelativePosixPath(value, name);
}

function requireNullableUrl(value: unknown, name: string): string | null {
  if (value === null) {
    return null;
  }
  const url = requireString(value, name);
  const parts = url.split("/");
  if (!url.startsWith("/") || url.startsWith("//") || url.includes("\\") || parts.includes("..")) {
    throw new Error(`${name} must be a local absolute-path URL`);
  }
  return url;
}

function requireOne(
  manifest: ViewerPackageResourceManifest,
  role: ViewerPackageResourceRole,
): ViewerPackageResourceBinding {
  const matches = manifest.resources.filter((resource) => resource.role === role);
  if (matches.length !== 1) {
    throw new Error(`manifest requires exactly one ${role} resource, got ${matches.length}`);
  }
  return matches[0]!;
}

function requireUnique(values: readonly string[], name: string): void {
  if (new Set(values).size !== values.length) {
    throw new Error(`${name} must be unique`);
  }
}

export function decodeViewerPackageResourceManifest(
  value: unknown,
): ViewerPackageResourceManifest {
  const root = requireRecord(value, "viewer package resource manifest");
  requireExactKeys(root, ["schemaVersion", "resources"], "viewer package resource manifest");
  const schemaVersion = requireString(root.schemaVersion, "schemaVersion");
  if (schemaVersion !== VIEWER_PACKAGE_RESOURCE_BINDINGS_SCHEMA_VERSION) {
    throw new Error(`unsupported viewer package resource bindings schema version: ${schemaVersion}`);
  }
  if (!Array.isArray(root.resources) || root.resources.length === 0) {
    throw new Error("viewer package resource manifest requires resources");
  }

  const resources = root.resources.map((raw, index): ViewerPackageResourceBinding => {
    const name = `resources[${index}]`;
    const item = requireRecord(raw, name);
    requireExactKeys(
      item,
      ["role", "logicalIdentifier", "url", "package", "packageResourcePath", "bundlePath"],
      name,
    );
    const role = requireString(item.role, `${name}.role`);
    if (!roles.has(role as ViewerPackageResourceRole)) {
      throw new Error(`unsupported viewer package resource role: ${role}`);
    }
    const typedRole = role as ViewerPackageResourceRole;
    const logicalIdentifier = requireLogicalIdentifier(item.logicalIdentifier, `${name}.logicalIdentifier`);
    const url = requireNullableUrl(item.url, `${name}.url`);
    const bundlePath = requireNullablePath(item.bundlePath, `${name}.bundlePath`);
    if (publicRoles.has(typedRole)) {
      const expectedUrl = publicUrlForLogicalIdentifier(logicalIdentifier);
      if (url !== expectedUrl) {
        throw new Error(`${name}.url mismatch: expected ${expectedUrl}, got ${url}`);
      }
    } else if (url !== null) {
      throw new Error(`${typedRole} resource URL must be null`);
    }
    if (bundleRoles.has(typedRole) !== (bundlePath !== null)) {
      throw new Error(`${typedRole} resource has invalid bundlePath ownership`);
    }
    const packageName = requireString(item.package, `${name}.package`);
    if (!/^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$/.test(packageName)) {
      throw new Error(`${name}.package must be an importable package name`);
    }
    return Object.freeze({
      role: typedRole,
      logicalIdentifier,
      url,
      package: packageName,
      packageResourcePath: requireRelativePosixPath(
        item.packageResourcePath,
        `${name}.packageResourcePath`,
      ),
      bundlePath,
    });
  });

  requireUnique(resources.map((item) => item.logicalIdentifier), "logical identifiers");
  requireUnique(resources.flatMap((item) => item.url === null ? [] : [item.url]), "public URLs");
  requireUnique(resources.flatMap((item) => item.bundlePath === null ? [] : [item.bundlePath]), "bundle paths");
  const manifest = Object.freeze({
    schemaVersion: VIEWER_PACKAGE_RESOURCE_BINDINGS_SCHEMA_VERSION,
    resources: Object.freeze(resources),
  });
  requireOne(manifest, "model_entrypoint");
  requireOne(manifest, "viewer_declaration");
  requireOne(manifest, "fixture");
  return manifest;
}

function parseStringArray(section: string, key: string): string[] {
  const escaped = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = section.match(new RegExp(`(?:^|\\n)\\s*${escaped}\\s*=\\s*\\[([\\s\\S]*?)\\]`));
  if (match === null) {
    throw new Error(`missing TOML string array ${key}`);
  }
  return Array.from(match[1]!.matchAll(/["']([^"']+)["']/g), (item) => item[1]!);
}

function tomlSection(document: string, name: string): string {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = document.match(new RegExp(`(?:^|\\n)\\[${escaped}\\]\\s*\\n([\\s\\S]*?)(?=\\n\\[|$)`));
  if (match === null) {
    throw new Error(`missing TOML section [${name}]`);
  }
  return match[1]!;
}

function workspaceSourceRoots(repoRoot: string): string[] {
  const rootPyproject = readFileSync(resolve(repoRoot, "pyproject.toml"), "utf8");
  const members = parseStringArray(tomlSection(rootPyproject, "tool.uv.workspace"), "members");
  return [repoRoot, ...members.map((member) => resolve(repoRoot, member))].flatMap((projectRoot) => {
    const pyprojectPath = resolve(projectRoot, "pyproject.toml");
    if (!existsSync(pyprojectPath)) {
      throw new Error(`workspace project is missing pyproject.toml: ${projectRoot}`);
    }
    const pyproject = readFileSync(pyprojectPath, "utf8");
    const sourceDirectories = parseStringArray(
      tomlSection(pyproject, "tool.setuptools.packages.find"),
      "where",
    );
    return sourceDirectories.map((directory) => resolve(projectRoot, directory));
  });
}

function discoverManifestPaths(sourceRoot: string): string[] {
  const results: string[] = [];
  const visit = (directory: string): void => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      if (entry.name === "__pycache__" || entry.name.startsWith(".")) {
        continue;
      }
      const path = resolve(directory, entry.name);
      if (entry.isDirectory()) {
        visit(path);
      } else if (entry.isFile() && entry.name === "viewer-resource-bindings.json") {
        results.push(path);
      }
    }
  };
  visit(sourceRoot);
  return results;
}

function isWithin(root: string, candidate: string): boolean {
  const child = relative(root, candidate);
  return child === "" || (!child.startsWith(`..${sep}`) && child !== ".." && !isAbsolute(child));
}

function resolvePackageResource(
  sourceRoots: readonly string[],
  binding: ViewerPackageResourceBinding,
): string {
  const packageParts = binding.package.split(".");
  const packageRoots = sourceRoots
    .map((sourceRoot) => resolve(sourceRoot, ...packageParts))
    .filter((path) => existsSync(resolve(path, "__init__.py")));
  if (packageRoots.length !== 1) {
    throw new Error(`expected one workspace source for package ${binding.package}, got ${packageRoots.length}`);
  }
  const packageRoot = realpathSync(packageRoots[0]!);
  const candidate = resolve(packageRoot, ...binding.packageResourcePath.split("/"));
  if (!existsSync(candidate) || !statSync(candidate).isFile()) {
    throw new Error(`missing package resource ${binding.package}:${binding.packageResourcePath}`);
  }
  const resolvedCandidate = realpathSync(candidate);
  if (!isWithin(packageRoot, resolvedCandidate)) {
    throw new Error(`package resource escapes its owner: ${binding.package}:${binding.packageResourcePath}`);
  }
  return resolvedCandidate;
}

export function validateViewerProfileResourceBindings(
  manifest: ViewerPackageResourceManifest,
  resolvedResources: readonly ResolvedViewerPackageResource[],
): void {
  const viewerBinding = requireOne(manifest, "viewer_declaration");
  const viewerResource = resolvedResources.find(
    (resource) => resource.logicalIdentifier === viewerBinding.logicalIdentifier,
  );
  if (viewerResource === undefined) {
    throw new Error("resolved resources are missing the viewer declaration");
  }
  const profile = decodeViewerRobotDeclaration(
    JSON.parse(readFileSync(viewerResource.sourcePath, "utf8")),
  );
  const model = requireOne(manifest, "model_entrypoint");
  const fixture = requireOne(manifest, "fixture");
  if (profile.modelResourcePath !== model.logicalIdentifier || profile.modelUrl !== model.url) {
    throw new Error("viewer model resource does not match its package manifest");
  }
  if (profile.fixtureResourcePath !== fixture.logicalIdentifier || profile.fixtureUrl !== fixture.url) {
    throw new Error("viewer fixture resource does not match its package manifest");
  }
  const manifestVfs = new Set(
    manifest.resources
      .filter((resource) => resource.role === "model_include" || resource.role === "model_dependency")
      .map((resource) => JSON.stringify([resource.bundlePath, resource.logicalIdentifier, resource.url])),
  );
  const profileVfs = new Set(
    Array.from(profile.vfsAssets, ([vfsPath, url]) =>
      JSON.stringify([vfsPath, profile.vfsResourcePaths.get(vfsPath), url]),
    ),
  );
  if (manifestVfs.size !== profileVfs.size || [...manifestVfs].some((item) => !profileVfs.has(item))) {
    throw new Error("viewer VFS resources do not exactly match their package manifest");
  }
}

export function loadViewerPackageResources(repoRoot: string): {
  readonly manifests: readonly ViewerPackageResourceManifest[];
  readonly resources: readonly ResolvedViewerPackageResource[];
} {
  const sourceRoots = workspaceSourceRoots(repoRoot);
  const manifestPaths = [
    ...new Set(sourceRoots.flatMap(discoverManifestPaths).map((path) => realpathSync(path))),
  ];
  if (manifestPaths.length === 0) {
    throw new Error("no viewer package resource manifests found in workspace sources");
  }
  const manifests = manifestPaths.map((path) =>
    decodeViewerPackageResourceManifest(JSON.parse(readFileSync(path, "utf8"))),
  );
  const resources = manifests.flatMap((manifest) => {
    const resolved = manifest.resources.map((binding) => Object.freeze({
      ...binding,
      sourcePath: resolvePackageResource(sourceRoots, binding),
    }));
    validateViewerProfileResourceBindings(manifest, resolved);
    return resolved;
  });
  requireUnique(resources.map((resource) => resource.logicalIdentifier), "workspace logical identifiers");
  requireUnique(resources.flatMap((resource) => resource.url === null ? [] : [resource.url]), "workspace public URLs");
  return Object.freeze({ manifests: Object.freeze(manifests), resources: Object.freeze(resources) });
}

export function loadDynamicViewerResourcesFromEnvironment(
  environment: NodeJS.ProcessEnv = process.env,
): ReadonlyMap<string, string> {
  const configured = environment.XPOTATO_SIM_DYNAMIC_VIEWER_RESOURCE_ROOT;
  if (configured === undefined) {
    return new Map();
  }
  if (!isAbsolute(configured)) {
    throw new Error("dynamic viewer resource root must be absolute");
  }
  const root = realpathSync(configured);
  if (!statSync(root).isDirectory()) {
    throw new Error("dynamic viewer resource root must be a directory");
  }
  const resources = new Map<string, string>();
  const visit = (directory: string): void => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      if (entry.isSymbolicLink()) {
        throw new Error("dynamic viewer resources must not contain symlinks");
      }
      const path = resolve(directory, entry.name);
      if (entry.isDirectory()) {
        visit(path);
        continue;
      }
      if (!entry.isFile()) {
        throw new Error("dynamic viewer resource root contains an unsupported entry");
      }
      const sourcePath = realpathSync(path);
      if (!isWithin(root, sourcePath)) {
        throw new Error("dynamic viewer resource escapes its root");
      }
      const relativePath = relative(root, sourcePath).split(sep);
      if (
        relativePath.length === 0 ||
        relativePath.some((part) => !/^[A-Za-z0-9._~-]+$/.test(part))
      ) {
        throw new Error("dynamic viewer resource path contains an unsafe segment");
      }
      const url = `/${relativePath.map(encodeURIComponent).join("/")}`;
      if (resources.has(url)) {
        throw new Error(`duplicate dynamic viewer resource URL: ${url}`);
      }
      resources.set(url, sourcePath);
    }
  };
  visit(root);
  if (resources.size === 0) {
    throw new Error("dynamic viewer resource root must not be empty");
  }
  return resources;
}

export function createViewerPackageResourcePlugin(repoRoot: string): Plugin {
  const decoded = loadViewerPackageResources(repoRoot);
  const publicResources = new Map(
    decoded.resources.flatMap((resource) =>
      resource.url === null ? [] : [[resource.url, resource.sourcePath] as const],
    ),
  );
  for (const [url, sourcePath] of loadDynamicViewerResourcesFromEnvironment()) {
    if (publicResources.has(url)) {
      throw new Error(`dynamic viewer resource collides with package resource: ${url}`);
    }
    publicResources.set(url, sourcePath);
  }
  return {
    name: "viewer-package-resources",
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        const requestPath = new URL(request.url ?? "/", "http://viewer.local").pathname;
        const sourcePath = publicResources.get(requestPath);
        if (sourcePath === undefined) {
          next();
          return;
        }
        response.statusCode = 200;
        response.end(readFileSync(sourcePath));
      });
    },
    generateBundle() {
      for (const [publicPath, sourcePath] of publicResources) {
        this.emitFile({
          type: "asset",
          fileName: publicPath.slice(1),
          source: readFileSync(sourcePath),
        });
      }
    },
  };
}
