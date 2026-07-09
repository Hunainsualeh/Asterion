import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { ThemeProvider, THEME_INIT_SCRIPT } from "@/hooks/useTheme";
import { SettingsProvider } from "@/hooks/useSettings";
import { NotificationsProvider } from "@/hooks/useNotifications";
import { AppUIProvider } from "@/hooks/useAppUI";
import { VoiceConfigProvider } from "@/hooks/useVoiceConfig";
import { VoiceProvider } from "./components/voice/VoiceProvider";
import Sidebar from "./components/sidebar/Sidebar";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Asterion",
  description: "Multi-agent development pipeline",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`} suppressHydrationWarning>
      <head>
        {/* Set the theme class before first paint to avoid a light/dark flash.
            type flips server/client so React doesn't warn about a live <script>,
            and doesn't re-run it on client-side navigations (see AGENTS.md). */}
        <script
          type={typeof window === "undefined" ? "text/javascript" : "text/plain"}
          suppressHydrationWarning
          dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }}
        />
      </head>
      <body className="h-full bg-bg text-text-primary">
        <ThemeProvider>
          <SettingsProvider>
            <NotificationsProvider>
              <VoiceConfigProvider>
                <AppUIProvider>
                  <VoiceProvider>
                    {/* `relative` so absolutely-positioned descendants (e.g. Tailwind's
                        sr-only spans) are contained here instead of stretching the
                        document and giving the page its own scrollbar. */}
                    <div className="app-shell relative flex h-screen overflow-hidden">
                      <Sidebar />
                      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">{children}</div>
                    </div>
                  </VoiceProvider>
                </AppUIProvider>
              </VoiceConfigProvider>
            </NotificationsProvider>
          </SettingsProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
