"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { AccessRequest, Expense, LeaveRequest, Ticket, TravelRequest } from "@/types/domain";
import { Check, ShieldCheck, XCircle, Loader2 } from "lucide-react";

const roles = [
  { key: "hr", label: "HR" },
  { key: "ops", label: "Operations" },
  { key: "it", label: "IT" },
] as const;

export default function ApprovalsPage() {
  const [role, setRole] = useState<(typeof roles)[number]["key"]>("hr");
  const [leaves, setLeaves] = useState<LeaveRequest[]>([]);
  const [expenses, setExpenses] = useState<Expense[]>([]);
  const [travel, setTravel] = useState<TravelRequest[]>([]);
  const [access, setAccess] = useState<AccessRequest[]>([]);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const [leaveRes, expRes, travelRes, accessRes, ticketRes] = await Promise.all([
        api.get<{ requests: LeaveRequest[] }>(`/domain/requests/me`).then((r) => r.requests),
        api.get<{ expenses: Expense[] }>(`/domain/expenses/me`).then((r) => r.expenses),
        api.get<{ travel_requests: TravelRequest[] }>(`/domain/travel-requests/me`).then((r) => r.travel_requests),
        api.get<{ access_requests: AccessRequest[] }>(`/domain/access-requests?status=pending`).then((r) => r.access_requests),
        api.get<{ tickets: Ticket[] }>(`/domain/tickets/me`).then((r) => r.tickets),
      ]);
      setLeaves(leaveRes);
      setExpenses(expRes);
      setTravel(travelRes);
      setAccess(accessRes);
      setTickets(ticketRes);
    } catch (err) {
      console.error(err);
      setToast("Failed to load approval queues");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const act = async (kind: string, id: number, decision: "approve" | "reject") => {
    const pathMap: Record<string, string> = {
      leave: `/domain/requests/${id}/${decision}`,
      expense: `/domain/expenses/${id}/${decision}`,
      travel: `/domain/travel-requests/${id}/${decision}`,
      access: `/domain/access-requests/${id}/${decision}`,
    };
    const path = pathMap[kind];
    if (!path) return;
    try {
      await api.post(path, decision === "reject" ? { reason: "Rejected via UI" } : {});
      setToast(`${decision}d ${kind} #${id}`);
      load();
    } catch (err) {
      console.error(err);
      setToast(`Failed to ${decision} ${kind} #${id}`);
    }
  };

  return (
    <div className="space-y-4">
      <header className="flex flex-col gap-2">
        <p className="pill w-fit">Department Approvals</p>
        <h1 className="text-2xl font-semibold">Role-based approval dashboards</h1>
        <p className="text-slate-300">Connected to the approval endpoints exposed in core-ai.</p>
      </header>

      <div className="flex flex-wrap gap-2">
        {roles.map((r) => (
          <button
            key={r.key}
            onClick={() => setRole(r.key)}
            className={`btn-ghost ${role === r.key ? "border-emerald-400 text-emerald-200" : ""}`}
          >
            <ShieldCheck className="h-4 w-4" /> {r.label}
          </button>
        ))}
        {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
        {toast ? <span className="badge text-xs">{toast}</span> : null}
      </div>

      {role === "hr" && (
        <ApprovalCard
          title="Leave requests"
          items={leaves}
          render={(item) => `${item.leave_type} ${item.start_date} -> ${item.end_date}`}
          onApprove={(id) => act("leave", id, "approve")}
          onReject={(id) => act("leave", id, "reject")}
        />
      )}

      {role === "ops" && (
        <div className="grid gap-4 md:grid-cols-2">
          <ApprovalCard
            title="Expenses"
            items={expenses}
            render={(item: Expense) => `${item.amount} ${item.currency} - ${item.category}`}
            onApprove={(id) => act("expense", id, "approve")}
            onReject={(id) => act("expense", id, "reject")}
          />
          <ApprovalCard
            title="Travel"
            items={travel}
            render={(item: TravelRequest) => `${item.origin} -> ${item.destination} on ${item.departure_date}`}
            onApprove={(id) => act("travel", id, "approve")}
            onReject={(id) => act("travel", id, "reject")}
          />
        </div>
      )}

      {role === "it" && (
        <div className="grid gap-4 md:grid-cols-2">
          <ApprovalCard
            title="Access requests"
            items={access}
            render={(item: AccessRequest) => `${item.resource} - ${item.requested_role}`}
            onApprove={(id) => act("access", id, "approve")}
            onReject={(id) => act("access", id, "reject")}
          />
          <div className="glass-card p-4">
            <h2 className="section-title">Tickets (read-only)</h2>
            <p className="text-sm text-slate-400">Ticket lifecycle is managed via /domain/tickets endpoints.</p>
            <div className="mt-2 space-y-2">
              {tickets.map((t) => (
                <div key={t.id} className="rounded-xl border border-white/5 bg-white/5 p-3">
                  <p className="text-sm font-semibold text-slate-100">{t.description}</p>
                  <p className="text-xs text-slate-400">{t.type} - status {t.status}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ApprovalCard<T extends { id: number; status?: string }>({
  title,
  items,
  render,
  onApprove,
  onReject,
}: {
  title: string;
  items: T[];
  render: (item: T) => string;
  onApprove: (id: number) => void;
  onReject: (id: number) => void;
}) {
  return (
    <div className="glass-card p-4">
      <h2 className="section-title">{title}</h2>
      <div className="mt-3 space-y-2 text-sm">
        {items.length === 0 ? (
          <p className="text-slate-400">No items.</p>
        ) : (
          items.map((item) => (
            <div key={item.id} className="rounded-xl border border-white/5 bg-white/5 p-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="font-semibold text-slate-100">{render(item)}</p>
                  <p className="text-xs text-slate-400">Status: {item.status || "pending"}</p>
                </div>
                <div className="flex gap-2">
                  <button onClick={() => onApprove(item.id)} className="btn-ghost text-emerald-200">
                    <Check className="h-4 w-4" />
                  </button>
                  <button onClick={() => onReject(item.id)} className="btn-ghost text-rose-200">
                    <XCircle className="h-4 w-4" />
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}


