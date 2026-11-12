# Why a managed region of the global instruction files

[Documentation index](README.md) · How to: [keep your global instructions in step](installation.md#keep-your-global-instructions-in-step) · Reference: [markers and typed reasons](global-instructions-reference.md)

Skills update by link: repoint a link and every agent on the machine reads the new
skill. A global instruction file works differently. It is hand-written configuration,
often shared with other tools, and it holds rules the user does not want rewritten.
So `ahl update` cannot own the file, and it does not try to.

It owns one marked region instead. The region is small, fixed and the same for every
user, so the agent sees the current short guidance (use the harness skill, keep the
scope on the project unless asked, run `ahl update --check`) without anyone editing a
file. Everything else stays yours, byte for byte, and a hash of the file with the
region removed is the check that it did.

Several choices follow from that boundary:

- **Automatic, with an opt-out.** An update that did not refresh the region would
  leave the guidance describing a release that is no longer installed. Because the
  file is private, the opt-out is remembered, so a later update never reverses it.
- **Refuse rather than repair.** Malformed markers, a symlink into someone else's
  file or a file AHL does not own are refused with the exact next step. Guessing a
  repair would be a write AHL cannot prove is safe.
- **A warning, not a rollback.** The launcher and skill links are valid on their own.
  Undoing them because a text file was odd would make the update worse than the
  problem, so the update finishes and exits 3 to say that something needs attention.
- **A replaced edit is kept.** The region is AHL's by definition, but the text a person
  typed there is still theirs, so it goes to the backup and the replacement says so.
- **No extra authority.** The text comes from the checksum-verified archive and no
  code or network is involved beyond what the update already does.

One consequence is timing. The installer that writes the region is the one bundled
with the release you are updating from. The first `ahl update` from a release older
than this feature installs the new release with the old installer; the next
`ahl update` (or the one-line installer) writes the region, and `ahl doctor` reports
it `absent` until then.
