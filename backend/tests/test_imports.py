"""Resolution cases for app.imports, one small synthetic repo per language.

Run from backend/:  python -m unittest discover -s tests -v

The resolvers are regex-and-convention based, which is exactly the kind of
code that silently regresses. Each case writes a tiny repo to a temp dir and
asserts the *exact* edge set for a file — exact, because an extra edge (a
stdlib name latching onto a repo file, a commented-out import) is as much a
bug as a missing one.
"""

import tempfile
import unittest
from pathlib import Path

from app.imports import build_dependency_graph


def graph_for(files: dict[str, str]) -> dict[str, list[str]]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel, content in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(content)
        return build_dependency_graph(root, [root / rel for rel in files])


class ImportResolutionTests(unittest.TestCase):
    def assertEdges(self, files: dict[str, str], source: str, expected: set[str]) -> None:
        self.assertEqual(set(graph_for(files)[source]), expected)

    def test_python_nested_project_and_stdlib_shadow(self):
        files = {
            "backend/app/__init__.py": "",
            "backend/app/main.py": "import json\nimport logging\nfrom app.merge import merge\nfrom app import models\n",
            "backend/app/merge.py": "",
            "backend/app/models.py": "",
            # A repo file named like a stdlib module must not capture `import logging`.
            "backend/app/logging.py": "",
        }
        self.assertEdges(files, "backend/app/main.py", {"backend/app/merge.py", "backend/app/models.py", "backend/app/__init__.py"})

    def test_python_relative_imports(self):
        files = {
            "src/pkg/__init__.py": "from . import core\n",
            "src/pkg/core.py": "",
            "src/pkg/sub/__init__.py": "",
            "src/pkg/sub/helper.py": "from .. import core\nfrom . import missing\n",
        }
        self.assertEdges(files, "src/pkg/__init__.py", {"src/pkg/core.py"})
        self.assertEdges(files, "src/pkg/sub/helper.py", {"src/pkg/core.py"})

    def test_python_src_layout_from_tests(self):
        files = {"src/lib/__init__.py": "", "src/lib/api.py": "", "tests/test_api.py": "import lib\nfrom lib.api import get\n"}
        self.assertEdges(files, "tests/test_api.py", {"src/lib/__init__.py", "src/lib/api.py"})

    def test_js_relative_index_esm_extension_and_dynamic(self):
        files = {
            "src/main.ts": (
                "import a from './a'\nimport { b } from './lib/b.js'\nimport w from './widgets'\n"
                "export * from './reexport'\nconst c = require('./c')\nconst l = () => import('./lazy')\n"
                "// import dead from './dead'\n/* import d2 from './dead' */\nimport React from 'react'\nimport './main.css'\n"
            ),
            "src/a.tsx": "", "src/lib/b.ts": "", "src/widgets/index.ts": "", "src/reexport.ts": "",
            "src/c.js": "", "src/lazy.ts": "", "src/dead.ts": "", "src/main.css": "",
        }
        self.assertEdges(
            files,
            "src/main.ts",
            {"src/a.tsx", "src/lib/b.ts", "src/widgets/index.ts", "src/reexport.ts", "src/c.js", "src/lazy.ts", "src/main.css"},
        )

    def test_js_tsconfig_paths_with_comments_and_subpath_imports(self):
        files = {
            "web/package.json": '{"name": "web"}',
            "web/tsconfig.json": (
                '{\n  // JSONC, with a URL-looking string: "https://x"\n  "compilerOptions": {\n    "baseUrl": ".",\n'
                '    "paths": {"~/*": ["./src/*"], "#cfg": ["./src/config.ts"],}\n  }\n}'
            ),
            "web/src/main.ts": "import u from '~/lib/utils'\nimport cfg from '#cfg'\nimport i from './icon.svg?raw'\n",
            "web/src/lib/utils.ts": "", "web/src/config.ts": "", "web/src/icon.svg": "",
        }
        self.assertEdges(files, "web/src/main.ts", {"web/src/lib/utils.ts", "web/src/config.ts", "web/src/icon.svg"})

    def test_js_tsconfig_extends(self):
        files = {
            "tsconfig.base.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@shared/*": ["shared/*"]}}}',
            "app/tsconfig.json": '{"extends": "../tsconfig.base.json"}',
            "app/index.ts": "import { x } from '@shared/x'\n",
            "shared/x.ts": "",
        }
        self.assertEdges(files, "app/index.ts", {"shared/x.ts"})

    def test_js_workspace_packages_and_src_convention(self):
        files = {
            "apps/web/package.json": '{"name": "web"}',
            "apps/web/src/main.ts": "import { Button } from '@acme/ui'\nimport { f } from '@acme/ui/form'\nimport h from '@/hooks/use'\n",
            "apps/web/src/hooks/use.ts": "",
            "packages/ui/package.json": '{"name": "@acme/ui"}',
            "packages/ui/src/index.ts": "", "packages/ui/src/form.tsx": "",
        }
        self.assertEdges(files, "apps/web/src/main.ts", {"packages/ui/src/index.ts", "packages/ui/src/form.tsx", "apps/web/src/hooks/use.ts"})

    def test_vue_component_script_and_style(self):
        files = {
            "src/App.vue": "<script setup>\nimport Tile from './Tile.vue'\n</script>\n<style lang=\"scss\">\n@use './styles/vars';\n</style>\n",
            "src/Tile.vue": "", "src/styles/_vars.scss": "@import 'mixins';\n", "src/styles/_mixins.scss": "",
        }
        self.assertEdges(files, "src/App.vue", {"src/Tile.vue", "src/styles/_vars.scss"})
        self.assertEdges(files, "src/styles/_vars.scss", {"src/styles/_mixins.scss"})

    def test_html_entry_points(self):
        files = {"web/index.html": '<link rel="stylesheet" href="./a.css"><script type="module" src="/src/main.tsx"></script><script src="https://cdn/x.js"></script>', "web/a.css": "", "web/src/main.tsx": ""}
        self.assertEdges(files, "web/index.html", {"web/a.css", "web/src/main.tsx"})

    def test_go_module_packages(self):
        files = {
            "go.mod": "module github.com/me/app\n\ngo 1.22\n",
            "main.go": 'package main\n\nimport (\n\t"fmt"\n\tdb "github.com/me/app/internal/db"\n\t"github.com/other/lib"\n)\n',
            "internal/db/conn.go": 'package db\nimport "github.com/me/app/internal/cfg"\n',
            "internal/db/conn_test.go": "package db\n",
            "internal/cfg/cfg.go": "package cfg\n",
        }
        self.assertEdges(files, "main.go", {"internal/db/conn.go"})
        self.assertEdges(files, "internal/db/conn.go", {"internal/cfg/cfg.go"})

    def test_rust_modules_paths_and_workspace(self):
        files = {
            "core/Cargo.toml": '[package]\nauthors = ["A <a@b.c>"]\nname = "my-core"\n\n[dependencies]\nname = "decoy"\n',
            "core/src/lib.rs": "pub mod config;\nmod net;\nmod inline { }\n",
            "core/src/config.rs": "use crate::net::client::Client;\n",
            "core/src/net/mod.rs": "pub mod client;\n",
            "core/src/net/client.rs": "use super::super::config::Settings;\nuse std::io;\n",
            "core/src/main.rs": "use my_core::config::Settings;\n",
            "cli/Cargo.toml": '[package]\nname = "cli"\n',
            "cli/src/main.rs": "use my_core::net::client::Client;\n",
        }
        self.assertEdges(files, "core/src/lib.rs", {"core/src/config.rs", "core/src/net/mod.rs"})
        self.assertEdges(files, "core/src/config.rs", {"core/src/net/client.rs"})
        self.assertEdges(files, "core/src/net/client.rs", {"core/src/config.rs"})
        self.assertEdges(files, "core/src/main.rs", {"core/src/config.rs"})
        self.assertEdges(files, "cli/src/main.rs", {"core/src/net/client.rs"})

    def test_jvm_imports(self):
        files = {
            "app/src/main/java/com/acme/App.java": "package com.acme;\nimport com.acme.util.Strings;\nimport static com.acme.util.Maths.add;\nimport com.acme.model.*;\nimport java.util.List;\n",
            "app/src/main/java/com/acme/util/Strings.java": "", "app/src/main/java/com/acme/util/Maths.java": "",
            "app/src/main/java/com/acme/model/User.kt": "",
        }
        self.assertEdges(
            files,
            "app/src/main/java/com/acme/App.java",
            {"app/src/main/java/com/acme/util/Strings.java", "app/src/main/java/com/acme/util/Maths.java", "app/src/main/java/com/acme/model/User.kt"},
        )

    def test_c_includes(self):
        files = {"src/main.c": '#include "util.h"\n#include "net/sock.h"\n#include <stdio.h>\n#include <mylib/api.h>\n', "src/util.h": "", "include/net/sock.h": "", "include/mylib/api.h": ""}
        self.assertEdges(files, "src/main.c", {"src/util.h", "include/net/sock.h", "include/mylib/api.h"})

    def test_ruby_requires(self):
        files = {"lib/acme.rb": 'require "acme/thing"\nrequire_relative "acme/other"\nrequire "json"\n', "lib/acme/thing.rb": "", "lib/acme/other.rb": ""}
        self.assertEdges(files, "lib/acme.rb", {"lib/acme/thing.rb", "lib/acme/other.rb"})

    def test_php_psr4_group_use_and_include(self):
        files = {
            "composer.json": '{"autoload": {"psr-4": {"App\\\\": "src/"}}}',
            "src/Http/Kernel.php": "<?php\nuse App\\Models\\User;\nuse App\\Support\\{Str, Arr};\nuse Vendor\\Lib\\Thing;\nrequire_once __DIR__ . '/helpers.php';\n",
            "src/Models/User.php": "", "src/Support/Str.php": "", "src/Support/Arr.php": "", "src/Http/helpers.php": "",
        }
        self.assertEdges(files, "src/Http/Kernel.php", {"src/Models/User.php", "src/Support/Str.php", "src/Support/Arr.php", "src/Http/helpers.php"})

    def test_dart_package_and_relative(self):
        files = {"pubspec.yaml": "name: shop\n", "lib/main.dart": "import 'package:shop/cart.dart';\nimport 'widgets/tile.dart';\nimport 'package:flutter/material.dart';\nimport 'dart:io';\n", "lib/cart.dart": "", "lib/widgets/tile.dart": ""}
        self.assertEdges(files, "lib/main.dart", {"lib/cart.dart", "lib/widgets/tile.dart"})

    def test_edges_never_leave_the_repo_or_self_reference(self):
        files = {"a/x.ts": "import '../../outside'\nimport './x'\n", "a/y.py": "from ....way.up import thing\n"}
        graph = graph_for(files)
        self.assertEqual(graph["a/x.ts"], [])
        self.assertEqual(graph["a/y.py"], [])

    def test_unsupported_language_has_no_edges_but_is_present(self):
        graph = graph_for({"Program.cs": "using System;\nusing Acme.Core;\n", "Core.cs": "namespace Acme.Core {}\n"})
        self.assertEqual(graph, {"Program.cs": [], "Core.cs": []})


if __name__ == "__main__":
    unittest.main()
