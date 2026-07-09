"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  type ReactNode,
} from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAppUI } from "@/hooks/useAppUI";
import { useTheme } from "@/hooks/useTheme";
import { useVoice, type VoiceStatus } from "@/hooks/useVoice";
import { useVoiceConfig } from "@/hooks/useVoiceConfig";
import { deleteAllProjects, deleteProject, startProject } from "@/lib/api";
import { matchCommand, VOICE_ACTIONS, VOICE_ENABLED, type VoiceActionId } from "@/lib/voice";
import VoiceOverlay from "./VoiceOverlay";

interface VoiceControlValue {
  status: VoiceStatus;
  supported: boolean;
  active: boolean; // in a live voice session (listening/thinking/speaking)
  toggle: () => void;
  enter: () => void;
  exit: () => void;
  pttDown: () => void;
  pttUp: () => void;
}

const Ctx = createContext<VoiceControlValue | null>(null);

const AUTOSPEAK_KEY = "asterion:voice-autospeak";

const noop = () => {};

/** What `useVoiceControl()` sees while VOICE_ENABLED is false.
 *
 * `supported: false` is the same signal the hook already gives on a browser
 * without the Web Speech API, so every existing consumer — VoiceButton hides
 * itself, ChatComposer keeps rendering the text box — does the right thing
 * with no extra branching. */
const DISABLED: VoiceControlValue = {
  status: "unsupported",
  supported: false,
  active: false,
  toggle: noop,
  enter: noop,
  exit: noop,
  pttDown: noop,
  pttUp: noop,
};

export function VoiceProvider({ children }: { children: ReactNode }) {
  // Returns before a single hook runs, so no recognition object, no
  // getUserMedia, no TTS, no window listeners. Legal despite the rules of
  // hooks: VOICE_ENABLED is a module constant, so this branch is fixed for the
  // life of the process and hook order can never change between renders.
  if (!VOICE_ENABLED) return <Ctx.Provider value={DISABLED}>{children}</Ctx.Provider>;
  return <VoiceProviderInner>{children}</VoiceProviderInner>;
}

function VoiceProviderInner({ children }: { children: ReactNode }) {
  const { settings, commands, wakeWords } = useVoiceConfig();
  const appUI = useAppUI();
  const { theme, toggle: toggleTheme } = useTheme();
  const router = useRouter();
  const pathname = usePathname();
  const engineRef = useRef<ReturnType<typeof useVoice> | null>(null);
  const replyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const currentPid = useCallback(() => {
    const m = /^\/pipeline\/([^/]+)/.exec(pathname || "");
    return m ? m[1] : null;
  }, [pathname]);

  // ------------------------------------------------ execute a matched command
  const executeAction = useCallback(
    (action: VoiceActionId) => {
      switch (action) {
        case "open_settings":
          appUI.openSettings("general");
          break;
        case "close_overlay":
          appUI.closeSettings();
          appUI.closeNotifications();
          break;
        case "open_tasks":
          appUI.openSettings("tasks");
          break;
        case "open_notifications":
          appUI.openNotifications();
          break;
        case "open_profile":
          appUI.openSettings("profile");
          break;
        case "new_chat":
          router.push("/");
          break;
        case "delete_chat": {
          const pid = currentPid();
          if (pid) {
            deleteProject(pid).catch(() => {});
            appUI.bumpProjects();
            router.push("/");
          }
          break;
        }
        case "delete_all_chats":
          deleteAllProjects().catch(() => {});
          appUI.bumpProjects();
          router.push("/");
          break;
        case "toggle_theme":
          toggleTheme();
          break;
        case "dark_mode":
          if (theme !== "dark") toggleTheme();
          break;
        case "light_mode":
          if (theme !== "light") toggleTheme();
          break;
        case "toggle_sidebar":
          appUI.toggleSidebar();
          break;
        case "go_home":
          router.push("/");
          break;
        case "refresh_page":
          if (typeof window !== "undefined") window.location.reload();
          break;
        case "stop_agent":
        case "start_agent":
          window.dispatchEvent(new CustomEvent("asterion:voice-page-command", { detail: { action } }));
          break;
        case "search_conversations":
          // No search surface yet — spoken hint handled by the action's `say`.
          break;
        case "open_new_tab": {
          if (typeof window === "undefined") break;
          const win = window.open("about:blank", "_blank", "noopener");
          if (!win) {
            // Browsers block window.open() unless it runs inside a direct user
            // gesture (a click), which a voice command isn't — no code-side fix
            // for this, only the browser's own "allow pop-ups" site permission.
            engineRef.current?.speakReply(
              "Your browser's pop-up blocker stopped that. Allow pop-ups for this site to open tabs by voice.",
            );
          }
          break;
        }
        case "close_tab":
          if (typeof window !== "undefined") {
            window.close();
            // window.close() is a no-op (silently, no error) unless this tab
            // was itself opened by script — a browser security rule we can't
            // override. If we're still here after a beat, explain why.
            setTimeout(() => {
              engineRef.current?.speakReply(
                "I can't close this tab — browsers only let a page close tabs it opened itself. Try \"go back\" instead.",
              );
            }, 300);
          }
          break;
        case "go_back":
          if (typeof window !== "undefined") window.history.back();
          break;
        case "open_google": {
          if (typeof window === "undefined") break;
          const win = window.open("https://www.google.com", "_blank", "noopener");
          if (!win) window.location.assign("https://www.google.com");
          break;
        }
        case "stop_voice":
          engineRef.current?.exit();
          break;
      }
    },
    [appUI, router, theme, toggleTheme, currentPid],
  );

  // ------------------------------------------------ handlers for the engine
  const onCommand = useCallback(
    (text: string): boolean => {
      const cmd = matchCommand(text, commands);
      if (!cmd) return false;
      executeAction(cmd.action);
      const meta = VOICE_ACTIONS.find((a) => a.id === cmd.action);
      // These either navigate away immediately (refresh) or give their own
      // situation-specific spoken feedback from inside executeAction.
      const skipsSay: VoiceActionId[] = ["stop_voice", "refresh_page", "open_new_tab", "close_tab"];
      if (meta?.say && !skipsSay.includes(cmd.action)) {
        engineRef.current?.speakReply(meta.say);
      }
      return true;
    },
    [commands, executeAction],
  );

  const onConversation = useCallback(
    (text: string) => {
      if (replyTimer.current) clearTimeout(replyTimer.current);
      const pid = currentPid();
      if (pid) {
        // A chat is open — let its bridge send the message and report the reply.
        window.dispatchEvent(new CustomEvent("asterion:voice-utterance", { detail: { text, pid } }));
      } else {
        // No chat open — start one, then have the new pipeline speak its answer.
        startProject(text)
          .then(({ project_id }) => {
            try {
              sessionStorage.setItem(AUTOSPEAK_KEY, project_id);
            } catch {
              /* ignore */
            }
            router.push(`/pipeline/${project_id}`);
          })
          .catch(() => engineRef.current?.failThinking("I couldn't start that."));
      }
      // Safety: never stay stuck "thinking".
      replyTimer.current = setTimeout(() => engineRef.current?.failThinking("I didn't get a response in time."), 45000);
    },
    [currentPid, router],
  );

  const engine = useVoice(settings, commands, wakeWords, { onCommand, onConversation });
  engineRef.current = engine;

  // Speak AI replies bridged up from the active pipeline.
  //
  // These listeners read engineRef.current instead of closing over `engine`
  // directly, and depend on `[]`/stable values only. `engine` is a brand-new
  // object literal every render, and useVoice's mic-meter re-renders the
  // provider ~60x/sec while listening — depending on `engine` here would tear
  // down and re-add these window listeners at that same rate for the entire
  // duration of every listening turn.
  useEffect(() => {
    const onReply = (e: Event) => {
      const detail = (e as CustomEvent).detail as { text?: string };
      if (replyTimer.current) clearTimeout(replyTimer.current);
      if (detail?.text) engineRef.current?.speakReply(detail.text);
      else engineRef.current?.failThinking();
    };
    window.addEventListener("asterion:voice-reply", onReply);
    return () => window.removeEventListener("asterion:voice-reply", onReply);
  }, []);

  // Keyboard: hold Space for push-to-talk (when enabled), Esc exits.
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      const typing = el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
      const eng = engineRef.current;
      if (!eng) return;
      if (e.key === "Escape" && eng.status !== "off") eng.exit();
      if (settings.pushToTalk && e.code === "Space" && !e.repeat && !typing && eng.status !== "off") {
        e.preventDefault();
        eng.pttDown();
      }
    };
    const up = (e: KeyboardEvent) => {
      if (settings.pushToTalk && e.code === "Space") engineRef.current?.pttUp();
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, [settings.pushToTalk]);

  const active = engine.status === "listening" || engine.status === "thinking" || engine.status === "speaking";

  const toggle = useCallback(() => {
    if (active) engine.exit();
    else engine.enter();
  }, [active, engine]);

  const value = useMemo<VoiceControlValue>(
    () => ({
      status: engine.status,
      supported: engine.supported,
      active,
      toggle,
      enter: engine.enter,
      exit: engine.exit,
      pttDown: engine.pttDown,
      pttUp: engine.pttUp,
    }),
    [engine.status, engine.supported, engine.enter, engine.exit, engine.pttDown, engine.pttUp, active, toggle],
  );

  return (
    <Ctx.Provider value={value}>
      {children}
      <VoiceOverlay engine={engine} />
    </Ctx.Provider>
  );
}

export function useVoiceControl() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useVoiceControl must be used within VoiceProvider");
  return ctx;
}
