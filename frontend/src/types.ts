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

export interface Graph {
  nodes: GraphNode[];
  annotations: Annotation[];
  // Absent for the phase-1 fixture demo, which isn't backed by a real GitHub
  // repo — the viewer uses this to decide whether source-viewing and line
  // rationale are available at all.
  repo_url: string | null;
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

export interface FileContent {
  path: string;
  content: string;
  truncated: boolean;
}

export interface AnalysisStatus {
  job_id: string | null;
  status: "pending" | "running" | "done" | "error";
  stage: string;
  graph: Graph | null;
  error: string | null;
}
