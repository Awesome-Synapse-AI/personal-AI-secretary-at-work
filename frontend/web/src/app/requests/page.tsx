"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { APPROVALS_WS_URL } from "@/lib/config";
import { AccessRequest, Expense, LeaveRequest, Ticket, TravelRequest } from "@/types/domain";
import { Loader2, RefreshCw, Radio, CloudOff } from "lucide-react";

interface RequestState {
  leaves: LeaveRequest[];
  expenses: Expense[];
  travel: TravelRequest[];
  access: AccessRequest[];
  tickets: Ticket[];
}

const initialState: RequestState = { leaves: [], expenses: [], travel: [], access: [], tickets: [] };

export default function RequestsPage() {
  const [data, setData] = useState<RequestState>(initialState);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const [leaves, expenses, travel, access, tickets] = await Promise.all([
        api.get<{ requests: LeaveRequest[] }>(`/domain/requests/me`).then((r) => r.requests),
        api.get<{ expenses: Expense[] }>(`/domain/expenses/me`).then((r) => r.expenses),
        api.get<{ travel_requests: TravelRequest[] }>(`/domain/travel-requests/me`).then((r) => r.travel_requests),
        api.get<{ access_requests: AccessRequest[] }>(`/domain/access-requests/me`).then((r) => r.access_requests),
        api.get<{ tickets: Ticket[] }>(`/domain/tickets/me`).then((r) => r.tickets),
      ]);
      setData({ leaves, expenses, travel, access, tickets });
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
      { title: "Leave", items: data.leaves, fields: (l: LeaveRequest) => `${l.leave_type} ${l.start_date} -> ${l.end_date}`, status: (l: LeaveRequest) => l.status },
      { title: "Expenses", items: data.expenses, fields: (e: Expense) => `${e.amount} ${e.currency} - ${e.category}`, status: (e: Expense) => e.status },
      { title: "Travel", items: data.travel, fields: (t: TravelRequest) => `${t.origin} -> ${t.destination} on ${t.departure_date}`, status: (t: TravelRequest) => t.status },
      { title: "Access", items: data.access, fields: (a: AccessRequest) => `${a.resource} - ${a.requested_role}`, status: (a: AccessRequest) => a.status },
      { title: "Tickets", items: data.tickets, fields: (t: Ticket) => `${t.type} - ${t.description}`, status: (t: Ticket) => t.status },
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
                    <p className="text-sm font-semibold text-slate-100">{card.fields(item)}</p>
                    <div className="mt-2 flex items-center justify-between text-xs text-slate-400">
                      <span>Status: {card.status(item)}</span>
                      <StatusPill status={card.status(item)} />
                    </div>
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
  return null;
}


