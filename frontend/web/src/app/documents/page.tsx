"use client";

import { FormEvent, useState } from "react";
import { api, buildUploadForm } from "@/lib/api";
import { DEMO_USER_ID } from "@/lib/config";
import { DocumentSearchHit } from "@/types/domain";
import { FileUp, Search, Loader2 } from "lucide-react";

interface UploadRow {
  id?: number;
  filename: string;
  status: "pending" | "submitted" | "failed";
  message?: string;
}

export default function DocumentsPage() {
  const [file, setFile] = useState<File | null>(null);
  const [uploads, setUploads] = useState<UploadRow[]>([]);
  const [searchQuery, setSearchQuery] = useState("policy for travel meals");
  const [searching, setSearching] = useState(false);
  const [hits, setHits] = useState<DocumentSearchHit[]>([]);
  const [error, setError] = useState<string | null>(null);

  const onUpload = async (e: FormEvent) => {
    e.preventDefault();
    if (!file) return;
    const row: UploadRow = { filename: file.name, status: "pending" };
    setUploads((prev) => [row, ...prev]);
    try {
      const form = buildUploadForm(file, { owner: DEMO_USER_ID, scope: "user_docs", source: "manual" });
      const res = await api.post<{ document_id: number; status: string; message: string }>(`/documents/upload`, form);
      setUploads((prev) => prev.map((u) => (u === row ? { ...u, status: "submitted", id: res.document_id, message: res.message } : u)));
      setError(null);
    } catch (err: any) {
      console.error(err);
      setError(err.message || "Upload failed");
      setUploads((prev) => prev.map((u) => (u === row ? { ...u, status: "failed", message: err.message } : u)));
    }
  };

  const onSearch = async (e: FormEvent) => {
    e.preventDefault();
    setSearching(true);
    try {
      const res = await api.post<{ matches: DocumentSearchHit[] }>(`/documents/search`, {
        query: searchQuery,
        top_k: 5,
        scope: "user_docs",
        owner: DEMO_USER_ID,
      });
      setHits(res.matches || []);
      setError(null);
    } catch (err: any) {
      console.error(err);
      setError(err.message || "Search failed");
    } finally {
      setSearching(false);
    }
  };

  return (
    <div className="space-y-4">
      <header className="flex flex-col gap-1">
        <p className="pill w-fit">My Documents</p>
        <h1 className="text-2xl font-semibold">Upload, ingest, and QnA over documents</h1>
        <p className="text-slate-300">Connected to /documents/upload and /documents/search in core-ai.</p>
      </header>

      {error ? <p className="text-sm text-amber-200">{error}</p> : null}

      <section className="glass-card grid gap-4 p-4 md:grid-cols-2">
        <form onSubmit={onUpload} className="space-y-3">
          <h2 className="section-title">
            <FileUp className="h-4 w-4" /> Upload to doc-search-svc
          </h2>
          <input
            type="file"
            className="input"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
            required
          />
          <button type="submit" className="btn-primary" disabled={!file}>
            Send to ingestion
          </button>
          <p className="text-xs text-slate-400">We pass owner={DEMO_USER_ID} scope=user_docs for filtering.</p>
        </form>

        <div>
          <h3 className="section-title">Ingestion status</h3>
          <div className="mt-2 space-y-2 text-sm">
            {uploads.length === 0 ? (
              <p className="text-slate-400">No uploads yet.</p>
            ) : (
              uploads.map((u, idx) => (
                <div key={idx} className="rounded-xl border border-white/5 bg-white/5 p-3">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold">{u.filename}</span>
                    <Status status={u.status} />
                  </div>
                  <p className="text-xs text-slate-400">{u.message || "Processing"}</p>
                  {u.id ? <p className="text-xs text-slate-500">Document #{u.id}</p> : null}
                </div>
              ))
            )}
          </div>
        </div>
      </section>

      <section className="glass-card p-4">
        <form onSubmit={onSearch} className="flex flex-col gap-3 md:flex-row md:items-end">
          <div className="flex-1">
            <label className="text-sm text-slate-300">Ask a question over your docs</label>
            <div className="mt-1 flex items-center gap-2">
              <Search className="h-4 w-4 text-emerald-300" />
              <input
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="input"
                placeholder="e.g., what is our per-diem limit?"
              />
            </div>
          </div>
          <button type="submit" className="btn-primary" disabled={searching}>
            {searching ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            Search
          </button>
        </form>

        <div className="mt-4 space-y-2">
          {hits.length === 0 ? (
            <p className="text-sm text-slate-400">No matches yet.</p>
          ) : (
            hits.map((hit) => (
              <div key={`${hit.document_id}-${hit.chunk_index}`} className="rounded-xl border border-white/5 bg-white/5 p-3">
                <div className="flex items-center justify-between text-sm">
                  <p className="font-semibold text-slate-100">{hit.title}</p>
                  <span className="badge text-xs text-emerald-200">score {hit.score.toFixed(3)}</span>
                </div>
                <p className="text-xs text-slate-400">Doc #{hit.document_id} - chunk {hit.chunk_index}</p>
                <p className="mt-1 text-xs text-slate-300">Path: {hit.path}</p>
              </div>
            ))
          )}
        </div>
      </section>
    </div>
  );
}

function Status({ status }: { status: UploadRow["status"] }) {
  const label = status === "submitted" ? "submitted" : status === "failed" ? "failed" : "pending";
  const color =
    status === "submitted" ? "text-emerald-200" : status === "failed" ? "text-rose-200" : "text-cyan-200";
  return (
    <span className={`badge ${color}`}>
      {status === "pending" ? <Loader2 className="h-3 w-3 animate-spin" /> : <Search className="h-3 w-3" />} {label}
    </span>
  );
}


