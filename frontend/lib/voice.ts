// Voice system shared model: the action catalog, default phrase mappings,
// wake words, tunable settings, and the (deterministic, offline) matchers that
// turn a spoken transcript into either a command or a conversational turn.
//
// The whole voice stack is client-side: Web Speech API for recognition,
// speechSynthesis for TTS, Web Audio for the waveform. Nothing here needs the
// backend — commands execute locally for zero latency, and only genuine
// conversation is sent to the AI.

// ============================================================================
// FEATURE FLAGS — the two switches that turn the voice stack off.
//
// Both are currently OFF. Nothing is deleted; every component below still
// compiles and every test still passes. Flip a flag back to `true` to restore
// that half of the feature — no other edit is required.
//
//   VOICE_ENABLED      The whole stack: mic button, voice overlay, speech
//                      recognition, TTS, the Settings › Voice tab. When false,
//                      VoiceProvider short-circuits before any hook runs, so
//                      getUserMedia is never called and no recognition object
//                      is ever constructed.
//
//   WAKE_WORD_ENABLED  Background "Hey Friday" standby listening only. This is
//                      the one that held the microphone open continuously and
//                      streamed audio to Google's speech endpoint whenever the
//                      tab was focused. It is force-clamped off in
//                      useVoiceConfig regardless of what a user's saved
//                      localStorage config says, so re-enabling VOICE_ENABLED
//                      alone brings back push-to-talk and the mic button
//                      WITHOUT resurrecting the always-on microphone.
//
// Turning voice back on: set VOICE_ENABLED = true. Turning the always-on mic
// back on as well: also set WAKE_WORD_ENABLED = true.
// ============================================================================
export const VOICE_ENABLED = false;
export const WAKE_WORD_ENABLED = false;

export type VoiceActionId =
  | "open_settings"
  | "close_overlay"
  | "open_tasks"
  | "open_notifications"
  | "open_profile"
  | "new_chat"
  | "delete_chat"
  | "delete_all_chats"
  | "toggle_theme"
  | "dark_mode"
  | "light_mode"
  | "toggle_sidebar"
  | "go_home"
  | "refresh_page"
  | "stop_agent"
  | "start_agent"
  | "search_conversations"
  | "open_new_tab"
  | "close_tab"
  | "go_back"
  | "open_google"
  | "stop_voice";

export interface VoiceActionMeta {
  id: VoiceActionId;
  label: string;
  /** True when the action only makes sense on a specific page (e.g. a run). */
  pageScoped?: boolean;
  /** Short line the assistant speaks/echoes when it runs. */
  say: string;
}

export const VOICE_ACTIONS: VoiceActionMeta[] = [
  { id: "open_settings", label: "Open Settings", say: "Opening settings" },
  { id: "close_overlay", label: "Close Panel / Settings", say: "Closing" },
  { id: "open_tasks", label: "Open Tasks", say: "Opening your tasks" },
  { id: "open_notifications", label: "Open Notifications", say: "Here are your notifications" },
  { id: "open_profile", label: "Open Profile", say: "Opening your profile" },
  { id: "new_chat", label: "Open New Chat", say: "Starting a new chat" },
  { id: "delete_chat", label: "Delete Current Chat", say: "Deleting this chat" },
  { id: "delete_all_chats", label: "Delete All Chats", say: "Deleting all chats" },
  { id: "toggle_theme", label: "Toggle Dark Mode", say: "Switching the theme" },
  { id: "dark_mode", label: "Dark Mode", say: "Dark mode on" },
  { id: "light_mode", label: "Light Mode", say: "Light mode on" },
  { id: "toggle_sidebar", label: "Toggle Sidebar", say: "Toggling the sidebar" },
  { id: "go_home", label: "Open Dashboard / History", say: "Going home" },
  { id: "refresh_page", label: "Refresh Current Page", say: "Refreshing" },
  { id: "stop_agent", label: "Stop Agent Execution", pageScoped: true, say: "Stopping the run" },
  { id: "start_agent", label: "Start / Retry Agent", pageScoped: true, say: "Running that again" },
  { id: "search_conversations", label: "Search Conversations", say: "Search isn't available yet" },
  { id: "open_new_tab", label: "Open New Tab", say: "Opening a new tab" },
  { id: "close_tab", label: "Close Current Tab", say: "Closing this tab" },
  { id: "go_back", label: "Go Back", say: "Going back" },
  { id: "open_google", label: "Open Google", say: "Opening Google" },
  { id: "stop_voice", label: "Exit Voice Mode", say: "Voice mode off" },
];

export const VOICE_ACTION_LABEL: Record<VoiceActionId, string> = Object.fromEntries(
  VOICE_ACTIONS.map((a) => [a.id, a.label]),
) as Record<VoiceActionId, string>;

export interface VoiceCommand {
  id: string; // stable id for editing
  action: VoiceActionId;
  phrases: string[];
  enabled: boolean;
}

const DEFAULT_PHRASES: Record<VoiceActionId, string[]> = {
  open_settings: ["open settings", "settings", "configure app", "open control panel"],
  close_overlay: ["close settings", "close panel", "close that"],
  open_tasks: ["open tasks", "show tasks", "show my tasks", "my tasks"],
  open_notifications: ["open notifications", "show notifications", "notifications"],
  open_profile: ["open profile", "my profile", "profile"],
  new_chat: ["new chat", "open new chat", "start a new chat", "create new chat"],
  delete_chat: ["delete this chat", "delete current chat", "delete this conversation"],
  delete_all_chats: ["delete all chats", "clear all chats", "delete all projects"],
  toggle_theme: ["toggle dark mode", "switch theme", "toggle theme"],
  dark_mode: ["dark mode", "turn on dark mode", "night mode"],
  light_mode: ["light mode", "turn on light mode", "day mode"],
  toggle_sidebar: ["toggle sidebar", "collapse sidebar", "hide sidebar", "show sidebar"],
  go_home: ["open dashboard", "go home", "open history", "home", "go to dashboard"],
  refresh_page: ["refresh page", "refresh", "reload page", "reload"],
  stop_agent: ["stop agent", "stop execution", "stop the run", "cancel run", "stop"],
  start_agent: ["start agent", "retry", "run again", "try again"],
  search_conversations: ["search conversations", "search chats", "find a conversation"],
  open_new_tab: ["open a new tab", "open new tab", "new tab"],
  close_tab: ["close this tab", "close tab", "close current tab"],
  go_back: ["go back", "navigate back", "previous page"],
  open_google: ["open google", "go to google", "search google"],
  stop_voice: [
    "stop listening",
    "exit voice mode",
    "stop voice",
    "goodbye friday",
    "never mind",
    "end session",
    "terminate session",
  ],
};

export const DEFAULT_WAKE_WORDS = [
  "hey friday",
  "hello friday",
  "wake up",
  "open voice mode",
  "start listening",
];

export function defaultCommands(): VoiceCommand[] {
  return VOICE_ACTIONS.map((a) => ({
    id: `cmd-${a.id}`,
    action: a.id,
    phrases: [...(DEFAULT_PHRASES[a.id] ?? [])],
    enabled: true,
  }));
}

export interface VoiceSettings {
  recognitionLang: string; // BCP-47, e.g. "en-US"
  wakeWordEnabled: boolean; // background wake-word listening
  continuous: boolean; // keep listening after each reply
  pushToTalk: boolean; // only listen while the button/space is held
  autoTimeoutMs: number; // silence timeout before auto-stop
  ttsEnabled: boolean;
  ttsVoiceURI: string; // preferred voice
  ttsRate: number; // 0.5–2 (response speed)
  ttsPitch: number; // 0–2
  sensitivity: number; // 0–1, interim confidence gate (looser = more sensitive)
  wakeSensitivity: number; // 0–1, fuzziness of wake matching
  soundCues: boolean;
  noiseCancellation: boolean; // getUserMedia noiseSuppression constraint
}

export const DEFAULT_VOICE_SETTINGS: VoiceSettings = {
  recognitionLang: "en-US",
  // Follows the flag: when wake-word support ships again this default decides
  // whether it's opt-in or opt-out. useVoiceConfig clamps it either way.
  wakeWordEnabled: WAKE_WORD_ENABLED,
  continuous: true,
  pushToTalk: false,
  autoTimeoutMs: 8000,
  ttsEnabled: true,
  ttsVoiceURI: "",
  ttsRate: 1,
  ttsPitch: 1,
  sensitivity: 0.5,
  wakeSensitivity: 0.6,
  soundCues: true,
  noiseCancellation: true,
};

export const RECOGNITION_LANGS: { code: string; label: string }[] = [
  { code: "en-US", label: "English (US)" },
  { code: "en-GB", label: "English (UK)" },
  { code: "ur-PK", label: "Urdu" },
  { code: "hi-IN", label: "Hindi" },
  { code: "ar-SA", label: "Arabic" },
  { code: "de-DE", label: "German" },
  { code: "es-ES", label: "Spanish" },
  { code: "fr-FR", label: "French" },
  { code: "zh-CN", label: "Chinese (Mandarin)" },
];

// --------------------------------------------------------------------------- matching
export function normalize(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, "")
    .replace(/\s+/g, " ")
    .trim();
}

/** Does the (normalized) transcript match a phrase as a command? Single-word
 * phrases must be exact or at the start/end to avoid firing on stray words;
 * multi-word phrases may appear anywhere. */
function phraseHit(norm: string, phrase: string): boolean {
  const p = normalize(phrase);
  if (!p) return false;
  if (norm === p) return true;
  if (norm.startsWith(p + " ") || norm.endsWith(" " + p)) return true;
  if (p.includes(" ") && norm.includes(p)) return true;
  return false;
}

export function matchCommand(transcript: string, commands: VoiceCommand[]): VoiceCommand | null {
  const norm = normalize(transcript);
  if (!norm) return null;
  // Prefer the longest matching phrase (most specific) across enabled commands.
  let best: { cmd: VoiceCommand; len: number } | null = null;
  for (const cmd of commands) {
    if (!cmd.enabled) continue;
    for (const phrase of cmd.phrases) {
      if (phraseHit(norm, phrase)) {
        const len = normalize(phrase).length;
        if (!best || len > best.len) best = { cmd, len };
      }
    }
  }
  return best?.cmd ?? null;
}

export function matchWake(transcript: string, wakeWords: string[]): string | null {
  const norm = normalize(transcript);
  for (const w of wakeWords) {
    const nw = normalize(w);
    if (nw && norm.includes(nw)) return w;
  }
  return null;
}

/** Strip a leading wake word from a transcript so "hey friday open settings"
 * still resolves the command after waking. */
export function stripWake(transcript: string, wakeWords: string[]): string {
  let norm = normalize(transcript);
  for (const w of wakeWords) {
    const nw = normalize(w);
    if (nw && norm.startsWith(nw)) {
      norm = norm.slice(nw.length).trim();
      break;
    }
  }
  return norm;
}
