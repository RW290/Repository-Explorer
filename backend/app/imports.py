"""File-level dependency edges for many languages: local static analysis, no LLM.

`build_dependency_graph` maps every file to the repo files it imports. It is
deliberately best-effort and deliberately conservative: an edge is only
drawn when an import statement resolves to a file that actually exists in
the repo. Anything that points outside the repo (a package from npm, PyPI,
crates.io, the standard library) resolves to nothing and is dropped — that
is what keeps the graph a picture of *this* codebase rather than of its
dependency tree.

Why regexes and not real parsers for everything but Python: a proper parser
per language (tree-sitter grammars, the TypeScript compiler, `go list`)
would mean native dependencies or a toolchain per language on the server,
for a marginal gain. Import statements are the most regular syntax any
language has; a line-anchored pattern gets nearly all of them. The
interesting work is *resolution* — turning `~/lib/utils`, `crate::config`,
`com.acme.Foo` or `"github.com/me/app/internal/db"` into a path — and that
is language semantics, implemented below per language:

- Python      `ast`; relative imports, packages, and import roots inferred
              from layout (repo root, src/, lib/, project dirs, ancestors)
- JS/TS       import/export-from/require/dynamic import; relative paths,
              extension and index resolution, the `.js`→`.ts` ESM quirk,
              tsconfig/jsconfig `paths` + `baseUrl` (with `extends`),
              workspace packages, and the `@/`→`src/` convention
- Go          go.mod module path → package directory → its .go files
- Rust        `mod x;` declarations and `crate::`/`super::`/`self::` paths,
              plus sibling workspace crates
- Java/Kotlin/Scala/Groovy  fully-qualified imports matched by path suffix
- C/C++/ObjC  `#include`, relative first, then by path suffix
- Ruby        require_relative, and require against lib/
- PHP         composer PSR-4 `use`, and literal include/require
- Dart        relative and own-`package:` imports
- CSS/SCSS/Less  @import/@use/@forward incl. Sass partials
- HTML/Vue/Svelte  script/link tags (and the JS rules for their scripts)

Languages whose imports name modules rather than files (C#, Swift) get no
edges: a namespace can be spread over any number of files, so any edge drawn
would be a guess.
"""

import ast
import json
import posixpath
import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

# Bump when resolution changes materially, so analyses cached under an older
# resolver can have their edges refreshed (see pipeline.refresh_dependencies)
# without paying for a full LLM re-analysis.
RESOLVER_VERSION = 2

MAX_SCAN_BYTES = 1_000_000
# A Go import or a Java wildcard names a whole package/directory. Linking to
# every file in a huge one would bury the graph, so those fan-outs are capped.
MAX_PACKAGE_FANOUT = 8

_STDLIB = set(getattr(sys, "stdlib_module_names", ()))

JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
_JS_RESOLVE_EXTENSIONS = JS_EXTENSIONS + (".d.ts", ".vue", ".svelte", ".json", ".css", ".scss", ".sass", ".less")
# TypeScript ESM asks you to write the *output* extension in the import
# (`./foo.js`) even though the file on disk is `./foo.ts`.
_JS_OUTPUT_TO_SOURCE = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}


@dataclass
class RepoIndex:
    """Everything resolvers need to know about the repo, built once."""

    root: Path
    files: set[str]
    dirs: dict[str, list[str]] = field(default_factory=dict)  # dir -> files directly in it
    by_basename: dict[str, list[str]] = field(default_factory=dict)
    _text_cache: dict[str, str | None] = field(default_factory=dict)
    _memo: dict[str, object] = field(default_factory=dict)

    @classmethod
    def build(cls, root: Path, files: Iterable[str]) -> "RepoIndex":
        index = cls(root=root, files=set(files))
        for f in sorted(index.files):
            index.dirs.setdefault(posixpath.dirname(f), []).append(f)
            index.by_basename.setdefault(posixpath.basename(f), []).append(f)
        return index

    def read(self, rel: str) -> str | None:
        if rel not in self._text_cache:
            path = self.root / rel
            try:
                if path.stat().st_size > MAX_SCAN_BYTES:
                    self._text_cache[rel] = None
                else:
                    self._text_cache[rel] = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                self._text_cache[rel] = None
        return self._text_cache[rel]

    def memo(self, key: str, build: Callable[[], object]) -> object:
        if key not in self._memo:
            self._memo[key] = build()
        return self._memo[key]

    def by_suffix(self, suffix: str, near: str) -> str | None:
        """The known file equal to `suffix` or ending in `/suffix`. When several
        match (two packages each with a `utils/config.py`), the one sharing
        the longest directory prefix with the importing file wins — imports
        overwhelmingly point at the nearer of two same-named files."""
        suffix = suffix.strip("/")
        if not suffix:
            return None
        candidates = [f for f in self.by_basename.get(posixpath.basename(suffix), []) if f == suffix or f.endswith("/" + suffix)]
        if not candidates:
            return None
        near_parts = near.split("/")[:-1]

        def closeness(candidate: str) -> tuple[int, int]:
            shared = 0
            for a, b in zip(near_parts, candidate.split("/")):
                if a != b:
                    break
                shared += 1
            return (-shared, len(candidate))

        return min(candidates, key=closeness)

    def ancestors(self, rel: str) -> list[str]:
        """Directories from the file's own outward to the repo root ("")."""
        out = []
        current = posixpath.dirname(rel)
        while True:
            out.append(current)
            if not current:
                return out
            current = posixpath.dirname(current)


def _norm(path: str) -> str | None:
    """Normalized repo-relative path, or None if it escapes the repo."""
    normalized = posixpath.normpath(path)
    if normalized.startswith("../") or normalized == ".." or normalized.startswith("/"):
        return None
    return "" if normalized == "." else normalized


def _join(directory: str, *parts: str) -> str | None:
    return _norm(posixpath.join(directory, *parts)) if directory else _norm(posixpath.join(*parts))


# --------------------------------------------------------------------------
# Python
# --------------------------------------------------------------------------


def _python_roots(index: RepoIndex, rel: str) -> list[str]:
    """Directories an absolute import may be rooted at, nearest first.

    Python resolves `import a.b` against sys.path, which static analysis
    can't see — so infer it from layout: the importing file's own ancestors
    (a script's directory, or the directory above its package chain, is on
    the path when it runs), then project directories (anything with a
    pyproject.toml/setup.py, and their src/), then the classic repo-level
    roots."""

    def project_roots() -> list[str]:
        roots: list[str] = []
        for marker in ("pyproject.toml", "setup.py", "setup.cfg"):
            for f in index.by_basename.get(marker, []):
                d = posixpath.dirname(f)
                roots.extend([d, posixpath.join(d, "src") if d else "src", posixpath.join(d, "lib") if d else "lib"])
        return roots

    shared = index.memo("py_project_roots", project_roots)
    seen: set[str] = set()
    ordered: list[str] = []
    for root in [*index.ancestors(rel), *shared, "", "src", "lib"]:  # type: ignore[misc]
        if root not in seen:
            seen.add(root)
            ordered.append(root)
    return ordered


def _python_module_file(index: RepoIndex, base: str) -> str | None:
    for candidate in (f"{base}.py", f"{base}/__init__.py", f"{base}.pyi"):
        if candidate in index.files:
            return candidate
    return None


def _python_deps(index: RepoIndex, rel: str, text: str) -> set[str]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return set()
    deps: set[str] = set()
    here = posixpath.dirname(rel)

    def add_module(base: str | None, names: list[str]) -> bool:
        """`base` is a module path without extension. Imported names may
        themselves be submodules (`from pkg import mod`), so try those too."""
        if base is None:
            return False
        found = False
        module_file = _python_module_file(index, base)
        if module_file:
            deps.add(module_file)
            found = True
        for name in names:
            sub = _python_module_file(index, f"{base}/{name}")
            if sub:
                deps.add(sub)
                found = True
        return found

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets = [(alias.name, 0, []) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            targets = [(node.module, node.level, [a.name for a in node.names if a.name != "*"])]
        else:
            continue
        for module, level, names in targets:
            parts = module.split(".") if module else []
            if level > 0:
                base_dir = here
                for _ in range(level - 1):
                    base_dir = posixpath.dirname(base_dir)
                base = posixpath.join(base_dir, *parts) if parts else base_dir
                if parts:
                    add_module(base, names)
                else:
                    # `from . import x`: each name is a sibling module.
                    for name in names:
                        sub = _python_module_file(index, posixpath.join(base, name) if base else name)
                        if sub:
                            deps.add(sub)
                continue
            if not parts or parts[0] in _STDLIB:
                continue
            for root in _python_roots(index, rel):
                base = posixpath.join(root, *parts) if root else posixpath.join(*parts)
                if add_module(base, names):
                    break
    return deps


# --------------------------------------------------------------------------
# JavaScript / TypeScript (and the script blocks of Vue / Svelte files)
# --------------------------------------------------------------------------

_JS_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_JS_LINE_COMMENT = re.compile(r"^\s*//.*$", re.M)
_JS_IMPORT_PATTERNS = [
    re.compile(r"""(?:^|[;\s])import\s+(?:type\s+)?(?:[\w*${}\s,]+?\s+from\s+)?["']([^"'\n]+)["']""", re.M),
    re.compile(r"""(?:^|[;\s])export\s+(?:type\s+)?(?:\*(?:\s+as\s+[\w$]+)?|\{[^}]*\})\s+from\s+["']([^"'\n]+)["']""", re.M),
    re.compile(r"""\brequire\(\s*["']([^"'\n]+)["']\s*\)"""),
    re.compile(r"""\bimport\(\s*["']([^"'\n]+)["']\s*\)"""),
]


def _strip_json_comments(text: str) -> str:
    """tsconfig is JSON-with-comments. A regex can't tell `//` in a comment
    from `//` in "https://…", so walk it with string state."""
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
            out.append(ch)
        elif ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        else:
            out.append(ch)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def _load_ts_config(index: RepoIndex, config_path: str, depth: int = 0) -> dict:
    """{'base': dir baseUrl resolves to or None, 'paths': [(prefix, suffix, [targets])]}"""
    text = index.read(config_path)
    if text is None:
        return {"base": None, "paths": []}
    try:
        data = json.loads(_strip_json_comments(text))
    except (json.JSONDecodeError, ValueError):
        return {"base": None, "paths": []}
    config_dir = posixpath.dirname(config_path)
    result = {"base": None, "paths": []}
    extends = data.get("extends") if isinstance(data, dict) else None
    if isinstance(extends, str) and extends.startswith(".") and depth < 3:
        parent = _join(config_dir, extends)
        if parent:
            for candidate in (parent, f"{parent}.json"):
                if candidate in index.files:
                    result = _load_ts_config(index, candidate, depth + 1)
                    break
    options = data.get("compilerOptions") if isinstance(data, dict) else None
    if not isinstance(options, dict):
        return result
    base = result["base"]
    if isinstance(options.get("baseUrl"), str):
        base = _join(config_dir, options["baseUrl"])
        result["base"] = base
    paths = options.get("paths")
    if isinstance(paths, dict):
        # `paths` targets are relative to baseUrl, or to the config when unset.
        anchor = base if base is not None else config_dir
        parsed = []
        for pattern, targets in paths.items():
            if not isinstance(targets, list):
                continue
            prefix, star, suffix = pattern.partition("*")
            resolved = [_join(anchor, t) if anchor else _norm(t) for t in targets if isinstance(t, str)]
            parsed.append((prefix, suffix if star else None, [r for r in resolved if r is not None]))
        # Longest prefix first, as the compiler does.
        result["paths"] = sorted(parsed, key=lambda p: -len(p[0]))
    return result


def _nearest_ts_config(index: RepoIndex, rel: str) -> dict:
    for directory in index.ancestors(rel):
        for name in ("tsconfig.json", "jsconfig.json"):
            candidate = posixpath.join(directory, name) if directory else name
            if candidate in index.files:
                return index.memo(f"tsconfig:{candidate}", lambda c=candidate: _load_ts_config(index, c))  # type: ignore[return-value]
    return {"base": None, "paths": []}


def _workspace_packages(index: RepoIndex) -> dict[str, str]:
    """package.json `name` → its directory, for monorepo cross-package imports."""

    def build() -> dict[str, str]:
        packages: dict[str, str] = {}
        for f in index.by_basename.get("package.json", []):
            text = index.read(f)
            if not text:
                continue
            try:
                name = json.loads(text).get("name")
            except (json.JSONDecodeError, AttributeError, ValueError):
                continue
            if isinstance(name, str) and name:
                packages[name] = posixpath.dirname(f)
        return packages

    return index.memo("js_workspace", build)  # type: ignore[return-value]


def _js_resolve_path(index: RepoIndex, base: str | None) -> str | None:
    """Node/bundler file resolution for an extensionless-or-not base path."""
    if base is None:
        return None
    if base in index.files:
        return base
    stem, ext = posixpath.splitext(base)
    for source_ext in _JS_OUTPUT_TO_SOURCE.get(ext, ()):
        if stem + source_ext in index.files:
            return stem + source_ext
    for extension in _JS_RESOLVE_EXTENSIONS:
        if base + extension in index.files:
            return base + extension
    for extension in _JS_RESOLVE_EXTENSIONS:
        candidate = posixpath.join(base, "index" + extension) if base else "index" + extension
        if candidate in index.files:
            return candidate
    return None


def _js_resolve(index: RepoIndex, rel: str, spec: str) -> str | None:
    # Drop bundler query/fragment suffixes (`./icon.svg?raw`) — but a leading
    # `#` is a Node subpath import (`#config`), not a fragment.
    spec = spec.split("?")[0]
    if "#" in spec[1:]:
        spec = spec[: spec.index("#", 1)]
    if not spec or spec.startswith(("http:", "https:", "data:", "node:")):
        return None
    here = posixpath.dirname(rel)
    if spec.startswith("."):
        return _js_resolve_path(index, _join(here, spec))
    if spec.startswith("/"):
        # Root-relative (Vite/Next public or src root): try the package root.
        for directory in index.ancestors(rel):
            pkg = posixpath.join(directory, "package.json") if directory else "package.json"
            if pkg in index.files or not directory:
                for sub in ("", "src", "public"):
                    hit = _js_resolve_path(index, _join(directory, sub, spec.lstrip("/")) if directory or sub else _norm(spec.lstrip("/")))
                    if hit:
                        return hit
        return None

    config = _nearest_ts_config(index, rel)
    for prefix, suffix, targets in config["paths"]:
        if suffix is None:
            if spec != prefix:
                continue
            captured = ""
        else:
            if not (spec.startswith(prefix) and spec.endswith(suffix) and len(spec) >= len(prefix) + len(suffix)):
                continue
            captured = spec[len(prefix) : len(spec) - len(suffix) if suffix else len(spec)]
        for target in targets:
            hit = _js_resolve_path(index, _norm(target.replace("*", captured)) if "*" in target else target)
            if hit:
                return hit
    if config["base"] is not None:
        hit = _js_resolve_path(index, _join(config["base"], spec) if config["base"] else _norm(spec))
        if hit:
            return hit

    packages = _workspace_packages(index)
    for name, directory in packages.items():
        if spec == name or spec.startswith(name + "/"):
            rest = spec[len(name) :].lstrip("/")
            if rest:
                for sub in ("", "src"):
                    hit = _js_resolve_path(index, _join(directory, sub, rest) if directory or sub else _norm(rest))
                    if hit:
                        return hit
            else:
                for entry in ("src/index", "index", "src/main", "lib/index"):
                    hit = _js_resolve_path(index, _join(directory, entry) if directory else entry)
                    if hit:
                        return hit
            return None

    # The `@/x` and `~/x` → `<package>/src/x` convention, for projects that
    # declare it in a bundler config rather than tsconfig.
    if spec.startswith(("@/", "~/")):
        for directory in index.ancestors(rel):
            pkg = posixpath.join(directory, "package.json") if directory else "package.json"
            if pkg in index.files or not directory:
                for sub in ("src", "", "app"):
                    hit = _js_resolve_path(index, _join(directory, sub, spec[2:]) if directory or sub else _norm(spec[2:]))
                    if hit:
                        return hit
    return None


def _js_deps(index: RepoIndex, rel: str, text: str) -> set[str]:
    cleaned = _JS_LINE_COMMENT.sub("", _JS_BLOCK_COMMENT.sub("", text))
    deps: set[str] = set()
    for pattern in _JS_IMPORT_PATTERNS:
        for match in pattern.finditer(cleaned):
            hit = _js_resolve(index, rel, match.group(1).strip())
            if hit:
                deps.add(hit)
    return deps


# --------------------------------------------------------------------------
# Go
# --------------------------------------------------------------------------

_GO_IMPORT_BLOCK = re.compile(r"^\s*import\s*\((.*?)\)", re.S | re.M)
_GO_IMPORT_SINGLE = re.compile(r'^\s*import\s+(?:[\w.]+\s+)?"([^"]+)"', re.M)
_GO_IMPORT_LINE = re.compile(r'^\s*(?:[\w.]+\s+)?"([^"]+)"', re.M)


def _go_modules(index: RepoIndex) -> list[tuple[str, str]]:
    def build() -> list[tuple[str, str]]:
        modules = []
        for f in index.by_basename.get("go.mod", []):
            text = index.read(f) or ""
            match = re.search(r"^\s*module\s+(\S+)", text, re.M)
            if match:
                modules.append((match.group(1).strip('"'), posixpath.dirname(f)))
        return sorted(modules, key=lambda m: -len(m[0]))

    return index.memo("go_modules", build)  # type: ignore[return-value]


def _go_deps(index: RepoIndex, rel: str, text: str) -> set[str]:
    imports = set(_GO_IMPORT_SINGLE.findall(text))
    for block in _GO_IMPORT_BLOCK.findall(text):
        imports.update(_GO_IMPORT_LINE.findall(block))
    deps: set[str] = set()
    for path in imports:
        for module, module_dir in _go_modules(index):
            if path != module and not path.startswith(module + "/"):
                continue
            package_dir = _join(module_dir, path[len(module) :].lstrip("/")) if path != module else module_dir
            if package_dir is None:
                break
            # A Go import names a package (a directory), not a file.
            members = [f for f in index.dirs.get(package_dir, []) if f.endswith(".go") and not f.endswith("_test.go")]
            deps.update(members[:MAX_PACKAGE_FANOUT])
            break
    return deps


# --------------------------------------------------------------------------
# Rust
# --------------------------------------------------------------------------

_RUST_MOD = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+(\w+)\s*;", re.M)
_RUST_PATH = re.compile(r"\b(crate|super|self)((?:::\w+)+)")
_RUST_USE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?use\s+(\w+)((?:::\w+)*)", re.M)
_RUST_ROOT_FILES = {"mod.rs", "lib.rs", "main.rs"}


def _rust_crates(index: RepoIndex) -> list[tuple[str, str]]:
    """(crate name as written in `use`, crate src dir), one per Cargo.toml."""

    def build() -> list[tuple[str, str]]:
        crates = []
        for f in index.by_basename.get("Cargo.toml", []):
            text = index.read(f) or ""
            # The [package] table runs to the next table header. (Not "to the
            # next `[`": `authors = ["…"]` has one on the very next line.)
            section = re.search(r"^\[package\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
            package = re.search(r"^\s*name\s*=\s*\"([^\"]+)\"", section.group(1), re.M) if section else None
            directory = posixpath.dirname(f)
            src = posixpath.join(directory, "src") if directory else "src"
            if package:
                crates.append((package.group(1).replace("-", "_"), src))
        return crates

    return index.memo("rust_crates", build)  # type: ignore[return-value]


def _rust_module_file(index: RepoIndex, base_dir: str, segments: list[str]) -> str | None:
    """Longest resolvable prefix of a module path: `a::b::Thing` is b.rs if it
    exists, else a.rs (Thing and b being items inside it)."""
    for end in range(len(segments), 0, -1):
        stem = posixpath.join(base_dir, *segments[:end]) if base_dir else posixpath.join(*segments[:end])
        for candidate in (f"{stem}.rs", f"{stem}/mod.rs"):
            if candidate in index.files:
                return candidate
    return None


def _rust_deps(index: RepoIndex, rel: str, text: str) -> set[str]:
    here = posixpath.dirname(rel)
    name = posixpath.basename(rel)
    # Where this file's child modules live, and where its parent's do.
    self_dir = here if name in _RUST_ROOT_FILES else posixpath.join(here, name[:-3]) if here else name[:-3]
    super_dir = posixpath.dirname(here) if name in _RUST_ROOT_FILES else here

    crate_src = None
    for crate_name, src in sorted(_rust_crates(index), key=lambda c: -len(c[1])):
        if rel.startswith(src + "/"):
            crate_src = src
            break

    deps: set[str] = set()
    for child in _RUST_MOD.findall(text):
        hit = _rust_module_file(index, self_dir, [child])
        if hit:
            deps.add(hit)
    for anchor, tail in _RUST_PATH.findall(text):
        segments = [s for s in tail.split("::") if s]
        base = {"crate": crate_src, "super": super_dir, "self": self_dir}[anchor]
        # `super::super::x`: each further `super` is one more module up.
        while base is not None and segments and segments[0] == "super":
            base = posixpath.dirname(base) if base else None
            segments = segments[1:]
        if base is None or not segments:
            continue
        hit = _rust_module_file(index, base, segments)
        if hit:
            deps.add(hit)
    for first, tail in _RUST_USE.findall(text):
        for crate_name, src in _rust_crates(index):
            if first != crate_name:
                continue
            # Another workspace crate — or this crate's own library, which a
            # binary (main.rs, src/bin/*) names by crate name, not `crate::`.
            segments = [s for s in tail.split("::") if s]
            hit = _rust_module_file(index, src, segments) if segments else None
            hit = hit or next((c for c in (f"{src}/lib.rs", f"{src}/main.rs") if c in index.files), None)
            if hit:
                deps.add(hit)
    return deps


# --------------------------------------------------------------------------
# JVM languages
# --------------------------------------------------------------------------

_JVM_IMPORT = re.compile(r"^\s*import\s+(static\s+)?([\w.]+?)(\.\*|\._)?\s*;?\s*$", re.M)
_JVM_EXTENSIONS = (".java", ".kt", ".scala", ".groovy")


def _jvm_deps(index: RepoIndex, rel: str, text: str) -> set[str]:
    deps: set[str] = set()
    for static, dotted, wildcard in _JVM_IMPORT.findall(text):
        segments = dotted.split(".")
        if len(segments) < 2:
            continue
        if wildcard and not static:
            # `import com.acme.util.*` — the package directory's files.
            directory_suffix = "/".join(segments)
            matches = sorted(d for d in index.dirs if d == directory_suffix or d.endswith("/" + directory_suffix))
            if matches:
                members = [f for f in index.dirs[matches[0]] if f.endswith(_JVM_EXTENSIONS)]
                deps.update(members[:MAX_PACKAGE_FANOUT])
            continue
        # A static or nested-class import names a member inside the class, so
        # walk back until a file matches (at most two segments).
        for drop in range(0, 3):
            candidate_segments = segments[: len(segments) - drop]
            if len(candidate_segments) < 2:
                break
            hit = None
            for extension in _JVM_EXTENSIONS:
                hit = index.by_suffix("/".join(candidate_segments) + extension, rel)
                if hit:
                    break
            if hit:
                deps.add(hit)
                break
    return deps


# --------------------------------------------------------------------------
# C family
# --------------------------------------------------------------------------

_C_INCLUDE = re.compile(r'^\s*#\s*(?:include|import)\s*(["<])([^">]+)[">]', re.M)


def _c_deps(index: RepoIndex, rel: str, text: str) -> set[str]:
    here = posixpath.dirname(rel)
    deps: set[str] = set()
    for quote, path in _C_INCLUDE.findall(text):
        hit = None
        if quote == '"':
            local = _join(here, path)
            hit = local if local in index.files else None
        # Quoted includes fall back to the include path; angle includes are
        # usually system headers, so only a path-shaped one is worth matching.
        if hit is None and (quote == '"' or "/" in path):
            hit = index.by_suffix(path, rel)
        if hit:
            deps.add(hit)
    return deps


# --------------------------------------------------------------------------
# Ruby, PHP, Dart
# --------------------------------------------------------------------------

_RUBY_REQUIRE = re.compile(r"""^\s*(require_relative|require|load)\s*\(?\s*["']([^"']+)["']""", re.M)


def _ruby_deps(index: RepoIndex, rel: str, text: str) -> set[str]:
    here = posixpath.dirname(rel)
    deps: set[str] = set()
    for kind, path in _RUBY_REQUIRE.findall(text):
        target = path if path.endswith(".rb") else path + ".rb"
        if kind == "require_relative" or path.startswith("."):
            local = _join(here, target)
            if local in index.files:
                deps.add(local)  # type: ignore[arg-type]
            continue
        # `require "acme/thing"` resolves against $LOAD_PATH; lib/ is the
        # convention every gem follows.
        hit = index.by_suffix("lib/" + target, rel) or index.by_suffix(target, rel) if "/" in target else index.by_suffix("lib/" + target, rel)
        if hit:
            deps.add(hit)
    return deps


_PHP_USE = re.compile(r"^\s*use\s+(?:function\s+|const\s+)?\\?([\w\\]+)(?:\s+as\s+\w+)?\s*;", re.M)
_PHP_GROUP_USE = re.compile(r"^\s*use\s+\\?([\w\\]+)\\\{([^}]+)\}\s*;", re.M)
_PHP_INCLUDE = re.compile(r"""\b(?:require|include)(?:_once)?\s*\(?\s*(?:__DIR__\s*\.\s*)?["']([^"']+\.php)["']""")


def _php_psr4(index: RepoIndex) -> list[tuple[str, str]]:
    def build() -> list[tuple[str, str]]:
        mappings = []
        for f in index.by_basename.get("composer.json", []):
            try:
                data = json.loads(index.read(f) or "{}")
            except (json.JSONDecodeError, ValueError):
                continue
            base = posixpath.dirname(f)
            for section in ("autoload", "autoload-dev"):
                psr4 = (data.get(section) or {}).get("psr-4") if isinstance(data, dict) else None
                if not isinstance(psr4, dict):
                    continue
                for namespace, directories in psr4.items():
                    for directory in [directories] if isinstance(directories, str) else directories:
                        resolved = _join(base, directory) if base else _norm(directory)
                        if resolved is not None:
                            mappings.append((namespace.strip("\\"), resolved))
        return sorted(mappings, key=lambda m: -len(m[0]))

    return index.memo("php_psr4", build)  # type: ignore[return-value]


def _php_class_file(index: RepoIndex, rel: str, qualified: str) -> str | None:
    qualified = qualified.strip("\\")
    for namespace, directory in _php_psr4(index):
        if qualified == namespace or qualified.startswith(namespace + "\\"):
            rest = qualified[len(namespace) :].strip("\\").replace("\\", "/")
            candidate = _join(directory, rest + ".php") if directory else _norm(rest + ".php")
            if candidate in index.files:
                return candidate
    segments = qualified.split("\\")
    # No composer mapping: match by path suffix, needing at least two
    # segments so a bare class name can't latch onto an unrelated file.
    for start in range(0, max(len(segments) - 1, 1)):
        hit = index.by_suffix("/".join(segments[start:]) + ".php", rel)
        if hit:
            return hit
    return None


def _php_deps(index: RepoIndex, rel: str, text: str) -> set[str]:
    here = posixpath.dirname(rel)
    deps: set[str] = set()
    names = list(_PHP_USE.findall(text))
    for prefix, group in _PHP_GROUP_USE.findall(text):
        names.extend(f"{prefix}\\{item.strip().split(' as ')[0].strip()}" for item in group.split(","))
    for name in names:
        hit = _php_class_file(index, rel, name)
        if hit:
            deps.add(hit)
    for path in _PHP_INCLUDE.findall(text):
        local = _join(here, path.lstrip("/"))
        if local in index.files:
            deps.add(local)  # type: ignore[arg-type]
    return deps


_DART_IMPORT = re.compile(r"""^\s*(?:import|export|part)\s+["']([^"']+)["']""", re.M)


def _dart_deps(index: RepoIndex, rel: str, text: str) -> set[str]:
    def packages() -> dict[str, str]:
        found = {}
        for f in index.by_basename.get("pubspec.yaml", []):
            match = re.search(r"^name:\s*(\S+)", index.read(f) or "", re.M)
            if match:
                directory = posixpath.dirname(f)
                found[match.group(1)] = posixpath.join(directory, "lib") if directory else "lib"
        return found

    own = index.memo("dart_packages", packages)
    here = posixpath.dirname(rel)
    deps: set[str] = set()
    for spec in _DART_IMPORT.findall(text):
        if spec.startswith("dart:"):
            continue
        if spec.startswith("package:"):
            name, _, rest = spec[len("package:") :].partition("/")
            lib = own.get(name)  # type: ignore[union-attr]
            candidate = _join(lib, rest) if lib else None
        else:
            candidate = _join(here, spec)
        if candidate in index.files:
            deps.add(candidate)  # type: ignore[arg-type]
    return deps


# --------------------------------------------------------------------------
# Stylesheets and markup
# --------------------------------------------------------------------------

_CSS_IMPORT = re.compile(r"""@(?:import|use|forward)\s+(?:url\(\s*)?["']([^"'\n]+)["']""")
_STYLE_EXTENSIONS = (".scss", ".sass", ".less", ".css", ".styl")


def _style_resolve(index: RepoIndex, rel: str, spec: str) -> str | None:
    spec = spec.split("?")[0]
    if not spec or spec.startswith(("http:", "https:", "data:", "sass:")):
        return None
    base = _join(posixpath.dirname(rel), spec[1:] if spec.startswith("~") else spec)
    if base is None:
        return None
    directory, name = posixpath.split(base)
    options = [base, *(base + e for e in _STYLE_EXTENSIONS)]
    # Sass partials: `@use "vars"` finds `_vars.scss`.
    options += [posixpath.join(directory, "_" + name + e) for e in ("", *_STYLE_EXTENSIONS)]
    options += [posixpath.join(base, "_index" + e) for e in _STYLE_EXTENSIONS]
    options += [posixpath.join(base, "index" + e) for e in _STYLE_EXTENSIONS]
    return next((o for o in options if o in index.files), None)


def _style_deps(index: RepoIndex, rel: str, text: str) -> set[str]:
    cleaned = _JS_BLOCK_COMMENT.sub("", text)
    return {hit for spec in _CSS_IMPORT.findall(cleaned) if (hit := _style_resolve(index, rel, spec))}


_HTML_REF = re.compile(r"""<(?:script|link|img|source)\b[^>]*?\b(?:src|href)\s*=\s*["']([^"'#?]+)""", re.I)


def _html_deps(index: RepoIndex, rel: str, text: str) -> set[str]:
    here = posixpath.dirname(rel)
    deps: set[str] = set()
    for ref in _HTML_REF.findall(text):
        if ref.startswith(("http:", "https:", "//", "data:", "mailto:")):
            continue
        if ref.startswith("/"):
            # Root-relative: a dev server serves from the page's own directory
            # (Vite) or a public/ folder beside it.
            options = [_join(here, ref.lstrip("/")), _join(here, "public", ref.lstrip("/")), _norm(ref.lstrip("/"))]
        else:
            options = [_join(here, ref)]
        hit = next((o for o in options if o in index.files), None)
        if hit:
            deps.add(hit)
    return deps


def _component_deps(index: RepoIndex, rel: str, text: str) -> set[str]:
    """Vue/Svelte/Astro single-file components: JS imports in the script
    block, stylesheet imports in the style block. The patterns are specific
    enough to run over the whole file."""
    return _js_deps(index, rel, text) | _style_deps(index, rel, text)


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

Resolver = Callable[[RepoIndex, str, str], set[str]]

_BY_EXTENSION: dict[str, Resolver] = {
    **{e: _python_deps for e in (".py", ".pyi")},
    **{e: _js_deps for e in JS_EXTENSIONS},
    **{e: _component_deps for e in (".vue", ".svelte", ".astro")},
    ".go": _go_deps,
    ".rs": _rust_deps,
    **{e: _jvm_deps for e in _JVM_EXTENSIONS},
    **{e: _c_deps for e in (".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx", ".m", ".mm", ".ino")},
    ".rb": _ruby_deps,
    ".php": _php_deps,
    ".dart": _dart_deps,
    **{e: _style_deps for e in _STYLE_EXTENSIONS},
    **{e: _html_deps for e in (".html", ".htm")},
}

SUPPORTED_LANGUAGES = "Python, JavaScript/TypeScript, Go, Rust, Java/Kotlin/Scala, C/C++, Ruby, PHP, Dart, CSS/SCSS, HTML"


def resolver_for(rel: str) -> Resolver | None:
    lowered = rel.lower()
    if lowered.endswith(".d.ts"):
        return _js_deps
    return _BY_EXTENSION.get(posixpath.splitext(lowered)[1])


def build_dependency_graph(repo_root: Path, files: list[Path]) -> dict[str, list[str]]:
    """Every file gets an entry (possibly empty). Edges only ever point at
    files in `files`, never at itself, and never outside the repo."""
    rels = [f.relative_to(repo_root).as_posix() for f in files]
    index = RepoIndex.build(repo_root, rels)
    graph: dict[str, list[str]] = {}
    for rel in rels:
        resolver = resolver_for(rel)
        text = index.read(rel) if resolver else None
        if resolver is None or text is None:
            graph[rel] = []
            continue
        try:
            deps = resolver(index, rel, text)
        except (RecursionError, re.error, ValueError):
            # One pathological file must not cost the repo its whole graph.
            deps = set()
        graph[rel] = sorted(d for d in deps if d != rel and d in index.files)
    return graph
