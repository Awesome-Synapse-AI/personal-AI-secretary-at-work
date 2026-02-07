export type Status = "submitted" | "approved" | "rejected" | "open" | "in_progress" | "resolved" | "closed" | "pending";

export interface LeaveRequest {
  id: number;
  user_id: string;
  leave_type: string;
  start_date: string;
  end_date: string;
  status: string;
  requested_days?: number;
  approver_id?: string | null;
  reject_reason?: string | null;
  created_at: string;
  updated_at: string;
  reason?: string | null;
}

export interface Expense {
  id: number;
  user_id: string;
  amount: number;
  currency: string;
  date: string;
  category: string;
  project_code?: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface TravelRequest {
  id: number;
  user_id: string;
  origin: string;
  destination: string;
  departure_date: string;
  return_date?: string | null;
  travel_class?: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface Ticket {
  id: number;
  user_id: string;
  type: string;
  category?: string | null;
  description: string;
  location?: string | null;
  priority?: string | null;
  status: string;
  assignee?: string | null;
  created_at: string;
  updated_at: string;
}

export interface AccessRequest {
  id: number;
  user_id: string;
  resource: string;
  requested_role: string;
  justification: string;
  status: string;
  approver_id?: string | null;
  reject_reason?: string | null;
  created_at: string;
  updated_at: string;
}

export interface DocumentSearchHit {
  document_id: number;
  title: string;
  score: number;
  chunk_index: number;
  path: string;
}

export type ChatEvent = {
  type: string;
  data?: Record<string, any>;
};

export type ChatAction = {
  type: string;
  status: string;
  payload?: Record<string, any>;
  result?: Record<string, any>;
  error?: string;
};

export interface PendingRequest {
  domain: string;
  type: string;
  filled: Record<string, any>;
  missing: string[];
  step?: string;
  subtype?: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: number;
  pendingRequest?: PendingRequest | null;
  actions?: ChatAction[];
  events?: ChatEvent[];
}
