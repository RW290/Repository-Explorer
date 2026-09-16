import type { ReactNode } from "react";

/**
 * Renders model output as rich text: inline code, fenced code blocks, bold
 * and italics.
 *
 * The earlier approach asked the prompts for plain prose and stripped any
 * Markdown that came back anyway. That fought the model rather than using
 * it — writing `Session` in backticks is exactly how an identifier should
 * be written, and flattening it lost the distinction between prose and
 * code. The prompts now ask for that markup and this renders it.
 *
 * Builds React elements rather than setting innerHTML: this is model
 * output, and it should never be able to inject markup.
 */

type Block = { kind: "code"; code: string } | { kind: "prose"; text: string };

const FENCE = /```[a-zA-Z0-9_-]*\n?([\s\S]*?)```/g;

function toBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;
  FENCE.lastIndex = 0;
  while ((match = FENCE.exec(text)) !== null) {
    if (match.index > cursor) blocks.push({ kind: "prose", text: text.slice(cursor, match.index) });
    blocks.push({ kind: "code", code: match[1].replace(/\n$/, "") });
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length) blocks.push({ kind: "prose", text: text.slice(cursor) });
  return blocks;
}

// Code spans first, so ** inside backticks stays literal.
const INLINE = /(`[^`]+`)|(\*\*[^*]+\*\*)|(__[^_]+__)|(\*[^*\n]+\*)|(\[[^\]]+\]\([^)]*\))/g;

function inline(text: string, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;
  INLINE.lastIndex = 0;
  while ((match = INLINE.exec(text)) !== null) {
    if (match.index > cursor) out.push(text.slice(cursor, match.index));
    const token = match[0];
    const key = `${keyPrefix}-${match.index}`;
    if (token.startsWith("`")) {
      out.push(<code className="rich__code" key={key}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith("**") || token.startsWith("__")) {
      out.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("*")) {
      out.push(<em key={key}>{token.slice(1, -1)}</em>);
    } else {
      // [label](url): keep the label, drop the URL — model-supplied links
      // aren't trustworthy enough to make clickable.
      out.push(token.slice(1, token.indexOf("]")));
    }
    cursor = match.index + token.length;
  }
  if (cursor < text.length) out.push(text.slice(cursor));
  return out;
}

export function RichText({ text, className }: { text: string; className?: string }) {
  const blocks = toBlocks(text);
  return (
    <div className={className}>
      {blocks.flatMap((block, i) => {
        if (block.kind === "code") {
          return [
            <pre className="rich__block" key={`c${i}`}>
              <code>{block.code}</code>
            </pre>,
          ];
        }
        // Blank lines separate paragraphs; a lone newline is a soft wrap in
        // the model's output, so it joins rather than breaking the sentence.
        return block.text
          .split(/\n\s*\n/)
          .map((paragraph) => paragraph.replace(/\s*\n\s*/g, " ").trim())
          .filter(Boolean)
          .map((paragraph, j) => (
            <p key={`p${i}-${j}`}>{inline(paragraph, `p${i}-${j}`)}</p>
          ));
      })}
    </div>
  );
}
