"use client";

import { useState } from "react";
import { Bot, Send, User, Sparkles, TriangleAlert, CheckCircle2, Workflow } from "lucide-react";
import { useChatStream } from "@/lib/hooks/useChatStream";
import { ChatEvent, ChatMessage, PendingRequest } from "@/types/domain";

export default function ChatPage() {
  const { messages, send, streamingText, pendingRequest, connecting, connectionError, events } = useChatStream();
  const [draft, setDraft] = useState("");

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!draft.trim()) return;
    send(draft.trim());
    setDraft("");
  };

  const timeline = buildPipeline(events);

  return (
    <div className="space-y-4">
      <header className="flex items-start justify-between gap-4">
        <div>
          <p className="pill w-fit">Employee Chat</p>
          <h1 className="mt-2 text-2xl font-semibold">Chat with the core-ai agents</h1>
          <p className="text-slate-300">Streaming tokens + Router -> Domain -> Tools pipeline visualization.</p>
        </div>
        {connectionError ? (
          <span className="badge bg-amber-500/20 text-amber-100">{connectionError}</span>
        ) : connecting ? (
          <span className="badge">Connecting...</span>
        ) : (
          <span className="badge text-emerald-200">Live</span>
        )}
      </header>

      <AgentPipeline timeline={timeline} />

      <div className="glass-card flex flex-col gap-4 p-4">
        <div className="space-y-3 max-h-[60vh] overflow-y-auto pr-2">
          {messages.map((m) => (
            <MessageBubble key={m.id} message={m} />
          ))}
          {streamingText ? (
            <MessageBubble
              message={{
                id: "streaming",
                role: "assistant",
                content: streamingText,
                createdAt: Date.now(),
              }}
              streaming
            />
          ) : null}
        </div>

        {pendingRequest ? <PendingCard pending={pendingRequest} /> : null}

        <form onSubmit={onSubmit} className="flex items-end gap-3">
          <textarea
            className="input min-h-[60px] flex-1"
            placeholder="Ask for leave, log an expense, book a room, file a ticket, or ask a policy question..."
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <button type="submit" className="btn-primary">
            Send <Send size={16} />
          </button>
        </form>
      </div>
    </div>
  );
}

function MessageBubble({ message, streaming }: { message: ChatMessage; streaming?: boolean }) {
  const isUser = message.role === "user";
  return (
    <div className={`flex gap-3 ${isUser ? "justify-end" : "justify-start"}`}>
      {!isUser && (
        <span className="badge bg-emerald-500/10 text-emerald-200">
          <Bot size={14} />
          Agent
        </span>
      )}
      <div
        className={`max-w-[75%] rounded-2xl px-4 py-3 text-sm leading-6 shadow-lg ${
          isUser ? "bg-slate-800/80 text-slate-100" : "bg-white/10 text-slate-100"
        } ${streaming ? "border border-emerald-400/50" : ""}`}
      >
        {!isUser && message.pendingRequest ? (
          <p className="mb-2 text-xs uppercase tracking-[0.2em] text-emerald-300">Pending request context</p>
        ) : null}
        {message.content || "..."}
        {message.actions?.length ? (
          <div className="mt-2 space-y-1 text-xs text-emerald-200">
            {message.actions.map((a, idx) => (
              <div key={idx} className="flex items-center gap-2">
                <CheckCircle2 size={14} />
                <span>
                  {a.type}: {a.status}
                </span>
              </div>
            ))}
          </div>
        ) : null}
      </div>
      {isUser && (
        <span className="badge bg-cyan-500/10 text-cyan-200">
          <User size={14} />
          You
        </span>
      )}
    </div>
  );
}

function PendingCard({ pending }: { pending: PendingRequest }) {
  return (
    <div className="glass-card border-emerald-400/30 bg-emerald-400/10 p-4">
      <div className="flex items-center gap-2 text-sm font-semibold text-emerald-100">
        <Sparkles size={16} /> Clarification needed ({pending.type})
      </div>
      <p className="mt-2 text-slate-100">I still need these details:</p>
      <div className="mt-2 flex flex-wrap gap-2">
        {pending.missing.map((field) => (
          <span key={field} className="badge bg-white/10 text-emerald-100">
            {field}
          </span>
        ))}
      </div>
      {pending.filled && Object.keys(pending.filled).length ? (
        <div className="mt-3 text-xs text-slate-200">
          <p className="font-semibold">Captured:</p>
          <div className="mt-1 grid grid-cols-2 gap-1">
            {Object.entries(pending.filled).map(([k, v]) => (
              <p key={k} className="truncate">
                <span className="text-slate-400">{k}:</span> {String(v)}
              </p>
            ))}
          </div>
        </div>
      ) : null}
      <p className="mt-3 text-xs text-slate-300">Tip: Answer the highlighted questions directly to advance the flow.</p>
    </div>
  );
}

function AgentPipeline({ timeline }: { timeline: PipelineState }) {
  const steps = [
    { key: "router", label: "Router" },
    { key: "domain", label: "Domain" },
    { key: "tools", label: "Tools" },
  ] as const;
  return (
    <div className="glass-card flex items-center gap-4 p-3 text-sm">
      <Workflow className="h-4 w-4 text-emerald-300" />
      <div className="flex w-full flex-col gap-2 md:flex-row md:items-center">
        {steps.map((step, idx) => {
          const state = timeline[step.key];
          const color = state === "done" ? "bg-emerald-500" : state === "active" ? "bg-cyan-400" : "bg-slate-600";
          return (
            <div key={step.key} className="flex items-center gap-2">
              <span className={`h-2.5 w-2.5 rounded-full ${color} shadow-glow`} />
              <span className="font-medium text-slate-100">{step.label}</span>
              {idx < steps.length - 1 && <span className="h-px w-8 bg-white/10 md:w-12" />}
            </div>
          );
        })}
      </div>
      <span className="text-xs text-slate-400">derived from agent events</span>
    </div>
  );
}

type PipelineState = { router: "idle" | "active" | "done"; domain: "idle" | "active" | "done"; tools: "idle" | "active" | "done" };

function buildPipeline(events: ChatEvent[]): PipelineState {
  const state: PipelineState = { router: "idle", domain: "idle", tools: "idle" };
  for (const evt of events) {
    if (evt.type === "agent_started" && evt.data?.agent === "RouterAgent") state.router = "active";
    if (evt.type === "agent_finished" && evt.data?.agent === "RouterAgent") state.router = "done";
    if (evt.type === "agent_started" && evt.data?.agent === "DomainAgent") state.domain = "active";
    if (evt.type === "agent_finished" && evt.data?.agent === "DomainAgent") state.domain = "done";
    if (evt.type === "tool_call") state.tools = "active";
    if (evt.type === "tool_result") state.tools = "done";
  }
  return state;
}

