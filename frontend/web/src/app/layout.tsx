import type { Metadata } from "next";
import Link from "next/link";
import { Space_Grotesk } from "next/font/google";
import "./globals.css";
import { ApiStatusChip } from "@/components/ApiStatusChip";

const spaceGrotesk = Space_Grotesk({ subsets: ["latin"], variable: "--font-space-grotesk" });

export const metadata: Metadata = {
  title: "AI Secretary | Employee Hub",
  description: "Chat, requests, documents, and approvals connected to core-ai backend",
};

const nav = [
  { href: "/chat", label: "Employee Chat" },
  { href: "/requests", label: "My Requests" },
  { href: "/documents", label: "My Documents" },
  { href: "/approvals", label: "Department Approvals" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${spaceGrotesk.variable}`}>
      <body className="bg-transparent text-slate-100">
        <div className="sticky top-0 z-50 border-b border-white/5 bg-slate-900/70 backdrop-blur-xl">
          <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
            <Link href="/" className="flex items-center gap-2 text-lg font-semibold">
              <span className="h-3 w-3 rounded-full bg-gradient-to-r from-emerald-400 to-cyan-400 shadow-glow" />
              AI Secretary
            </Link>
            <nav className="flex items-center gap-2 text-sm">
              {nav.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className="rounded-lg px-3 py-2 text-slate-200 transition hover:bg-white/10"
                >
                  {item.label}
                </Link>
              ))}
              <ApiStatusChip />
            </nav>
          </div>
        </div>
        <main className="mx-auto flex min-h-screen max-w-6xl flex-col gap-6 px-4 pb-14 pt-8">
          {children}
        </main>
      </body>
    </html>
  );
}
