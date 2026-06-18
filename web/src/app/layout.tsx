import type { Metadata, Viewport } from "next";
import { Vazirmatn } from "next/font/google";
import "./globals.css";
import { AuthProvider } from "@/lib/auth";
import { SiteShell } from "@/components/SiteShell";

const vazir = Vazirmatn({
  subsets: ["arabic"],
  variable: "--font-vazir",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Sepid Exchange | صرافی سپید",
  description: "مکمل وب ربات Sepid Exchange — آگهی یورو، پیشنهاد، معامله",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  minimumScale: 1,
  themeColor: "#06070d",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fa" dir="rtl" className={`${vazir.variable} overflow-x-clip`}>
      <body className="min-h-screen min-w-0 overflow-x-clip font-[family-name:var(--font-vazir)]">
        <AuthProvider>
          <SiteShell>{children}</SiteShell>
        </AuthProvider>
      </body>
    </html>
  );
}
