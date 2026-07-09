"use client";

import { useEffect, useRef } from "react";

/** Real-time mic waveform from the Web Audio analyser. Falls back to a gentle
 * idle animation when no stream is attached (e.g. while the AI is speaking). */
export default function VoiceWaveform({
  analyserRef,
  active,
  bars = 40,
}: {
  analyserRef: React.MutableRefObject<AnalyserNode | null>;
  active: boolean;
  bars?: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const accent = () =>
      getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#0E7C86";

    let t = 0;
    const render = () => {
      const dpr = window.devicePixelRatio || 1;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
        canvas.width = w * dpr;
        canvas.height = h * dpr;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);

      const analyser = analyserRef.current;
      const color = accent();
      const barW = w / bars;
      let freq: number[] | null = null;
      if (analyser) {
        const buf = new Uint8Array(new ArrayBuffer(analyser.frequencyBinCount));
        analyser.getByteFrequencyData(buf);
        freq = Array.from(buf);
      }
      t += 0.08;
      for (let i = 0; i < bars; i++) {
        let amp: number;
        if (freq) {
          const idx = Math.floor((i / bars) * (freq.length * 0.6));
          amp = (freq[idx] / 255) * 0.9 + 0.05;
        } else {
          // idle shimmer
          amp = active ? 0.12 + 0.08 * Math.abs(Math.sin(t + i * 0.5)) : 0.06;
        }
        const barH = Math.max(2, amp * h);
        const x = i * barW + barW * 0.2;
        const y = (h - barH) / 2;
        ctx.fillStyle = color;
        ctx.globalAlpha = 0.35 + amp * 0.65;
        const r = Math.min(barW * 0.3, barH / 2, 3);
        roundRect(ctx, x, y, barW * 0.6, barH, r);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
      rafRef.current = requestAnimationFrame(render);
    };
    render();
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [analyserRef, active, bars]);

  return <canvas ref={canvasRef} className="h-16 w-full" aria-hidden />;
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
