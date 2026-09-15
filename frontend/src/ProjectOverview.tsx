import { useEffect, useState, type CSSProperties } from "react";
import { askAboutProject, fetchProjectRationales } from "./api";
import type { ProjectRationale } from "./types";

interface Props {
  owner: string;
  name: string;
  overview: string;
  style?: CSSProperties;
}

function splitOverview(text: string): string[] {
  const blocks = text
    .trim()
    .split(/\n\s*\n|\n/)
    .map((block) => block.trim())
    .filter(Boolean);

  if (blocks.length > 1) return blocks;

  // Older cached overviews are usually one 4–6 sentence paragraph. Grouping
  // sentences keeps those results readable without requiring a re-analysis.
  const sentences = text.match(/[^.!?]+[.!?]+(?:\s|$)/g)?.map((sentence) => sentence.trim()) ?? [];
  if (sentences.length < 3) return [text.trim()];

  const groups: string[] = [];
  const groupSize = Math.ceil(sentences.length / 2);
  for (let index = 0; index < sentences.length; index += groupSize) {
    groups.push(sentences.slice(index, index + groupSize).join(" "));
  }
  return groups;
}

export function ProjectOverview({ owner, name, overview, style }: Props) {
  const [rationales, setRationales] = useState<ProjectRationale[]>([]);
  const [expanded, setExpanded] = useState(false);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchProjectRationales(owner, name)
      .then(setRationales)
      .catch(() => {
        // Best-effort: no prior Q&A yet, or a transient fetch failure —
        // either way this just means the follow-up thread starts empty.
      });
  }, [owner, name]);

  async function submit() {
    setAsking(true);
    setError(null);
    try {
      const entry = await askAboutProject(owner, name, question.trim() || undefined);
      setRationales((prev) => [...prev, entry]);
      setQuestion("");
    } catch (e: unknown) {
      setError(String((e as { message?: string })?.message ?? e));
    } finally {
      setAsking(false);
    }
  }

  if (!overview) return null;
  const overviewParagraphs = splitOverview(overview);

  return (
    <div className="overview-card" style={style} onClick={(e) => e.stopPropagation()}>
      <div className="overview-card__kind">Project overview</div>
      <h2 className="overview-card__title">{name}</h2>
      <div className="overview-card__text">
        {overviewParagraphs.map((paragraph, index) => (
          <p key={`${index}-${paragraph.slice(0, 20)}`}>{paragraph}</p>
        ))}
      </div>

      {rationales.length > 0 && (
        <div className="overview-card__qa">
          {rationales.map((r) => (
            <div key={r.id} className="overview-card__entry">
              <p className="overview-card__q">{r.question}</p>
              <p className="overview-card__a">{r.answer}</p>
            </div>
          ))}
        </div>
      )}

      {expanded ? (
        <div className="overview-card__ask">
          <textarea
            className="overview-card__input"
            placeholder="Ask a follow-up question about this project…"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
          />
          <div className="overview-card__actions">
            <button onClick={submit} disabled={asking}>
              {asking ? "Asking…" : "Ask"}
            </button>
            <button className="overview-card__cancel" onClick={() => setExpanded(false)} disabled={asking}>
              Cancel
            </button>
          </div>
          {error && <p className="overview-card__error">{error}</p>}
        </div>
      ) : (
        <button className="overview-card__toggle" onClick={() => setExpanded(true)}>
          Ask a follow-up question <span aria-hidden="true">→</span>
        </button>
      )}
    </div>
  );
}
