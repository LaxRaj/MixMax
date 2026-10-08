import type { Metadata } from "next";
import { Archivo, Martian_Mono } from "next/font/google";
import { IdentityProvider } from "@/components/Identity";
import { Nav } from "@/components/Nav";
import "./globals.css";

const body = Archivo({
  subsets: ["latin"],
  variable: "--font-body",
  weight: ["400", "500", "600", "700"],
});

const mono = Martian_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  weight: ["400", "600", "700"],
});

export const metadata: Metadata = {
  title: "MixMax studio",
  description: "Songs in development: listen, leave notes, change settings, upload files.",
};

export const viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover" as const,
  themeColor: "#0b0908",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={`${body.variable} ${mono.variable}`}>
        <IdentityProvider>
          <Nav />
          {children}
        </IdentityProvider>
      </body>
    </html>
  );
}
