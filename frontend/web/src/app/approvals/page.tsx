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

type DetailRow = {
  label: string;
  value: string | null | undefined;
};

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
      const results = await Promise.allSettled([
        fetchWithFallback<LeaveRequest[]>([
          () => api.get<{ requests: LeaveRequest[] }>("/domain/requests?status=submitted").then((r) => r.requests),
          () => api.get<{ requests: LeaveRequest[] }>("/domain/requests/me").then((r) => r.requests),
        ]),
        fetchWithFallback<Expense[]>([
          () => api.get<{ expenses: Expense[] }>("/domain/expenses?status=submitted").then((r) => r.expenses),
          () => api.get<{ expenses: Expense[] }>("/domain/expenses/me").then((r) => r.expenses),
        ]),
        fetchWithFallback<TravelRequest[]>([
          () => api.get<{ travel_requests: TravelRequest[] }>("/domain/travel-requests?status=submitted").then((r) => r.travel_requests),
          () => api.get<{ travel_requests: TravelRequest[] }>("/domain/travel-requests/me").then((r) => r.travel_requests),
        ]),
        api.get<{ access_requests: AccessRequest[] }>("/domain/access-requests?status=pending").then((r) => r.access_requests),
        api.get<{ tickets: Ticket[] }>("/domain/tickets/me").then((r) => r.tickets),
      ]);

      const [leaveRes, expRes, travelRes, accessRes, ticketRes] = results.map((r) => (r.status === "fulfilled" ? r.value : []));

      setLeaves(leaveRes as LeaveRequest[]);
      setExpenses(expRes as Expense[]);
      setTravel(travelRes as TravelRequest[]);
      setAccess(accessRes as AccessRequest[]);
      setTickets(ticketRes as Ticket[]);

      const firstError = results.find((r) => r.status === "rejected") as PromiseRejectedResult | undefined;
      setToast(firstError ? (firstError.reason?.message || "Some queues failed to load") : null);
    } catch (err) {
      console.error(err);
      const msg = err instanceof Error ? err.message : "Failed to load approval queues";
      setToast(msg);
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
          headline={(item) => `${item.leave_type} ${item.start_date} -> ${item.end_date}`}
          details={(item) => [
            { label: "Request ID", value: String(item.id) },
            { label: "Type", value: item.leave_type },
            { label: "Start date", value: item.start_date },
            { label: "End date", value: item.end_date },
            { label: "Days", value: item.requested_days != null ? String(item.requested_days) : null },
            { label: "Reason", value: item.reason ?? null },
            { label: "Created", value: formatDateTime(item.created_at) },
            { label: "Updated", value: formatDateTime(item.updated_at) },
          ]}
          onApprove={(id) => act("leave", id, "approve")}
          onReject={(id) => act("leave", id, "reject")}
        />
      )}

      {role === "ops" && (
        <div className="grid gap-4 md:grid-cols-2">
          <ApprovalCard
            title="Expenses"
            items={expenses}
            headline={(item: Expense) => `Amount: ${item.amount} ${item.currency} | Description: ${formatDetailValue(item.category)}`}
            details={(item: Expense) => [
              { label: "Request ID", value: String(item.id) },
              { label: "Amount", value: String(item.amount) },
              { label: "Currency", value: item.currency },
              { label: "Description", value: item.category },
              { label: "Date", value: item.date },
              { label: "Project", value: item.project_code ?? null },
              { label: "Created", value: formatDateTime(item.created_at) },
              { label: "Updated", value: formatDateTime(item.updated_at) },
            ]}
            onApprove={(id) => act("expense", id, "approve")}
            onReject={(id) => act("expense", id, "reject")}
          />
          <ApprovalCard
            title="Travel"
            items={travel}
            headline={(item: TravelRequest) => `${item.origin} -> ${item.destination} on ${item.departure_date}`}
            details={(item: TravelRequest) => [
              { label: "Request ID", value: String(item.id) },
              { label: "Origin", value: item.origin },
              { label: "Destination", value: item.destination },
              { label: "Departure", value: item.departure_date },
              { label: "Departure time", value: item.preferred_departure_time ?? null },
              { label: "Return", value: item.return_date ?? null },
              { label: "Return time", value: item.preferred_return_time ?? null },
              { label: "Class", value: item.travel_class ?? null },
              { label: "Created", value: formatDateTime(item.created_at) },
              { label: "Updated", value: formatDateTime(item.updated_at) },
            ]}
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
            headline={(item: AccessRequest) => `${item.resource} - ${item.requested_role}`}
            details={(item: AccessRequest) => [
              { label: "Request ID", value: String(item.id) },
              { label: "Resource", value: item.resource },
              { label: "Requested role", value: item.requested_role },
              { label: "Justification", value: item.justification },
              { label: "Needed by", value: item.needed_by_date ?? null },
              { label: "Created", value: formatDateTime(item.created_at) },
              { label: "Updated", value: formatDateTime(item.updated_at) },
            ]}
            onApprove={(id) => act("access", id, "approve")}
            onReject={(id) => act("access", id, "reject")}
          />
          <div className="glass-card p-4">
            <h2 className="section-title">Tickets (read-only)</h2>
            <p className="text-sm text-slate-400">Ticket lifecycle is managed via /domain/tickets endpoints.</p>
            <div className="mt-2 space-y-2">
              {tickets.map((t) => (
                <TicketDetailItem key={t.id} ticket={t} />
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

async function fetchWithFallback<T>(fns: Array<() => Promise<T>>): Promise<T> {
  let lastError: unknown;
  for (const fn of fns) {
    try {
      return await fn();
    } catch (err) {
      lastError = err;
    }
  }
  if (lastError) throw lastError;
  throw new Error("No fetch functions provided");
}

function ApprovalCard<T extends { id: number; status?: string }>({
  title,
  items,
  headline,
  details,
  onApprove,
  onReject,
}: {
  title: string;
  items: T[];
  headline: (item: T) => string;
  details: (item: T) => DetailRow[];
  onApprove: (id: number) => void;
  onReject: (id: number) => void;
}) {
  const [expandedItems, setExpandedItems] = useState<Record<number, boolean>>({});

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
                  <p className="font-semibold text-slate-100">{headline(item)}</p>
                  <p className="text-xs text-slate-400">Status: {item.status || "pending"}</p>
                  {expandedItems[item.id] ? <DetailGrid rows={details(item)} /> : null}
                </div>
                <div className="flex gap-2">
                  <button onClick={() => setExpandedItems((prev) => ({ ...prev, [item.id]: !prev[item.id] }))} className="btn-ghost text-xs" type="button">
                    {expandedItems[item.id] ? "Hide details" : "Show details"}
                  </button>
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

function TicketDetailItem({ ticket: t }: { ticket: Ticket }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="rounded-xl border border-white/5 bg-white/5 p-3">
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-semibold text-slate-100">{t.description}</p>
        <button onClick={() => setExpanded((prev) => !prev)} className="btn-ghost text-xs" type="button">
          {expanded ? "Hide details" : "Show details"}
        </button>
      </div>
      <p className="mt-1 text-xs text-slate-400">
        {t.type} - status {t.status}
      </p>
      {expanded ? (
        <div className="mt-2 grid grid-cols-1 gap-x-4 gap-y-1 text-xs text-slate-300 md:grid-cols-2">
          <p>
            <span className="text-slate-400">Request ID:</span> {t.id}
          </p>
          <p>
            <span className="text-slate-400">Type:</span> {t.type}
          </p>
          <p>
            <span className="text-slate-400">Status:</span> {t.status}
          </p>
          {t.category ? (
            <p>
              <span className="text-slate-400">Category:</span> {t.category}
            </p>
          ) : null}
          {t.priority ? (
            <p>
              <span className="text-slate-400">Priority:</span> {t.priority}
            </p>
          ) : null}
          {t.location ? (
            <p>
              <span className="text-slate-400">Location:</span> {t.location}
            </p>
          ) : null}
          {t.incident_date ? (
            <p>
              <span className="text-slate-400">Incident date:</span> {t.incident_date}
            </p>
          ) : null}
          <p>
            <span className="text-slate-400">Created:</span> {formatDetailValue(formatDateTime(t.created_at))}
          </p>
          <p>
            <span className="text-slate-400">Updated:</span> {formatDetailValue(formatDateTime(t.updated_at))}
          </p>
        </div>
      ) : null}
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


