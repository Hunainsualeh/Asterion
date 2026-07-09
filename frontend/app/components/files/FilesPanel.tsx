"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Code2, Copy, ExternalLink, Eye, FileText, FolderOpen, Lock, RefreshCw, Save, X } from "lucide-react";
import Markdown from "@/app/components/ui/Markdown";
import {
  listArtifacts,
  rawArtifactUrl,
  readArtifact,
  writeArtifact,
  type ArtifactEntry,
} from "@/lib/api";
import IconButton from "@/app/components/ui/IconButton";
import Button from "@/app/components/ui/Button";

type Root = "repo" | "docs";
type ViewMode = "code" | "preview";

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function previewKind(path: string): "markdown" | "html" | null {
  const ext = path.toLowerCase().split(".").pop() ?? "";
  if (ext === "md" || ext === "markdown") return "markdown";
  if (ext === "html" || ext === "htm") return "html";
  return null;
}

/** Everything the agents produced: docs (scope/architecture/results/steps)
 * and the generated code tree. Repo files are editable in place; markdown and
 * HTML open in a rendered preview (toggle back to the code view any time). */
export default function FilesPanel({ projectId, open, onClose }: { projectId: string; open: boolean; onClose: () => void }) {
  const [docs, setDocs] = useState<ArtifactEntry[]>([]);
  const [repo, setRepo] = useState<ArtifactEntry[]>([]);
  const [selected, setSelected] = useState<{ root: Root; path: string } | null>(null);
  const [content, setContent] = useState("");
  const [dirty, setDirty] = useState(false);
  const [binary, setBinary] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [view, setView] = useState<ViewMode>("code");
  const [saved, setSaved] = useState(false);
  const [copied, setCopied] = useState(false);
  // Bumped after every save so an open HTML preview reloads the new file.
  const [previewTick, setPreviewTick] = useState(0);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await listArtifacts(projectId);
      setDocs(res.docs);
      setRepo(res.repo);
      setStatus(null);
    } catch {
      setStatus("Couldn't load the file list.");
    }
  }, [projectId]);

  useEffect(() => {
    if (open) refresh();
  }, [open, refresh]);

  useEffect(() => () => {
    if (flashTimer.current) clearTimeout(flashTimer.current);
  }, []);

  function flash(setter: (v: boolean) => void) {
    setter(true);
    if (flashTimer.current) clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => {
      setSaved(false);
      setCopied(false);
    }, 1600);
  }

  async function openFile(root: Root, path: string) {
    setSelected({ root, path });
    setLoading(true);
    setDirty(false);
    setSaved(false);
    setCopied(false);
    // Rendered output is what you want first for md/html; code is one click away.
    setView(previewKind(path) ? "preview" : "code");
    try {
      const res = await readArtifact(projectId, root, path);
      setContent(res.binary ? "" : res.content);
      setBinary(res.binary);
      setStatus(res.truncated ? "File truncated for display." : null);
    } catch {
      setStatus("Couldn't read that file.");
    } finally {
      setLoading(false);
    }
  }

  async function save() {
    if (!selected || selected.root !== "repo" || !dirty) return;
    try {
      await writeArtifact(projectId, selected.path, content);
      setDirty(false);
      setStatus(null);
      setPreviewTick((t) => t + 1);
      flash(setSaved);
    } catch (err) {
      setStatus(err instanceof Error ? `Save failed: ${err.message}` : "Save failed.");
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(content);
      flash(setCopied);
    } catch {
      setStatus("Couldn't copy — your browser blocked clipboard access.");
    }
  }

  if (!open) return null;

  const kind = selected ? previewKind(selected.path) : null;
  const editable = selected?.root === "repo";

  const section = (label: string, root: Root, entries: ArtifactEntry[]) => (
    <div>
      <p className="flex items-center gap-1.5 px-1 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wider text-text-tertiary">
        <FolderOpen size={12} /> {label}
      </p>
      {entries.length === 0 && <p className="px-1 text-xs text-text-tertiary">Nothing here yet.</p>}
      <ul>
        {entries.map((f) => (
          <li key={`${root}:${f.path}`}>
            <button
              type="button"
              onClick={() => openFile(root, f.path)}
              className={`flex w-full items-center gap-1.5 truncate rounded-md px-1.5 py-1 text-left font-mono text-[11px] transition-colors ${
                selected?.root === root && selected.path === f.path
                  ? "bg-accent-soft/60 text-text-primary"
                  : "text-text-secondary hover:bg-accent-soft/30"
              }`}
            >
              <FileText size={11} className="shrink-0" />
              <span className="min-w-0 flex-1 truncate">{f.path}</span>
              <span className="shrink-0 text-text-tertiary">{fmtSize(f.size)}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );

  return (
    <>
      <div className="fixed inset-0 z-30 bg-black/20 backdrop-blur-[1px] lg:hidden" onClick={onClose} />
      <aside className="fixed inset-y-0 right-0 z-40 flex h-full w-full max-w-2xl shrink-0 flex-col border-l border-border bg-surface shadow-xl lg:relative lg:z-auto lg:max-w-xl lg:shadow-none">
        <div className="flex items-center gap-2 border-b border-border px-4 py-3.5">
          <span className="flex-1 text-xs font-semibold uppercase tracking-wider text-text-secondary">Files</span>
          <IconButton onClick={refresh} aria-label="Refresh files">
            <RefreshCw size={14} />
          </IconButton>
          <IconButton onClick={onClose} aria-label="Close files panel">
            <X size={16} />
          </IconButton>
        </div>

        <div className="flex min-h-0 flex-1">
          <div className="w-56 shrink-0 overflow-y-auto scroll-thin border-r border-border-soft px-2 py-1">
            {section("Documents", "docs", docs)}
            {section("Generated code", "repo", repo)}
          </div>

          <div className="flex min-w-0 flex-1 flex-col">
            {selected ? (
              <>
                <div className="flex items-center gap-1.5 border-b border-border-soft px-3 py-2">
                  <span className="min-w-0 flex-1 truncate font-mono text-xs text-text-secondary">
                    {selected.root}/{selected.path}
                  </span>
                  {!editable && (
                    <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-surface-2 px-2 py-0.5 text-[10px] font-medium text-text-tertiary">
                      <Lock size={9} /> read-only
                    </span>
                  )}
                  {kind === "html" && !binary && (
                    <IconButton
                      onClick={() => window.open(rawArtifactUrl(projectId, selected.root, selected.path), "_blank")}
                      aria-label="Open preview in a new tab (full screen)"
                    >
                      <ExternalLink size={14} />
                    </IconButton>
                  )}
                  {kind && !binary && (
                    <div className="flex shrink-0 overflow-hidden rounded-lg border border-border">
                      <button
                        type="button"
                        onClick={() => setView("preview")}
                        className={`inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium transition-colors ${
                          view === "preview" ? "bg-accent-soft text-accent" : "text-text-secondary hover:bg-surface-2"
                        }`}
                      >
                        <Eye size={11} /> Preview
                      </button>
                      <button
                        type="button"
                        onClick={() => setView("code")}
                        className={`inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium transition-colors ${
                          view === "code" ? "bg-accent-soft text-accent" : "text-text-secondary hover:bg-surface-2"
                        }`}
                      >
                        <Code2 size={11} /> Code
                      </button>
                    </div>
                  )}
                  {!binary && (
                    <Button variant="ghost" size="sm" onClick={copy}>
                      <span className="inline-flex items-center gap-1.5">
                        {copied ? <Check size={12} className="text-success" /> : <Copy size={12} />}
                        {copied ? "Copied" : "Copy"}
                      </span>
                    </Button>
                  )}
                  {editable && (
                    <Button size="sm" onClick={save} disabled={!dirty && !saved}>
                      <span className="inline-flex items-center gap-1.5">
                        {saved ? <Check size={12} /> : <Save size={12} />}
                        {saved ? "Saved" : "Save"}
                      </span>
                    </Button>
                  )}
                </div>
                {loading ? (
                  <p className="p-4 text-sm text-text-tertiary">Loading...</p>
                ) : binary ? (
                  <p className="p-4 text-sm text-text-tertiary">Binary file — can&apos;t display.</p>
                ) : view === "preview" && kind === "html" ? (
                  <iframe
                    key={`${selected.root}/${selected.path}#${previewTick}`}
                    src={rawArtifactUrl(projectId, selected.root, selected.path)}
                    sandbox="allow-scripts allow-forms"
                    title={selected.path}
                    className="min-h-0 flex-1 bg-white"
                  />
                ) : view === "preview" && kind === "markdown" ? (
                  <div className="min-h-0 flex-1 overflow-y-auto scroll-thin p-4">
                    <Markdown>{content}</Markdown>
                  </div>
                ) : (
                  <textarea
                    value={content}
                    onChange={(e) => {
                      setContent(e.target.value);
                      setDirty(true);
                      setSaved(false);
                    }}
                    onKeyDown={(e) => {
                      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
                        e.preventDefault();
                        save();
                      }
                    }}
                    readOnly={!editable}
                    spellCheck={false}
                    className="min-h-0 flex-1 resize-none bg-transparent p-3 font-mono text-xs leading-relaxed text-text-primary outline-none"
                  />
                )}
              </>
            ) : (
              <div className="flex flex-1 items-center justify-center">
                <p className="text-sm text-text-tertiary">Select a file to view it.</p>
              </div>
            )}
            {status && <p className="border-t border-border-soft px-3 py-1.5 text-xs text-text-tertiary">{status}</p>}
          </div>
        </div>
      </aside>
    </>
  );
}
