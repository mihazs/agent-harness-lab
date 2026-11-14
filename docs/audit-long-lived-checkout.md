# Audit a long-lived checkout

Use this when `ahl audit` exits 3 with `blocked_reason: entry_bound_exceeded` on a
project whose dependency or build folders hold many files. A fresh clone of the
same commit audits completely; the entry bound counts what a long-lived checkout
adds on disk. Nothing here changes the project: the audit only reads it.

1. Run the audit and read the ranking. The human output prints
   `Inventory blocked: entry_bound_exceeded` and one `Large directory` line per
   top-level directory, largest first. With `--json`, the same rows are in
   `inventory_remedy.largest_directories`. They hold names and counts only, and
   `at least` marks a count that stopped at the bound.

   ```sh
   ahl audit . --runtime claude-code
   ```

2. Decide per directory. A directory that holds no agent instructions, skills,
   hooks or settings, such as installed dependencies or build output, belongs in
   `.gitignore`: the audit then neither counts nor enters it. The audit reads
   only the checkout's own `.gitignore` files, and only a conservative subset of
   git's rules. If the directory is ignored only in your global git excludes, add a
   plain `name/` line to the project's `.gitignore`. If a `.gitignore` holds a line
   outside that subset (for example a backslash escape), the audit applies none of
   its rules, so a directory it already lists still counts; rewrite that line or
   use step 3. A checkout without a `.git` entry at its root ignores nothing. See
   [ignored directories](static-audit.md#ignored-directories-and-the-entry-bound).

3. If every listed directory holds real project files, raise the bound for one
   run, up to 200,000 entries. Raising it reads more of the tree, so prefer
   step 2 where it applies.

   ```sh
   ahl audit . --runtime claude-code --max-inventory-entries 100000
   ```

4. Run the audit again. When it reports `completed`, `ahl improve . --from-audit
   REPORT` re-audits with the same ignore rules but under the default bound, because
   `--max-inventory-entries` belongs to the audit run alone and `improve` has no
   such flag. A report that completed only because of step 3 therefore blocks the
   recheck: `improve` stops before staging anything or calling a model, with `reason:
   inventory_entry_bound_exceeded` and the same ranking in `inventory_remedy`. Use
   step 2 for that report.

   A plain `ahl improve .` reads the inventory only when its candidate edits a
   nested instruction or rule file or declares an instruction plan. It does so after
   the proposal call, so that model call has already run, and a tree that
   still overflows stops the run with the same `reason` and `inventory_remedy`.

The report is a convention check, not evidence of harness performance.
