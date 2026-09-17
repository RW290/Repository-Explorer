import { useEffect, useRef, useState } from "react";
import { askAboutArchitecture, fetchArchitectureRationales } from "./api";
import type { ArchitectureRationale } from "./types";
import { RichText } from "./markdown";
import { SkeletonLines } from "./Skeleton";
import "./ArchitectureAsk.css";

/**
 * Questions about the architecture map, asked from the map.
 *
 * The backend hands the model the map itself — groups, member files with
 * their summaries, every flow and whether imports back it — so answers are
 * about this diagram. `focus` is whatever the reader has selected (a group
 * box or a node), which gives "explain this section" something to point at;
 * with nothing selected the question is about the whole map. General concept
 * questions are fine too: the model explains the concept, then says where it
 * shows up here.
 *
 * Answers are stored server-side and shared, so the thread opens with
 * whatever anyone has already asked about this repo.
 */

export interface AskFocus {
  kind: "group" | "node";
  id: string;
  label: string;
}

interface Props {
  owner: string;
  name: string;
  focus: AskFocus | null;
  onClearFocus: () => void;
  /** Re-select on the map what an earlier question was about. */
  onShowFocus: (kind: "group" | "node", id: string) => void;
  onClose: () => void;
}

const WHOLE_MAP_SUGGESTIONS = [
  "Walk me through what happens end to end, layer by layer.",
  "Why is it split into these layers? What would a different split cost?",
  "Which flows are inferred rather than verified, and should I trust them?",
  "Where would I start if I wanted to add a feature?",
];

function focusSuggestions(focus: AskFocus): string[] {
  const what = focus.kind === "group" ? "this section" : "this file";
  return [
    `Explain ${what}: what is it responsible for?`,
    `How does ${what} connect to the rest of the system?`,
    focus.kind === "group" ? "Why do these files belong together?" : "What would break if this were removed?",
  ];
}

export function ArchitectureAsk({ owner, name, focus, onClearFocus, onShowFocus, onClose }: Props) {
  const [entries, setEntries] = useState<ArchitectureRationale[]>([]);
  // Until the stored thread has loaded, "nothing asked yet" isn't known.
  const [loaded, setLoaded] = useState(false);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const threadEnd = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchArchitectureRationales(owner, name)
      .then(setEntries)
      .catch(() => {
        // Best-effort: an older backend or a transient failure just means the
        // thread starts empty. Asking still reports its own errors.
      })
      .finally(() => setLoaded(true));
  }, [owner, name]);

  useEffect(() => {
    threadEnd.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [entries.length, asking]);

  async function ask(text: string) {
    const asked = text.trim();
    if (asking) return;
    setAsking(asked || (focus ? `Explain ${focus.label}` : "Walk me through this architecture"));
    setError(null);
    try {
      const entry = await askAboutArchitecture(owner, name, asked || undefined, focus);
      setEntries((prev) => [...prev, entry]);
      setQuestion("");
    } catch (e: unknown) {
      setError(String((e as { message?: string })?.message ?? e));
    } finally {
      setAsking(null);
    }
  }

  const suggestions = focus ? focusSuggestions(focus) : WHOLE_MAP_SUGGESTIONS;

  return (
    <aside className="arch-ask" aria-label="Ask about the architecture">
      <header className="arch-ask__header">
        <div>
          <div className="arch-ask__kind">Ask about the architecture</div>
          <p className="arch-ask__hint">
            Answers are grounded in this map. Click a section or a file on it to ask about that specifically, or ask
            about a concept and see where it shows up here.
          </p>
        </div>
        <button className="arch-ask__close" onClick={onClose} aria-label="Close">
          ×
        </button>
      </header>

      <div className="arch-ask__thread">
        {!loaded && <SkeletonLines lines={4} />}
        {loaded && entries.length === 0 && !asking && (
          <p className="arch-ask__empty">Nothing asked about this architecture yet. Try one of the questions below.</p>
        )}
        {entries.map((entry) => (
          <article key={entry.id} className="arch-ask__entry">
            {entry.focus_label && entry.focus_kind && entry.focus_id && (
              <button
                className="arch-ask__about"
                onClick={() => onShowFocus(entry.focus_kind!, entry.focus_id!)}
                title="Show this on the map"
              >
                {entry.focus_kind === "group" ? "section" : "file"} · {entry.focus_label.split("/").pop()}
              </button>
            )}
            <p className="arch-ask__q">{entry.question}</p>
            <RichText className="arch-ask__a" text={entry.answer} />
          </article>
        ))}
        {asking && (
          <article className="arch-ask__entry arch-ask__entry--pending">
            <p className="arch-ask__q">{asking}</p>
            <SkeletonLines lines={4} />
          </article>
        )}
        <div ref={threadEnd} />
      </div>

      <footer className="arch-ask__composer">
        <div className="arch-ask__scope">
          <span className="arch-ask__scope-label">Asking about</span>
          {focus ? (
            <span className="arch-ask__chip">
              {focus.kind === "group" ? "section" : "file"} · {focus.label.split("/").pop()}
              <button onClick={onClearFocus} aria-label="Ask about the whole map instead" title="Ask about the whole map instead">
                ×
              </button>
            </span>
          ) : (
            <span className="arch-ask__chip arch-ask__chip--whole">the whole map</span>
          )}
        </div>
        <div className="arch-ask__suggestions">
          {suggestions.map((s) => (
            <button key={s} onClick={() => ask(s)} disabled={Boolean(asking)}>
              {s}
            </button>
          ))}
        </div>
        <textarea
          className="arch-ask__input"
          placeholder={
            focus
              ? `Ask about ${focus.label.split("/").pop()}, or anything else…`
              : "e.g. What's the difference between REST and a POST request? Where does caching happen?"
          }
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              if (question.trim()) ask(question);
            }
          }}
          disabled={Boolean(asking)}
        />
        <div className="arch-ask__actions">
          <button className="arch-ask__submit" onClick={() => ask(question)} disabled={Boolean(asking) || !question.trim()}>
            {asking ? "Asking…" : "Ask"}
          </button>
          <span className="arch-ask__enter">Enter to send · Shift+Enter for a new line</span>
        </div>
        {error && <p className="arch-ask__error">{error}</p>}
      </footer>
    </aside>
  );
}
