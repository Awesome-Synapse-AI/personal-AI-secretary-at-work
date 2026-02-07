import Link from "next/link";
import { ArrowRight, Bot, FileText, ListChecks, ShieldCheck } from "lucide-react";

export default function Home() {
  return (
    <div className="space-y-8">
      <section className="glass-card p-8 shadow-glow">
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div className="space-y-3">
            <p className="pill w-fit">Phase 6 - Frontend wired to core-ai</p>
            <h1 className="text-3xl font-semibold tracking-tight md:text-4xl">Your personal AI secretary at work</h1>
            <p className="max-w-2xl text-slate-300">
              Chat with agents, track requests, search documents, and approve workflows. Everything here talks directly
              to the FastAPI core in <code>services/core-ai/app</code>.
            </p>
            <div className="flex flex-wrap gap-3">
              <Link href="/chat" className="btn-primary">
                Launch chat <ArrowRight size={16} />
              </Link>
              <Link href="/requests" className="btn-ghost">
                View my requests
              </Link>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3 text-sm text-slate-200">
            {["Router", "Domain", "Tools", "Guardrails"].map((item) => (
              <div key={item} className="glass-card px-4 py-3 text-center">
                <p className="text-xs uppercase tracking-[0.2em] text-slate-400">{item}</p>
                <p className="text-lg font-semibold">Active</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-2">
        <FeatureCard
          title="Employee Chat"
          href="/chat"
          icon={<Bot className="h-5 w-5" />}
          description="Streaming tokens, agent pipeline strip, and clarification prompts for missing fields."
        />
        <FeatureCard
          title="My Requests"
          href="/requests"
          icon={<ListChecks className="h-5 w-5" />}
          description="Leave, expenses, travel, access, and ticket status with live updates."
        />
        <FeatureCard
          title="My Documents"
          href="/documents"
          icon={<FileText className="h-5 w-5" />}
          description="Upload to doc-search-svc, monitor ingestion, and ask questions over indexed content."
        />
        <FeatureCard
          title="Department Approvals"
          href="/approvals"
          icon={<ShieldCheck className="h-5 w-5" />}
          description="Role-based views for HR/IT/Admin with approve and reject actions plus audit trail UI."
        />
      </section>
    </div>
  );
}

function FeatureCard({ title, description, href, icon }: { title: string; description: string; href: string; icon: React.ReactNode }) {
  return (
    <Link href={href} className="glass-card block p-6 transition hover:-translate-y-1 hover:shadow-glow">
      <div className="flex items-center gap-3">
        <span className="badge bg-emerald-500/10 text-emerald-200">{icon}</span>
        <p className="text-lg font-semibold">{title}</p>
      </div>
      <p className="mt-3 text-slate-300">{description}</p>
      <p className="mt-4 inline-flex items-center gap-2 text-sm text-emerald-200">
        Open <ArrowRight size={14} />
      </p>
    </Link>
  );
}


