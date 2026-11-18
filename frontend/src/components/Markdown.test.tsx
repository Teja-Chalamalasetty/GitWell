import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { safeUrl } from "../lib/safeUrl";
import { Markdown } from "./Markdown";

describe("safeUrl", () => {
  it.each([
    ["https://example.com/a?b=c", "https://example.com/a?b=c"],
    ["http://example.com", "http://example.com"],
    ["mailto:me@example.com", "mailto:me@example.com"],
    ["#section", "#section"],
    ["/relative/path", "/relative/path"],
    ["javascript:alert(1)", ""],
    ["JaVaScRiPt:alert(1)", ""],
    ["  javascript:alert(1)", ""],
    ["data:text/html,<script>alert(1)</script>", ""],
    ["vbscript:msgbox(1)", ""],
    ["file:///etc/passwd", ""],
    ["ftp://example.com/x", ""],
    ["not a url", ""],
  ])("%s -> %j", (input, expected) => {
    expect(safeUrl(input)).toBe(expected);
  });
});

describe("Markdown", () => {
  it("renders basic markdown", () => {
    render(<Markdown>{"**bold** and `code`\n\n- one\n- two"}</Markdown>);
    expect(screen.getByText("bold").tagName).toBe("STRONG");
    expect(screen.getByText("code").tagName).toBe("CODE");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("does NOT render raw HTML (no rehype-raw): it never becomes elements", () => {
    const { container } = render(
      <Markdown>
        {'<script>window.__pwned = 1</script><img src=x onerror="window.__pwned=1"><b>x</b>'}
      </Markdown>,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it("opens http(s) links safely in a new tab", () => {
    render(<Markdown>{"[docs](https://example.com/docs)"}</Markdown>);
    const link = screen.getByRole("link", { name: "docs" });
    expect(link).toHaveAttribute("href", "https://example.com/docs");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
    expect(link.getAttribute("rel")).toContain("noreferrer");
  });

  it("allows mailto links", () => {
    render(<Markdown>{"[mail](mailto:me@example.com)"}</Markdown>);
    expect(screen.getByRole("link", { name: "mail" })).toHaveAttribute(
      "href",
      "mailto:me@example.com",
    );
  });

  it.each([
    "javascript:alert(1)",
    "data:text/html;base64,PHNjcmlwdD4=",
    "vbscript:x",
    "file:///etc/passwd",
  ])("drops the dangerous link %s", (href) => {
    render(<Markdown>{`[click](${href})`}</Markdown>);
    const link = screen.getByText("click").closest("a");
    expect(link?.getAttribute("href") ?? "").toBe("");
  });

  it("renders fenced code blocks as text", () => {
    const { container } = render(<Markdown>{"```yaml\nrun: <script>x</script>\n```"}</Markdown>);
    expect(container.querySelector("pre code")?.textContent).toContain("<script>x</script>");
    expect(container.querySelector("script")).toBeNull();
  });
});
