"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  matchCommand,
  matchWake,
  normalize,
  stripWake,
  WAKE_WORD_ENABLED,
  type VoiceCommand,
  type VoiceSettings,
} from "@/lib/voice";

// Minimal ambient typing for the Web Speech API (not in the TS DOM lib).
type SR = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((e: any) => void) | null;
  onend: (() => void) | null;
  onerror: ((e: any) => void) | null;
  onstart: (() => void) | null;
};

export type VoiceStatus =
  | "off"
  | "standby" // background wake-word listening
  | "listening" // actively capturing a command/utterance
  | "thinking" // waiting on the AI
  | "speaking" // TTS playing
  | "denied"
  | "unsupported";

export interface VoiceHandlers {
  /** Return true if the transcript was a recognized command (already executed). */
  onCommand: (text: string) => boolean;
  /** Handle a conversational utterance (send to chat). */
  onConversation: (text: string) => void;
  /** Fired when the user asks to leave voice mode. */
  onExit?: () => void;
}

function getSR(): (new () => SR) | null {
  if (typeof window === "undefined") return null;
  return (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition || null;
}

// Background wake-word listening holds the mic open, and Chrome's Web Speech
// API streams that audio to Google — so a literal 24/7 open mic is neither
// cheap nor private. Instead we only listen while the user is actually present:
// pause when the tab is hidden, and suspend after this much inactivity while
// visible (re-armed the instant the user interacts or refocuses the tab).
const STANDBY_IDLE_MS = 15 * 60 * 1000; // 15 min of no interaction → suspend

export function useVoice(
  settings: VoiceSettings,
  commands: VoiceCommand[],
  wakeWords: string[],
  handlers: VoiceHandlers,
) {
  const [status, setStatus] = useState<VoiceStatus>("off");
  const [interim, setInterim] = useState("");
  const [lastFinal, setLastFinal] = useState("");
  const [lastReply, setLastReply] = useState("");
  const [level, setLevel] = useState(0);
  // True when wake-word standby is armed but the mic is intentionally parked
  // (tab hidden or idle) to conserve resources — distinct from "off".
  const [standbyPaused, setStandbyPaused] = useState(false);

  // Detected after mount only: branching on `typeof window` during render makes
  // the client's first paint disagree with the server's (server always sees no
  // window), which is exactly the hydration mismatch React warns about. Start
  // false (matches SSR) and flip it in an effect once we know the real answer.
  const [supported, setSupported] = useState(false);
  useEffect(() => {
    setSupported(!!getSR());
  }, []);

  // Refs so the long-lived recognition callbacks never read stale state.
  const recRef = useRef<SR | null>(null);
  const modeRef = useRef<"off" | "standby" | "active">("off");
  const settingsRef = useRef(settings);
  const commandsRef = useRef(commands);
  const wakeRef = useRef(wakeWords);
  const handlersRef = useRef(handlers);
  const statusRef = useRef<VoiceStatus>("off");
  const analyserRef = useRef<AnalyserNode | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const rafRef = useRef<number | null>(null);
  const silenceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wantRestart = useRef(false);
  const ttsWatchdog = useRef<ReturnType<typeof setTimeout> | null>(null);
  const ttsPoll = useRef<ReturnType<typeof setInterval> | null>(null);
  const idleTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastActivity = useRef<number>(Date.now());
  const standbyPausedRef = useRef(false);

  useEffect(() => {
    settingsRef.current = settings;
  }, [settings]);
  useEffect(() => {
    commandsRef.current = commands;
  }, [commands]);
  useEffect(() => {
    wakeRef.current = wakeWords;
  }, [wakeWords]);
  useEffect(() => {
    handlersRef.current = handlers;
  }, [handlers]);

  const setStat = useCallback((s: VoiceStatus) => {
    statusRef.current = s;
    setStatus(s);
  }, []);

  // ---------------------------------------------------------------- sound cues
  const cue = useCallback((kind: "start" | "stop" | "ok") => {
    if (!settingsRef.current.soundCues) return;
    try {
      const ctx = audioCtxRef.current ?? new (window.AudioContext || (window as any).webkitAudioContext)();
      audioCtxRef.current = ctx;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      const now = ctx.currentTime;
      const freq = kind === "start" ? 660 : kind === "stop" ? 380 : 880;
      osc.frequency.value = freq;
      osc.type = "sine";
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.exponentialRampToValueAtTime(0.14, now + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.18);
      osc.connect(gain).connect(ctx.destination);
      osc.start(now);
      osc.stop(now + 0.2);
    } catch {
      /* audio blocked — ignore */
    }
  }, []);

  // ---------------------------------------------------------------- mic meter / waveform
  const stopMeter = useCallback(() => {
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    analyserRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setLevel(0);
  }, []);

  // Called on every re-entry into listening (goActive/resume/pttDown/interrupt),
  // i.e. once per conversational turn in a continuous session. Must tear down
  // the previous getUserMedia stream/analyser first, or every turn leaks a live
  // mic stream and an rAF loop that runs forever — over a long "continuous"
  // session that starves the mic and burns CPU.
  const startMeter = useCallback(async () => {
    stopMeter();
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          noiseSuppression: settingsRef.current.noiseCancellation,
          echoCancellation: true,
          autoGainControl: true,
        },
      });
      streamRef.current = stream;
      const ctx = audioCtxRef.current ?? new (window.AudioContext || (window as any).webkitAudioContext)();
      audioCtxRef.current = ctx;
      const src = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      src.connect(analyser);
      analyserRef.current = analyser;

      const data = new Uint8Array(new ArrayBuffer(analyser.frequencyBinCount));
      const tick = () => {
        // A newer startMeter() call has superseded this loop — stop recursing.
        if (analyserRef.current !== analyser) return;
        analyser.getByteTimeDomainData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i++) {
          const v = (data[i] - 128) / 128;
          sum += v * v;
        }
        setLevel(Math.min(1, Math.sqrt(sum / data.length) * 3));
        rafRef.current = requestAnimationFrame(tick);
      };
      tick();
    } catch {
      /* meter is optional; recognition still works */
    }
  }, [stopMeter]);

  // ---------------------------------------------------------------- TTS
  const stopSpeaking = useCallback(() => {
    try {
      window.speechSynthesis?.cancel();
    } catch {
      /* ignore */
    }
  }, []);

  const armSilence = useCallback(() => {
    if (silenceTimer.current) clearTimeout(silenceTimer.current);
    if (settingsRef.current.pushToTalk) return;
    const ms = settingsRef.current.autoTimeoutMs;
    if (!ms || settingsRef.current.continuous) return;
    silenceTimer.current = setTimeout(() => {
      if (modeRef.current === "active" && statusRef.current === "listening") stopListening();
    }, ms);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---------------------------------------------------------------- recognition
  const buildRecognition = useCallback((): SR | null => {
    const Ctor = getSR();
    if (!Ctor) return null;
    const rec = new Ctor();
    rec.continuous = true;
    rec.interimResults = true;
    rec.maxAlternatives = 1;
    rec.lang = settingsRef.current.recognitionLang || "en-US";

    rec.onresult = (e: any) => {
      let interimText = "";
      let finalText = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) finalText += r[0].transcript;
        else interimText += r[0].transcript;
      }
      if (interimText) {
        setInterim(interimText);
        armSilence();
      }
      if (finalText.trim()) {
        setInterim("");
        handleFinal(finalText.trim());
      }
    };
    rec.onend = () => {
      // Chrome ends recognition on silence; keep it alive while we should listen.
      if (wantRestart.current && modeRef.current !== "off" && statusRef.current !== "speaking") {
        try {
          rec.start();
        } catch {
          /* already started */
        }
      }
    };
    rec.onerror = (e: any) => {
      if (e?.error === "not-allowed" || e?.error === "service-not-allowed") {
        wantRestart.current = false;
        modeRef.current = "off";
        setStat("denied");
      }
    };
    return rec;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const startRecognition = useCallback(() => {
    if (!recRef.current) recRef.current = buildRecognition();
    const rec = recRef.current;
    if (!rec) {
      setStat("unsupported");
      return;
    }
    rec.lang = settingsRef.current.recognitionLang || "en-US";
    wantRestart.current = true;
    try {
      rec.start();
    } catch {
      // Usually just "already started" — harmless. But if the recognition
      // object is actually wedged (seen after rapid stop/start cycles), a
      // silently-swallowed throw here leaves status saying "listening" while
      // the mic is dead, and only a manual toggle off/on used to recover.
      // Rebuild a fresh instance and retry once instead of giving up.
      recRef.current = null;
      setTimeout(() => {
        if (!wantRestart.current) return;
        const fresh = buildRecognition();
        recRef.current = fresh;
        try {
          fresh?.start();
        } catch {
          /* still failing — next explicit user action will retry */
        }
      }, 250);
    }
  }, [buildRecognition, setStat]);

  const stopRecognition = useCallback(() => {
    wantRestart.current = false;
    if (silenceTimer.current) clearTimeout(silenceTimer.current);
    try {
      recRef.current?.stop();
    } catch {
      /* ignore */
    }
  }, []);

  // ---------------------------------------------------------------- transcript handling
  const handleFinal = useCallback((text: string) => {
    const mode = modeRef.current;
    if (mode === "standby") {
      const hit = matchWake(text, wakeRef.current);
      if (!hit) return;
      cue("start");
      const remainder = stripWake(text, wakeRef.current);
      goActive();
      if (remainder && normalize(remainder).length > 1) dispatch(remainder);
      return;
    }
    if (mode === "active") {
      setLastFinal(text);
      dispatch(text);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const dispatch = useCallback((text: string) => {
    const h = handlersRef.current;
    // A command executes locally and (optionally) keeps the conversation going.
    const handled = h.onCommand(text);
    if (handled) {
      armSilence();
      return;
    }
    // Otherwise it's a conversational turn → hand to chat, wait for the reply.
    setStat("thinking");
    stopRecognition();
    h.onConversation(text);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---------------------------------------------------------------- mode transitions
  const goActive = useCallback(() => {
    modeRef.current = "active";
    setStat("listening");
    startMeter();
    startRecognition();
    armSilence();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const enter = useCallback(() => {
    if (!supported) {
      setStat("unsupported");
      return;
    }
    stopSpeaking();
    goActive();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [supported]);

  const stopListening = useCallback(() => {
    stopRecognition();
    stopMeter();
    // Fall back to standby if the user keeps wake-word listening on.
    if (settingsRef.current.wakeWordEnabled) {
      modeRef.current = "standby";
      setStat("standby");
      startRecognition();
    } else {
      modeRef.current = "off";
      setStat("off");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const exit = useCallback(() => {
    if (ttsWatchdog.current) {
      clearTimeout(ttsWatchdog.current);
      ttsWatchdog.current = null;
    }
    if (ttsPoll.current) {
      clearInterval(ttsPoll.current);
      ttsPoll.current = null;
    }
    stopSpeaking();
    stopRecognition();
    stopMeter();
    modeRef.current = "off";
    setStat("off");
    cue("stop");
    handlersRef.current.onExit?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const startStandby = useCallback(() => {
    // Hard gate on the build flag, independent of `settings.wakeWordEnabled`.
    // useVoiceConfig already clamps that setting, but standby is the one path
    // that opens the microphone with no user gesture at all — it gets its own
    // check so a future caller can't reach it by passing settings directly.
    if (!WAKE_WORD_ENABLED) return;
    if (!supported || modeRef.current === "active") return;
    modeRef.current = "standby";
    setStat("standby");
    lastActivity.current = Date.now();
    // If the tab is hidden the moment standby arms (e.g. wake word toggled on
    // from a background tab), park the mic immediately — the visibility handler
    // brings it up when the tab is shown.
    if (typeof document !== "undefined" && document.hidden) {
      standbyPausedRef.current = true;
      setStandbyPaused(true);
      return;
    }
    standbyPausedRef.current = false;
    setStandbyPaused(false);
    startRecognition();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [supported]);

  const stopStandby = useCallback(() => {
    if (modeRef.current !== "standby") return;
    stopRecognition();
    standbyPausedRef.current = false;
    setStandbyPaused(false);
    modeRef.current = "off";
    setStat("off");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Park the standby mic without leaving standby mode (tab hidden or idle).
  const pauseStandby = useCallback(() => {
    if (modeRef.current !== "standby" || standbyPausedRef.current) return;
    standbyPausedRef.current = true;
    setStandbyPaused(true);
    stopRecognition();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-arm the standby mic — only when it makes sense (still in standby, still
  // paused, wake word on, and the tab is actually visible).
  const resumeStandby = useCallback(() => {
    if (modeRef.current !== "standby" || !standbyPausedRef.current) return;
    if (!settingsRef.current.wakeWordEnabled) return;
    if (typeof document !== "undefined" && document.hidden) return;
    standbyPausedRef.current = false;
    setStandbyPaused(false);
    lastActivity.current = Date.now();
    startRecognition();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---------------------------------------------------------------- push to talk
  const pttDown = useCallback(() => {
    stopSpeaking();
    modeRef.current = "active";
    setStat("listening");
    startMeter();
    startRecognition();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const pttUp = useCallback(() => {
    stopRecognition();
    stopMeter();
    // the last interim/final is dispatched by onresult; return to idle
    if (settingsRef.current.wakeWordEnabled) startStandby();
    else {
      modeRef.current = "off";
      setStat("off");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startStandby]);

  // ---------------------------------------------------------------- speak an AI reply
  const speakReply = useCallback((text: string) => {
    const clean = text.replace(/[#*`>_~]/g, "").replace(/\[(.*?)\]\(.*?\)/g, "$1").slice(0, 700);
    setLastReply(text);
    const s = settingsRef.current;
    let resumed = false;
    const clearTimers = () => {
      if (ttsWatchdog.current) {
        clearTimeout(ttsWatchdog.current);
        ttsWatchdog.current = null;
      }
      if (ttsPoll.current) {
        clearInterval(ttsPoll.current);
        ttsPoll.current = null;
      }
    };
    const resume = () => {
      // onend/onerror, the speaking-flag poll, and the watchdog can all fire —
      // only act once.
      if (resumed) return;
      resumed = true;
      clearTimers();
      if (modeRef.current === "off") return;
      if (s.continuous) {
        setStat("listening");
        startMeter();
        startRecognition();
        armSilence();
      } else stopListening();
    };
    if (!s.ttsEnabled || typeof window === "undefined" || !window.speechSynthesis) {
      resume();
      return;
    }
    setStat("speaking");
    stopRecognition(); // don't transcribe our own voice
    try {
      const synth = window.speechSynthesis;
      const speakNow = () => {
        const u = new SpeechSynthesisUtterance(clean);
        u.rate = s.ttsRate;
        u.pitch = s.ttsPitch;
        u.lang = s.recognitionLang;
        const voices = synth.getVoices();
        const v = voices.find((x) => x.voiceURI === s.ttsVoiceURI);
        if (v) u.voice = v;
        u.onend = resume;
        u.onerror = resume;
        synth.speak(u);
        // Chrome has a long-standing bug where speechSynthesis silently stops
        // (no 'end'/'error' fired) after ~15s, when backgrounded, or even
        // right away for short utterances — which otherwise wedges the
        // session in "speaking" forever (mic stays off) and made back-to-back
        // commands look like voice mode "only runs one command and stops
        // listening". Poll the synth's own `speaking` flag as the primary,
        // fast-path resume signal (fires within ~120ms of real completion)
        // instead of waiting on the unreliable event or a multi-second timer.
        ttsPoll.current = setInterval(() => {
          if (!synth.speaking && !synth.pending) resume();
        }, 120);
        // Keep a generous timeout as the last-resort net in case `speaking`
        // itself gets stuck true (same class of bug).
        const estimateMs = (clean.length / 14) * 1000 * (1 / Math.max(0.5, s.ttsRate)) + 2000;
        ttsWatchdog.current = setTimeout(resume, Math.max(4000, estimateMs));
      };
      // Calling speak() in the same tick as cancel() is a separate known
      // Chrome race that can drop the utterance (and all its events)
      // entirely when the queue was already empty — only cancel when
      // something is actually queued/speaking, and let a clean tick pass
      // before speaking.
      if (synth.speaking || synth.pending) {
        synth.cancel();
        setTimeout(speakNow, 50);
      } else {
        speakNow();
      }
    } catch {
      resume();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const interrupt = useCallback(() => {
    if (ttsWatchdog.current) {
      clearTimeout(ttsWatchdog.current);
      ttsWatchdog.current = null;
    }
    if (ttsPoll.current) {
      clearInterval(ttsPoll.current);
      ttsPoll.current = null;
    }
    stopSpeaking();
    if (modeRef.current !== "off") {
      setStat("listening");
      startMeter();
      startRecognition();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // If the AI never replies, don't get stuck "thinking".
  const failThinking = useCallback((message?: string) => {
    if (statusRef.current !== "thinking") return;
    if (message) setLastReply(message);
    if (settingsRef.current.continuous && modeRef.current !== "off") {
      setStat("listening");
      startRecognition();
    } else stopListening();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Toggle background standby when the setting flips.
  //
  // Also re-runs when `supported` flips (mount → effect fires once with
  // supported still false, since setSupported(true) from the effect above
  // doesn't apply until the next render — without `supported` here, that
  // first call bails out inside startStandby() and, because wakeWordEnabled
  // itself never changes again on a plain page load, standby would never
  // actually start until the user manually re-toggled the setting).
  useEffect(() => {
    if (settings.wakeWordEnabled && statusRef.current === "off") startStandby();
    if (!settings.wakeWordEnabled && statusRef.current === "standby") stopStandby();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings.wakeWordEnabled, supported]);

  // Make always-on wake-word feasible: only hold the mic while the user is
  // present. Pause on tab-hidden, suspend after idle, and re-arm on any
  // interaction or when the tab is refocused. Only wired up while wake word is
  // enabled; active (in-conversation) sessions are never touched by this.
  useEffect(() => {
    if (!supported || !settings.wakeWordEnabled) return;
    const onVisibility = () => {
      if (typeof document === "undefined") return;
      if (document.hidden) pauseStandby();
      else resumeStandby();
    };
    const onActivity = () => {
      lastActivity.current = Date.now();
      if (standbyPausedRef.current) resumeStandby();
    };
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pointerdown", onActivity, { passive: true });
    window.addEventListener("keydown", onActivity);
    window.addEventListener("mousemove", onActivity, { passive: true });
    window.addEventListener("touchstart", onActivity, { passive: true });
    idleTimer.current = setInterval(() => {
      if (
        modeRef.current === "standby" &&
        !standbyPausedRef.current &&
        Date.now() - lastActivity.current > STANDBY_IDLE_MS
      ) {
        pauseStandby();
      }
    }, 30_000);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pointerdown", onActivity);
      window.removeEventListener("keydown", onActivity);
      window.removeEventListener("mousemove", onActivity);
      window.removeEventListener("touchstart", onActivity);
      if (idleTimer.current) {
        clearInterval(idleTimer.current);
        idleTimer.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [supported, settings.wakeWordEnabled, pauseStandby, resumeStandby]);

  // Cleanup on unmount.
  useEffect(() => {
    return () => {
      wantRestart.current = false;
      try {
        recRef.current?.abort();
      } catch {
        /* ignore */
      }
      if (ttsWatchdog.current) clearTimeout(ttsWatchdog.current);
      if (ttsPoll.current) clearInterval(ttsPoll.current);
      if (idleTimer.current) clearInterval(idleTimer.current);
      stopMeter();
      stopSpeaking();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    status,
    interim,
    lastFinal,
    lastReply,
    level,
    supported,
    standbyPaused,
    analyserRef,
    enter,
    exit,
    stopListening,
    startStandby,
    stopStandby,
    pttDown,
    pttUp,
    speakReply,
    stopSpeaking,
    interrupt,
    failThinking,
    setThinking: () => setStat("thinking"),
  };
}
