import { useEffect, useState } from "react";
import type { AnalysisProgress as Progress, ProgressStage } from "./types";
import "./AnalysisProgress.css";

/**
 * What a running analysis is doing, drawn from the backend's stage tracker.
 *
 * Two shapes of the same data: a card on the landing page for the first few
 * seconds (before there's any graph to show), and a compact HUD over the
 * viewer once the partial graph has arrived and the reader is exploring
 * while summaries fill in.
 *
 * The whole road is visible from the first poll — stages are a fixed list,
 * not discovered as they start — because "3 of 6, and the long one is next"
 * is what makes a wait tolerable, more than any spinner.
 */

interface Props {
  progress: Progress | null;
  variant: "card" | "hud";
  /** Shown until the first poll returns. */
  fallbackLabel?: string;
  /** HUD only: start as the one-line header. The viewer sets this once the
   * reader drills into a folder, where the expanded panel would sit on top of
   * the bottom-left cards; "show" still expands it. */
  compact?: boolean;
}

function formatSeconds(total: number): string {
  const seconds = Math.max(0, Math.round(total));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`;
}

function StageRow({ stage }: { stage: ProgressStage }) {
  const fraction = stage.total ? Math.min(1, (stage.done ?? 0) / stage.total) : null;
  return (
    <li className={`progress-stage progress-stage--${stage.status}`}>
      <span className="progress-stage__icon" aria-hidden="true">
        {stage.status === "done" ? "✓" : stage.status === "running" ? "" : stage.status === "skipped" ? "–" : "·"}
      </span>
      <span className="progress-stage__label">{stage.label}</span>
      <span className="progress-stage__meta">
        {stage.total ? `${stage.done ?? 0}/${stage.total}` : stage.detail ?? ""}
        {stage.status === "done" && stage.total && stage.detail ? "" : ""}
      </span>
      <span className="progress-stage__time">{stage.seconds != null && stage.status !== "pending" ? formatSeconds(stage.seconds) : ""}</span>
      {stage.status === "running" && (
        <span className="progress-stage__bar" aria-hidden="true">
          <span
            className={fraction === null ? "progress-stage__fill progress-stage__fill--indeterminate" : "progress-stage__fill"}
            style={fraction === null ? undefined : { width: `${Math.max(4, fraction * 100)}%` }}
          />
        </span>
      )}
    </li>
  );
}

export function AnalysisProgress({ progress, variant, fallbackLabel, compact = false }: Props) {
  const [collapsed, setCollapsed] = useState(compact);
  // Follow the viewer's hint when it changes (repo level ↔ inside a folder),
  // while leaving the reader's own show/hide in force until it next does.
  useEffect(() => setCollapsed(compact), [compact]);
  // Tick locally between polls so the elapsed clock doesn't advance in jumps.
  const [drift, setDrift] = useState(0);
  useEffect(() => {
    setDrift(0);
    const started = Date.now();
    const timer = window.setInterval(() => setDrift((Date.now() - started) / 1000), 500);
    return () => window.clearInterval(timer);
  }, [progress?.elapsed]);

  if (!progress) {
    return (
      <div className={`progress progress--${variant}`}>
        <div className="progress__header">
          <span className="progress__pulse" aria-hidden="true" />
          <span className="progress__title">{fallbackLabel ?? "Starting…"}</span>
        </div>
      </div>
    );
  }

  const running = progress.stages.filter((s) => s.status === "running");
  const doneCount = progress.stages.filter((s) => s.status === "done" || s.status === "skipped").length;
  const headline = running.length > 0 ? running.map((s) => s.label.toLowerCase()).join(" · ") : "finishing up";

  return (
    <div className={`progress progress--${variant} ${collapsed ? "progress--collapsed" : ""}`} aria-live="polite">
      <div className="progress__header">
        <span className="progress__pulse" aria-hidden="true" />
        <span className="progress__title">
          {variant === "hud" ? "Still analyzing" : "Analyzing"} <em>{headline}</em>
        </span>
        <span className="progress__elapsed">
          {doneCount}/{progress.stages.length} · {formatSeconds(progress.elapsed + drift)}
        </span>
        {variant === "hud" && (
          <button className="progress__toggle" onClick={() => setCollapsed((v) => !v)} aria-expanded={!collapsed}>
            {collapsed ? "show" : "hide"}
          </button>
        )}
      </div>
      {!collapsed && (
        <>
          <ul className="progress__stages">
            {progress.stages.map((stage) => (
              <StageRow key={stage.key} stage={stage} />
            ))}
          </ul>
          {progress.events.length > 0 && (
            <ol className="progress__events" aria-label="Recent activity">
              {progress.events
                .slice(variant === "hud" ? -3 : -5)
                .reverse()
                .map((event) => (
                  <li key={`${event.t}-${event.text}`} className={`progress__event progress__event--${event.kind}`}>
                    <span className="progress__event-time">{formatSeconds(event.t)}</span>
                    {event.text}
                  </li>
                ))}
            </ol>
          )}
          {variant === "hud" && (
            <p className="progress__note">The graph is live: summaries appear on cards as they're written, most-connected files first. The architecture map is built last.</p>
          )}
        </>
      )}
    </div>
  );
}
