export type Confidence = "high" | "medium" | "low";

export interface Annotation {
  id: string;
  node_id: string;
  source: "pr" | "commit" | "issue";
  source_ref: string;
  date: string;
  rationale_stated: string | null;
  rationale_inferred: string | null;
  confidence: Confidence;
  diff_summary: string;
}

export interface GraphNode {
  id: string;
  type: "file" | "folder";
  parent: string | null;
  summary: string;
  dependencies: string[];
  annotations: string[];
}

export interface ArchitectureGroup {
  id: string;
  label: string;
  description: string | null;
}

export interface ArchitectureNode {
  // A real GraphNode id (file or folder): the map's boxes are the explorer's
  // own nodes, placed into semantic groups. The backend guarantees it exists
  // in `Graph.nodes` — except for an external system (`external`, id
  // prefixed "ext:"), a library or service outside the repo with no file to
  // open, drawn greyed out.
  id: string;
  group: string | null;
  external?: boolean;
  label?: string | null;
  description?: string | null;
}

export interface ArchitectureEdge {
  source: string;
  target: string;
  label: string | null;
  // True when the static import graph vouches for this relationship; false
  // for one the model asserted that imports can't see (HTTP, subprocess).
  // Drawn dashed, mirroring the stated-vs-inferred split on annotations.
  backed: boolean;
}

// Semantic map: groups like Frontend / API / LLM over a couple dozen of the
// graph's own file/folder nodes, plus the main flows between them. Compiled
// to a Mermaid flowchart client-side.
export interface Architecture {
  groups: ArchitectureGroup[];
  nodes: ArchitectureNode[];
  edges: ArchitectureEdge[];
}

export interface Graph {
  nodes: GraphNode[];
  annotations: Annotation[];
  // Null for the fixture demo, which isn't backed by a real GitHub
  // repo — the viewer uses this to decide whether source-viewing and line
  // rationale are available at all.
  repo_url: string | null;
  // Brief LLM-generated orientation to the whole project. Empty string if
  // generation failed or there was nothing to generate it from.
  overview: string;
  // Null when generation failed or the analysis predates the map stage; the
  // viewer then asks the backend to build one on first open.
  architecture: Architecture | null;
  // Generation of the backend's import resolver that produced the edges.
  // Informational: the backend refreshes stale edges itself on open.
  resolver_version?: number;
}

export interface LineRationale {
  id: string;
  path: string;
  start_line: number;
  end_line: number;
  selected_text: string;
  question: string;
  answer: string;
  created_at: number;
}

export interface FileRationale {
  id: string;
  path: string;
  question: string;
  answer: string;
  created_at: number;
}

// A question asked about the architecture map, optionally focused on one
// group or node ("explain this section"). Shared with everyone on the repo.
export interface ArchitectureRationale {
  id: string;
  question: string;
  answer: string;
  focus_kind: "group" | "node" | null;
  focus_id: string | null;
  focus_label: string | null;
  created_at: number;
}

export interface ProjectRationale {
  id: string;
  question: string;
  answer: string;
  created_at: number;
}

// One-line explanation of a function/method/class, generated for a whole
// file the first time its source is opened and shared after.
export interface SymbolExplainer {
  id: string;
  path: string;
  name: string;
  kind: "function" | "method" | "class";
  line: number;
  end_line: number;
  explanation: string;
  created_at: number;
}

export interface FileContent {
  path: string;
  content: string;
  truncated: boolean;
}

export interface ProgressStage {
  key: string;
  label: string;
  status: "pending" | "running" | "done" | "skipped";
  detail: string | null;
  done: number | null;
  total: number | null;
  seconds: number | null;
}

export interface AnalysisProgress {
  elapsed: number;
  stages: ProgressStage[];
  events: { t: number; text: string; kind: string }[];
  // Version of the partial graph; sent back as `have` so the graph is only
  // re-downloaded when it changed.
  partial_version: number;
}

export interface AnalysisStatus {
  job_id: string | null;
  status: "pending" | "running" | "done" | "error";
  stage: string;
  // While running this is the partial graph (structure first, summaries
  // filling in); null on a poll where nothing changed since `have`.
  graph: Graph | null;
  partial?: boolean;
  progress?: AnalysisProgress | null;
  error: string | null;
}
