import hljs from "highlight.js/lib/common";

const EXTENSION_LANGUAGES: Record<string, string> = {
  py: "python", pyi: "python", js: "javascript", mjs: "javascript", cjs: "javascript",
  jsx: "javascript", ts: "typescript", tsx: "typescript", json: "json", css: "css",
  scss: "scss", html: "xml", xml: "xml", svg: "xml", md: "markdown", markdown: "markdown",
  sh: "bash", bash: "bash", zsh: "bash", yml: "yaml", yaml: "yaml", toml: "ini", ini: "ini",
  cfg: "ini", rs: "rust", go: "go", rb: "ruby", java: "java", kt: "kotlin", c: "c",
  h: "c", cpp: "cpp", hpp: "cpp", cs: "csharp", php: "php", sql: "sql", swift: "swift",
  lua: "lua", pl: "perl", r: "r", dockerfile: "dockerfile", makefile: "makefile",
  ps1: "powershell", diff: "diff", patch: "diff",
};

function languageFor(path: string): string | null {
  const name = path.split("/").pop()?.toLowerCase() ?? "";
  if (name === "dockerfile" || name.startsWith("dockerfile.")) return "dockerfile";
  if (name === "makefile") return "makefile";
  const ext = name.includes(".") ? name.split(".").pop()! : "";
  const language = EXTENSION_LANGUAGES[ext];
  return language && hljs.getLanguage(language) ? language : null;
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function splitHighlightedLines(html: string): string[] {
  const lines: string[] = [];
  const open: string[] = [];
  let current = "";

  const token = /<span\b[^>]*>|<\/span>|\n|[^<\n]+|</g;
  let match: RegExpExecArray | null;
  while ((match = token.exec(html)) !== null) {
    const piece = match[0];
    if (piece === "\n") {
      lines.push(current + "</span>".repeat(open.length));
      current = open.join("");
    } else if (piece.startsWith("</span")) {
      open.pop();
      current += piece;
    } else if (piece.startsWith("<span")) {
      open.push(piece);
      current += piece;
    } else {
      current += piece;
    }
  }
  lines.push(current + "</span>".repeat(open.length));
  return lines;
}

export function highlightLines(content: string, path: string): string[] {
  // Mirrors Python's str.splitlines(), which the backend uses for all line
  // numbering: one trailing newline shouldn't produce a phantom extra line.
  const source = content.replace(/\n$/, "");
  const language = languageFor(path);
  if (!language) return source.split("\n").map(escapeHtml);
  try {
    return splitHighlightedLines(hljs.highlight(source, { language, ignoreIllegals: true }).value);
  } catch {
    return source.split("\n").map(escapeHtml);
  }
}
