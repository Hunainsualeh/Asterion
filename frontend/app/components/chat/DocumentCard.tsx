import Card from "@/app/components/ui/Card";
import Markdown from "@/app/components/ui/Markdown";
import type { QARound } from "@/lib/api";

export default function DocumentCard({ title, doc, qa }: { title: string; doc: string; qa?: QARound[] }) {
  return (
    <div className="ml-11">
      <Card title={`${title} document`} subtitle="Generated for this project" defaultOpen={false} accent="bg-accent/60">
        {qa && qa.length > 0 && (
          <div className="mb-4 space-y-2 border-b border-border-soft pb-4">
            {qa.map((round, i) => (
              <div key={i} className="text-xs text-text-tertiary">
                <p className="font-medium text-text-secondary">Q: {round.questions.join(" / ")}</p>
                <p>A: {round.answer}</p>
              </div>
            ))}
          </div>
        )}
        <Markdown>{doc}</Markdown>
      </Card>
    </div>
  );
}
