"use client";

import { useEffect, useState } from "react";
import { Check, Loader2, PlugZap, TriangleAlert } from "lucide-react";
import { API_BASE_URL } from "@/lib/config";

export function ApiStatusChip() {
  const [status, setStatus] = useState<"idle" | "ok" | "error" | "loading">("idle");

  useEffect(() => {
    let cancelled = false;
    async function ping() {
      setStatus("loading");
      try {
        const res = await fetch(`${API_BASE_URL}/health`, { cache: "no-store" });
        if (!res.ok) throw new Error("bad status");
        if (!cancelled) setStatus("ok");
      } catch (err) {
        console.error("health check failed", err);
        if (!cancelled) setStatus("error");
      }
    }
    ping();
    const id = setInterval(ping, 30000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const icon =
    status === "loading" ? (
      <Loader2 className="h-3 w-3 animate-spin" />
    ) : status === "ok" ? (
      <Check className="h-3 w-3" />
    ) : status === "error" ? (
      <TriangleAlert className="h-3 w-3" />
    ) : (
      <PlugZap className="h-3 w-3" />
    );

  const text =
    status === "ok" ? "API live" : status === "error" ? "API down" : status === "loading" ? "Checking" : "Offline";

  return (
    <span className="badge gap-1 bg-white/10 text-slate-100">
      {icon}
      {text}
    </span>
  );
}
