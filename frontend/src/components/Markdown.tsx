import ReactMarkdown from "react-markdown";
import { safeUrl } from "../lib/safeUrl";

// PRD S13: rendered WITHOUT rehype-raw (raw HTML is shown as text, never executed); only
// http, https and mailto links survive (see lib/safeUrl.ts).
export function Markdown({ children }: { children: string }) {
  return (
    <div className="prose-sm max-w-none space-y-2 text-sm leading-relaxed [&_code]:rounded [&_code]:bg-zinc-100 [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-[0.85em] dark:[&_code]:bg-zinc-800 [&_h1]:text-lg [&_h1]:font-semibold [&_h2]:text-base [&_h2]:font-semibold [&_li]:ml-5 [&_li]:list-disc [&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:bg-zinc-100 [&_pre]:p-3 dark:[&_pre]:bg-zinc-800 [&_pre_code]:bg-transparent [&_pre_code]:p-0">
      <ReactMarkdown
        urlTransform={safeUrl}
        components={{
          a: ({ href, children: label }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer nofollow"
              className="text-blue-600 underline dark:text-blue-400"
            >
              {label}
            </a>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
