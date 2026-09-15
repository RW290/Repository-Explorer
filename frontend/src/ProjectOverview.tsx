import { useEffect, useState } from "react";
import { askAboutProject, fetchProjectRationales } from "./api";
import type { ProjectRationale } from "./types";

interface Props {
  owner: string;
  name: string;
  overview: string;
}

export function ProjectOverview({ owner, name, overview }: Props) {
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

  return (
    <div className="project-overview">
      <div className="project-overview__kind">Project overview</div>
      <p className="project-overview__text">{overview}</p>

      {rationales.length > 0 && (
        <div className="project-overview__qa">
          {rationales.map((r) => (
            <div key={r.id} className="project-overview__entry">
              <p className="project-overview__q">{r.question}</p>
              <p className="project-overview__a">{r.answer}</p>
            </div>
          ))}
        </div>
      )}

      {expanded ? (
        <div className="project-overview__ask">
          <textarea
            className="project-overview__input"
            placeholder="Ask a follow-up question about this project…"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
          />
          <div className="project-overview__actions">
            <button onClick={submit} disabled={asking}>
              {asking ? "Asking…" : "Ask"}
            </button>
            <button className="project-overview__cancel" onClick={() => setExpanded(false)} disabled={asking}>
              Cancel
            </button>
          </div>
          {error && <p className="project-overview__error">{error}</p>}
        </div>
      ) : (
        <button className="project-overview__toggle" onClick={() => setExpanded(true)}>
          Ask a follow-up question <span aria-hidden="true">→</span>
        </button>
      )}
    </div>
  );
}
