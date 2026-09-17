import "./Skeleton.css";

/**
 * Loading placeholders shaped like the content they stand in for.
 *
 * A spinner says "wait"; a skeleton says "this is what's coming, and here is
 * where it will land", which is both calmer and more informative — and it
 * keeps the layout from jumping when the real thing arrives. Every block
 * shares one shimmer so a screenful of them reads as a single loading state
 * rather than a dozen competing animations.
 */

const LINE_WIDTHS = [96, 88, 92, 70, 84, 58, 90, 76];

export function SkeletonLines({ lines = 3, className = "" }: { lines?: number; className?: string }) {
  return (
    <div className={`skeleton-lines ${className}`} aria-hidden="true">
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} className="skeleton" style={{ width: `${LINE_WIDTHS[i % LINE_WIDTHS.length]}%` }} />
      ))}
    </div>
  );
}

/** Source code still loading: a gutter and ragged lines, indented in runs
 * the way real code is. */
export function CodeSkeleton({ lines = 26 }: { lines?: number }) {
  const shape = [38, 0, 52, 61, 44, 70, 33, 0, 47, 58, 66, 40, 25, 0, 55, 72, 49, 36, 63, 0, 42, 57, 30, 68, 51, 45];
  const indent = [0, 0, 0, 1, 1, 2, 2, 0, 0, 1, 2, 2, 1, 0, 0, 1, 1, 2, 1, 0, 0, 1, 1, 2, 1, 0];
  return (
    <div className="code-skeleton" role="status" aria-label="Loading source">
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="code-skeleton__line">
          <span className="skeleton code-skeleton__no" />
          {shape[i % shape.length] > 0 && (
            <span
              className="skeleton"
              style={{ width: `${shape[i % shape.length]}%`, marginLeft: `${indent[i % indent.length] * 22}px` }}
            />
          )}
        </div>
      ))}
    </div>
  );
}

/** The source viewer's lazy chunk still loading: the modal's frame with code
 * and side-bar placeholders. Styled here rather than with SourceViewer.css,
 * which ships inside the very chunk this is standing in for. */
export function SourceViewerSkeleton() {
  return (
    <div className="source-skeleton" role="status" aria-label="Opening source">
      <div className="source-skeleton__modal">
        <div className="source-skeleton__header">
          <span className="skeleton" style={{ width: 70, height: 26, borderRadius: 7 }} />
          <span className="skeleton" style={{ width: 240 }} />
          <span className="skeleton" style={{ width: 26, height: 26, borderRadius: 7 }} />
        </div>
        <div className="source-skeleton__body">
          <CodeSkeleton />
          <div className="source-skeleton__bar">
            <SkeletonLines lines={6} />
            <SkeletonLines lines={4} />
          </div>
        </div>
      </div>
    </div>
  );
}

/** The architecture map still loading: a few group boxes with nodes in them. */
export function MapSkeleton({ label }: { label?: string }) {
  const groups = [2, 4, 3, 2];
  return (
    <div className="map-skeleton" role="status" aria-label={label ?? "Loading the architecture map"}>
      <div className="map-skeleton__row">
        {groups.map((count, gi) => (
          <div key={gi} className="map-skeleton__group">
            <span className="skeleton map-skeleton__title" />
            {Array.from({ length: count }, (_, i) => (
              <span key={i} className="skeleton map-skeleton__node" />
            ))}
          </div>
        ))}
      </div>
      {label && <p className="map-skeleton__label">{label}</p>}
    </div>
  );
}

/** The whole viewer, before there is any graph at all: top bar, overview
 * prose, a grid of folder cards. */
export function ViewerSkeleton() {
  return (
    <div className="viewer-skeleton" aria-hidden="true">
      <div className="viewer-skeleton__topbar">
        <span className="skeleton viewer-skeleton__crumbs" />
        <span className="skeleton viewer-skeleton__switch" />
      </div>
      <div className="viewer-skeleton__overview">
        <span className="skeleton viewer-skeleton__kicker" />
        <span className="skeleton viewer-skeleton__title" />
        <SkeletonLines lines={4} />
      </div>
      <div className="viewer-skeleton__grid">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="viewer-skeleton__card">
            <span className="skeleton viewer-skeleton__card-kicker" />
            <span className="skeleton viewer-skeleton__card-name" />
            <span className="skeleton viewer-skeleton__card-chips" />
          </div>
        ))}
      </div>
    </div>
  );
}
