import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { RuleStatus } from "../api/types";
import { RULE_STATUS } from "../lib/status";
import { ArchivedBadge, DeltaBadge, GradeBadge, ScoreBadge, StatusBadge } from "./Badges";

const STATUSES: [RuleStatus, string, string][] = [
  ["pass", "Pass", "green"],
  ["warn", "Warning", "amber"],
  ["fail", "Fail", "red"],
  ["not_applicable", "N/A", "zinc"],
  ["error", "Error", "purple"],
];

describe("StatusBadge", () => {
  it.each(STATUSES)("%s shows %s in the %s colour", (status, label, colour) => {
    render(<StatusBadge status={status} />);
    const badge = screen.getByText(label);
    expect(badge).toHaveAttribute("data-status", status);
    expect(badge.className).toContain(colour);
  });

  it("uses a distinct colour for every status", () => {
    const classes = new Set(Object.values(RULE_STATUS).map((s) => s.classes));
    expect(classes.size).toBe(5);
  });
});

describe("GradeBadge", () => {
  it.each(["A", "B", "C", "D", "F"])("renders grade %s with an accessible name", (grade) => {
    render(<GradeBadge grade={grade} />);
    const badge = screen.getByLabelText(`Grade ${grade}`);
    expect(badge).toHaveTextContent(grade);
    expect(badge).toHaveAttribute("data-grade", grade);
  });

  it("A is green and F is red", () => {
    render(
      <>
        <GradeBadge grade="A" />
        <GradeBadge grade="F" />
      </>,
    );
    expect(screen.getByLabelText("Grade A").className).toContain("green");
    expect(screen.getByLabelText("Grade F").className).toContain("red");
  });

  it("shows a dash when there is no grade", () => {
    render(<GradeBadge grade={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("falls back to the F style for an unknown grade", () => {
    render(<GradeBadge grade="Z" />);
    expect(screen.getByLabelText("Grade Z").className).toContain("red");
  });
});

describe("ScoreBadge", () => {
  it("shows score and grade", () => {
    render(<ScoreBadge score={82} grade="B" />);
    expect(screen.getByText("82")).toBeInTheDocument();
    expect(screen.getByLabelText("Grade B")).toBeInTheDocument();
    expect(screen.queryByText("Incomplete")).not.toBeInTheDocument();
    expect(screen.queryByText("Partial")).not.toBeInTheDocument();
  });

  it("labels incomplete (rules errored) and partial scans", () => {
    render(<ScoreBadge score={50} grade="D" rulesErrored={2} isPartial />);
    expect(screen.getByText("Incomplete")).toHaveAttribute("title", "2 rule(s) failed to run");
    expect(screen.getByText("Partial")).toBeInTheDocument();
  });

  it("shows a dash for a null score", () => {
    render(<ScoreBadge score={null} grade={null} rulesErrored={1} />);
    expect(screen.getAllByText("—")).toHaveLength(2);
    expect(screen.getByText("Incomplete")).toBeInTheDocument();
  });
});

describe("DeltaBadge", () => {
  it("up is green with an up arrow", () => {
    render(<DeltaBadge delta={7} />);
    const el = screen.getByLabelText("up 7");
    expect(el).toHaveTextContent("▲ 7");
    expect(el.className).toContain("green");
  });

  it("down is red with a down arrow", () => {
    render(<DeltaBadge delta={-12} />);
    const el = screen.getByLabelText("down 12");
    expect(el).toHaveTextContent("▼ 12");
    expect(el.className).toContain("red");
  });

  it("null is a dash", () => {
    render(<DeltaBadge delta={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("zero is neutral", () => {
    render(<DeltaBadge delta={0} />);
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(screen.queryByText(/[▲▼]/)).not.toBeInTheDocument();
  });
});

describe("ArchivedBadge", () => {
  it("says Archived", () => {
    render(<ArchivedBadge />);
    expect(screen.getByText("Archived")).toBeInTheDocument();
  });
});
