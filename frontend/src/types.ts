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
}
