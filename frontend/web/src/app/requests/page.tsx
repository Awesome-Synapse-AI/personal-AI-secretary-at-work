"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { APPROVALS_WS_URL } from "@/lib/config";
import { AccessRequest, Expense, LeaveRequest, Ticket, TravelRequest, Booking } from "@/types/domain";
import { Loader2, RefreshCw, Radio, CloudOff } from "lucide-react";

interface RequestState {
  leaves: LeaveRequest[];
  expenses: Expense[];
  travel: TravelRequest[];
  access: AccessRequest[];
  tickets: Ticket[];
  bookings: Booking[];
}

type DetailRow = {
  label: string;
  value: string | null | undefined;
};

const initialState: RequestState = { leaves: [], expenses: [], travel: [], access: [], tickets: [], bookings: [] };

export default function RequestsPage() {
  const [data, setData] = useState<RequestState>(initialState);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedItems, setExpandedItems] = useState<Record<string, boolean>>({});

  const load = async () => {
    setLoading(true);
    try {
      const [leaves, expenses, travel, access, tickets, bookings] = await Promise.all([
        api.get<{ requests: LeaveRequest[] }>(`/domain/requests/me`).then((r) => r.requests ?? []),
        api.get<{ expenses: Expense[] }>(`/domain/expenses/me`).then((r) => r.expenses ?? []),
        api.get<{ travel_requests: TravelRequest[] }>(`/domain/travel-requests/me`).then((r) => r.travel_requests ?? []),
        api.get<{ access_requests: AccessRequest[] }>(`/domain/access-requests/me`).then((r) => r.access_requests ?? []),
        api.get<{ tickets: Ticket[] }>(`/domain/tickets/me`).then((r) => r.tickets ?? []),
        api.get<{ bookings: Booking[] }>(`/domain/bookings/me`).then((r) => r.bookings ?? []),
      ]);
      setData({ leaves, expenses, travel, access, tickets, bookings });
      setError(null);
    } catch (err: any) {
      console.error(err);
      setError(err.message || "Failed to load requests");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, []);

  useApprovalSocket(setData);

  const cards = useMemo(
    () => [
      {
        title: "Leave",
        items: data.leaves,
        headline: (l: LeaveRequest) => `${l.leave_type} leave ${l.start_date} -> ${l.end_date}`,
        details: (l: LeaveRequest): DetailRow[] => [
          { label: "Request ID", value: String(l.id) },
          { label: "Type", value: l.leave_type },
          { label: "Start date", value: l.start_date },
          { label: "End date", value: l.end_date },
          { label: "Days", value: l.requested_days != null ? String(l.requested_days) : null },
          { label: "Reason", value: l.reason ?? null },
          { label: "Reject reason", value: l.reject_reason ?? null },
          { label: "Created", value: formatDateTime(l.created_at) },
          { label: "Updated", value: formatDateTime(l.updated_at) },
        ],
        status: (l: LeaveRequest) => l.status,
      },
      {
        title: "Expenses",
        items: data.expenses,
        headline: (e: Expense) => `Amount: ${e.amount} ${e.currency} | Description: ${formatDetailValue(e.category)}`,
        details: (e: Expense): DetailRow[] => [
          { label: "Request ID", value: String(e.id) },
          { label: "Amount", value: String(e.amount) },
          { label: "Currency", value: e.currency },
          { label: "Description", value: e.category },
          { label: "Date", value: e.date },
          { label: "Project", value: e.project_code ?? null },
          { label: "Created", value: formatDateTime(e.created_at) },
          { label: "Updated", value: formatDateTime(e.updated_at) },
        ],
        status: (e: Expense) => e.status,
      },
      {
        title: "Travel",
        items: data.travel,
        headline: (t: TravelRequest) => `${t.origin} -> ${t.destination}`,
        details: (t: TravelRequest): DetailRow[] => [
          { label: "Request ID", value: String(t.id) },
          { label: "Origin", value: t.origin },
          { label: "Destination", value: t.destination },
          { label: "Departure", value: t.departure_date },
          { label: "Return", value: t.return_date ?? null },
          { label: "Class", value: t.travel_class ?? null },
          { label: "Created", value: formatDateTime(t.created_at) },
          { label: "Updated", value: formatDateTime(t.updated_at) },
        ],
        status: (t: TravelRequest) => t.status,
      },
      {
        title: "Access",
        items: data.access,
        headline: (a: AccessRequest) => `${a.resource} - ${a.requested_role}`,
        details: (a: AccessRequest): DetailRow[] => [
          { label: "Request ID", value: String(a.id) },
          { label: "Resource", value: a.resource },
          { label: "Requested role", value: a.requested_role },
          { label: "Justification", value: a.justification },
          { label: "Needed by", value: a.needed_by_date ?? null },
          { label: "Reject reason", value: a.reject_reason ?? null },
          { label: "Created", value: formatDateTime(a.created_at) },
          { label: "Updated", value: formatDateTime(a.updated_at) },
        ],
        status: (a: AccessRequest) => a.status,
      },
      {
        title: "Tickets",
        items: data.tickets,
        headline: (t: Ticket) => `${t.type} - ${t.description}`,
        details: (t: Ticket): DetailRow[] => [
          { label: "Request ID", value: String(t.id) },
          { label: "Type", value: t.type },
          { label: "Description", value: t.description },
          { label: "Category", value: t.category ?? null },
          { label: "Priority", value: t.priority ?? null },
          { label: "Location", value: t.location ?? null },
          { label: "Incident date", value: t.incident_date ?? null },
          { label: "Assignee", value: t.assignee ?? null },
          { label: "Created", value: formatDateTime(t.created_at) },
          { label: "Updated", value: formatDateTime(t.updated_at) },
        ],
        status: (t: Ticket) => t.status,
      },
      {
        title: "Workspace Bookings",
        items: data.bookings,
        headline: (b: Booking) =>
          `${formatResourceType(b.resource_type)}: ${b.resource_name || "Unknown resource"}`,
        details: (b: Booking): DetailRow[] => [
          { label: "Request ID", value: String(b.id) },
          { label: "Resource type", value: b.resource_type },
          { label: "Resource", value: b.resource_name || "Unknown resource" },
          { label: "Start", value: formatDateTime(b.start_time) },
          { label: "End", value: formatDateTime(b.end_time) },
          { label: "Created", value: formatDateTime(b.created_at) },
          { label: "Updated", value: formatDateTime(b.updated_at) },
        ],
        status: (b: Booking) => b.status,
      },
    ],
    [data]
  );

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between">
        <div>
          <p className="pill w-fit">My Requests</p>
          <h1 className="mt-2 text-2xl font-semibold">All workflow requests linked to core-ai</h1>
          <p className="text-slate-300">Leave, expenses, travel, access, and tickets. Auto-refresh every 15s plus WebSocket bump-ins.</p>
        </div>
        <button onClick={load} className="btn-ghost" type="button">
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />} Reload
        </button>
      </header>

      {error ? (
        <div className="glass-card flex items-center gap-2 border border-amber-400/40 bg-amber-500/10 p-3 text-sm text-amber-100">
          <Triangle /> {error}
        </div>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2">
        {cards.map((card) => (
          <div key={card.title} className="glass-card p-4">
            <div className="flex items-center justify-between">
              <h2 className="section-title">{card.title}</h2>
              <span className="badge text-xs text-slate-300">{card.items.length} items</span>
            </div>
            <div className="mt-3 space-y-2">
              {card.items.length === 0 ? (
                <p className="text-sm text-slate-400">No records yet.</p>
              ) : (
                card.items.map((item: any) => (
                  <div key={item.id} className="rounded-xl border border-white/5 bg-white/5 p-3">
                    <p className="text-sm font-semibold text-slate-100">{card.headline(item)}</p>
                    <div className="mt-2 flex items-center justify-between gap-2 text-xs text-slate-400">
                      <div className="flex items-center gap-2">
                        <span>Status: {card.status(item)}</span>
                        <StatusPill status={card.status(item)} />
                      </div>
                      <button
                        type="button"
                        onClick={() => {
                          const key = `${card.title}:${item.id}`;
                          setExpandedItems((prev) => ({ ...prev, [key]: !prev[key] }));
                        }}
                        className="btn-ghost px-2 py-1 text-xs"
                      >
                        {expandedItems[`${card.title}:${item.id}`] ? "Hide details" : "Show details"}
                      </button>
                    </div>
                    {expandedItems[`${card.title}:${item.id}`] ? <DetailGrid rows={card.details(item)} /> : null}
                  </div>
                ))
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function formatDateTime(value?: string | null): string | null {
  if (!value) return null;
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return value;
  return dt.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
    timeZoneName: "short",
  });
}

function DetailGrid({ rows }: { rows: DetailRow[] }) {
  if (rows.length === 0) return null;
  return (
    <div className="mt-2 grid grid-cols-1 gap-x-4 gap-y-1 text-xs text-slate-300 md:grid-cols-2">
      {rows.map((row) => (
        <p key={`${row.label}-${row.value}`}>
          <span className="text-slate-400">{row.label}:</span> {formatDetailValue(row.value)}
        </p>
      ))}
    </div>
  );
}

function formatDetailValue(value: string | null | undefined): string {
  if (value === null || value === undefined) return "N/A";
  const cleaned = String(value).trim();
  return cleaned.length > 0 ? cleaned : "N/A";
}

function formatResourceType(value: string): string {
  if (!value) return "Resource";
  return value.charAt(0).toUpperCase() + value.slice(1).toLowerCase();
}

function StatusPill({ status }: { status: string }) {
  const color = status === "approved" ? "text-emerald-200" : status === "rejected" ? "text-rose-200" : "text-cyan-200";
  return (
    <span className={`badge ${color}`}>
      <Radio size={12} /> {status}
    </span>
  );
}

function Triangle() {
  return <CloudOff className="h-4 w-4" />;
}

function useApprovalSocket(setData: (updater: (prev: RequestState) => RequestState) => void) {
  useEffect(() => {
    if (!APPROVALS_WS_URL) return;
    const ws = new WebSocket(APPROVALS_WS_URL);
    ws.onmessage = (evt) => {
      try {
        const payload = JSON.parse(evt.data);
        // expected: { kind: "leave" | "expense" | "travel" | "access" | "ticket", id: number, status: string }
        setData((prev) => {
          const next: RequestState = {
            leaves: [...prev.leaves],
            expenses: [...prev.expenses],
            travel: [...prev.travel],
            access: [...prev.access],
            tickets: [...prev.tickets],
            bookings: [...prev.bookings],
          };
          const target = cloneMap(next, payload.kind);
          if (!target) return prev;
          const idx = target.findIndex((i: any) => i.id === payload.id);
          if (idx >= 0) target[idx] = { ...target[idx], status: payload.status } as any;
          return next;
        });
      } catch (err) {
        console.error("approval ws parse error", err);
      }
    };
    return () => ws.close();
  }, [setData]);
}

function cloneMap(state: RequestState, kind: string | undefined) {
  if (!kind) return null;
  if (kind === "leave") return state.leaves;
  if (kind === "expense") return state.expenses;
  if (kind === "travel") return state.travel;
  if (kind === "access") return state.access;
  if (kind === "ticket") return state.tickets;
  if (kind === "booking") return state.bookings;
  return null;
}


