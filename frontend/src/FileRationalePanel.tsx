import { useEffect, useState } from "react";
import { askWhyFileExists, fetchFileRationales } from "./api";
import type { FileRationale } from "./types";
import { RichText } from "./markdown";

interface Props {
  owner: string;
  name: string;
  path: string;
}

export function FileRationalePanel({ owner, name, path }: Props) {
  const [entries, setEntries] = useState<FileRationale[]>([]);
  const [loading, setLoading] = useState(true);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchFileRationales(owner, name, path)
      .then(setEntries)
      .catch((e) => setError(String(e.message ?? e)))
      .finally(() => setLoading(false));
  }, [owner, name, path]);

  async function submit() {
    setAsking(true);
    setError(null);
    try {
      const entry = await askWhyFileExists(owner, name, path, question.trim() || undefined);
      setEntries((prev) => [...prev, entry]);
      setQuestion("");
    } catch (e: unknown) {
      setError(String((e as { message?: string })?.message ?? e));
    } finally {
      setAsking(false);
    }
  }

  return (
    <div className="file-rationale">
      {loading ? (
        <p className="file-rationale__hint">Loading…</p>
      ) : (
        <>
          {entries.length === 0 && (
            <p className="file-rationale__hint">No answers recorded yet for this file — ask below.</p>
          )}
          {entries.map((e) => (
            <div key={e.id} className="file-rationale__entry">
              <p className="file-rationale__q">{e.question}</p>
              <RichText className="file-rationale__a" text={e.answer} />
            </div>
          ))}
          <textarea
            className="file-rationale__input"
            placeholder="Why does this file exist? (optional — defaults to that question)"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
          />
          <button className="file-rationale__submit" onClick={submit} disabled={asking}>
            {asking ? "Asking…" : "Ask"}
          </button>
          {error && <p className="file-rationale__error">{error}</p>}
        </>
      )}
    </div>
  );
}
