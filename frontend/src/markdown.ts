/**
 * Strips Markdown syntax from model output.
 *
 * Every prompt in the backend asks for plain prose, but the model emits
 * fences, backticks and bold markers anyway often enough that the
 * instruction can't be relied on — and these strings are rendered as plain
 * text, so the markers show up literally. Belt and braces: the prompt asks,
 * this enforces.
 */
export function stripMarkdown(text: string): string {
  return text
    .replace(/```[a-zA-Z0-9_-]*\s*/g, "")
    .replace(/^\s*#{1,6}\s+/gm, "")
    .replace(/^\s*(?:[-*+]|\d+[.)])\s+/gm, "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/(^|[\s(])\*([^*]+)\*(?=[\s).,!?:;]|$)/g, "$1$2")
    .replace(/(^|[\s(])_([^_]+)_(?=[\s).,!?:;]|$)/g, "$1$2")
    .replace(/[ \t]+\n/g, "\n")
    .trim();
}
