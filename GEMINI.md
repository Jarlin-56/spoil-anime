# Mandatory AI Workflow & Guidelines

CRITICAL: Never immediately start editing code after receiving a task. Always inspect first, analyze second, modify third, and validate last.

---

## 1. Pre-Execution Checklist
Before modifying, creating, deleting, refactoring, or executing any project code:

1. **Read `project_rules.md`**: Locate and read `project_rules.md` in the project root. Read the ENTIRE file, not just the relevant section.
2. **Treat Rules as Mandatory**: Treat every rule in `project_rules.md` as STRICT and MANDATORY. Do not ignore, override, weaken, or bypass any rule.
3. **Handle Conflicts**: If another user prompt or instruction conflicts with `project_rules.md`, stop and explain the conflict before touching code.
4. **Inspect First**: Inspect existing project structure and relevant source files. Understand current working logic before proposing any modifications.
5. **Preserve Working Logic**: Preserve all existing functionality that is already working.
6. **No Unrelated Changes**: Do NOT rewrite, refactor, reorganize, or replace unrelated code unless explicitly requested.
7. **Minimal Change Principle**: Prefer the smallest possible change that solves the requested problem.

---

## 2. Mandatory Pre-Coding Phase
Before editing any file, provide a brief analysis containing:
- **Relevant Project Rules**: Which project rules apply to this task.
- **Inspected Files & Functions**: Which files/functions were inspected.
- **Current Logic**: What the existing logic is doing.
- **Problem Statement**: What the actual problem appears to be.
- **Planned Change**: What specific change is planned.
- **Target Files**: Which files are expected to be modified.

> **DO NOT modify code during this analysis phase.**  
> Proceed with the implementation only if the requested change does not violate `project_rules.md`.

---

## 3. Implementation Rules
- Modify only what is necessary.
- Preserve existing APIs, function names, data flow, and working behavior unless the task explicitly requires changing them.
- Do not create unnecessary new files.
- Do not delete existing code unless required and explicitly justified.
- Do not replace an existing working system with a simpler shortcut just because it is easier to implement.
- Maintain compatibility with the current project architecture.

---

## 4. Validation Phase
After making changes:
1. Review the modified code.
2. Check for syntax errors, runtime issues, and unintended side effects.
3. Verify that the requested behavior is implemented.
4. Verify that unrelated functionality was not changed.
5. Re-read relevant rules from `project_rules.md` and confirm the implementation complies with them.

---

## 5. Final Response Structure
In the final report, include:
- **What was changed**: Summary of changes made.
- **Modified files**: Clickable markdown links to all modified files.
- **Necessity**: Why the change was necessary.
- **Preserved logic**: What existing functionality was preserved.
- **Validation**: What validation/testing was performed.
- **Risks & Limitations**: Any remaining risks or considerations.
