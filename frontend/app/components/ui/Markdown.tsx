"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Check, Copy } from "lucide-react";
import { PrismLight as SyntaxHighlighter } from "react-syntax-highlighter";
import oneDark from "react-syntax-highlighter/dist/cjs/styles/prism/one-dark";
import bash from "react-syntax-highlighter/dist/cjs/languages/prism/bash";
import c from "react-syntax-highlighter/dist/cjs/languages/prism/c";
import cpp from "react-syntax-highlighter/dist/cjs/languages/prism/cpp";
import csharp from "react-syntax-highlighter/dist/cjs/languages/prism/csharp";
import css from "react-syntax-highlighter/dist/cjs/languages/prism/css";
import go from "react-syntax-highlighter/dist/cjs/languages/prism/go";
import java from "react-syntax-highlighter/dist/cjs/languages/prism/java";
import javascript from "react-syntax-highlighter/dist/cjs/languages/prism/javascript";
import json from "react-syntax-highlighter/dist/cjs/languages/prism/json";
import jsx from "react-syntax-highlighter/dist/cjs/languages/prism/jsx";
import markup from "react-syntax-highlighter/dist/cjs/languages/prism/markup";
import python from "react-syntax-highlighter/dist/cjs/languages/prism/python";
import rust from "react-syntax-highlighter/dist/cjs/languages/prism/rust";
import sql from "react-syntax-highlighter/dist/cjs/languages/prism/sql";
import tsx from "react-syntax-highlighter/dist/cjs/languages/prism/tsx";
import typescript from "react-syntax-highlighter/dist/cjs/languages/prism/typescript";
import yaml from "react-syntax-highlighter/dist/cjs/languages/prism/yaml";

const LANGS: Record<string, unknown> = {
  bash, sh: bash, shell: bash, zsh: bash,
  c, cpp, "c++": cpp, csharp, cs: csharp,
  css, go, golang: go, java,
  javascript, js: javascript, mjs: javascript,
  json, jsx,
  markup, html: markup, xml: markup, svg: markup,
  python, py: python,
  rust, rs: rust, sql,
  tsx, typescript, ts: typescript,
  yaml, yml: yaml,
};
for (const [alias, lang] of Object.entries(LANGS)) {
  SyntaxHighlighter.registerLanguage(alias, lang);
}

function CodeBlock({ lang, code }: { lang: string; code: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard blocked — nothing sensible to do */
    }
  }

  return (
    <div className="not-prose my-3 overflow-hidden rounded-xl border border-border-soft bg-[#282c34] shadow-sm">
      <div className="flex items-center justify-between border-b border-white/10 px-3 py-1">
        <span className="text-[11px] font-medium lowercase tracking-wider text-white/40">{lang || "code"}</span>
        <button
          type="button"
          onClick={copy}
          className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] font-medium text-white/50 transition-colors hover:bg-white/10 hover:text-white/90"
        >
          {copied ? <Check size={12} className="text-success" /> : <Copy size={12} />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <div className="overflow-x-auto scroll-thin">
        <SyntaxHighlighter
          language={LANGS[lang] ? lang : "markup"}
          style={oneDark as Record<string, React.CSSProperties>}
          customStyle={{ margin: 0, background: "transparent", padding: "12px 14px", fontSize: "12px", lineHeight: 1.6 }}
          codeTagProps={{ style: { fontFamily: "var(--font-mono, ui-monospace, monospace)" } }}
        >
          {code}
        </SyntaxHighlighter>
      </div>
    </div>
  );
}

/** Shared markdown renderer for everything the agents say: prose styling
 * plus real code blocks — syntax highlighted, language-labelled, and
 * copyable with one click. */
export default function Markdown({ children }: { children: string }) {
  return (
    <div className="prose prose-sm dark:prose-invert max-w-none prose-headings:text-text-primary prose-p:text-text-secondary prose-li:text-text-secondary prose-strong:text-text-primary">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code(props) {
            const { className, children: kids } = props;
            const text = String(kids ?? "").replace(/\n$/, "");
            const match = /language-([\w+.-]+)/.exec(className || "");
            // Fenced blocks carry a language-* class; bare fences have none
            // but still contain newlines — both get the full code card.
            if (match || text.includes("\n")) {
              return <CodeBlock lang={(match?.[1] ?? "").toLowerCase()} code={text} />;
            }
            return <code className={className}>{kids}</code>;
          },
          // CodeBlock renders its own <pre>; the default wrapper would add
          // prose pre styling (background, padding) around the card.
          pre({ children: kids }) {
            return <>{kids}</>;
          },
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
