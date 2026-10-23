---
tags: [inkobold, wiki]
---

# Input

`inkobold/input/` — `InputHub`.

| Platform | Path |
|----------|------|
| All | Drawing uses **GDK** pointer / tablet axes |
| Linux | Optional **libinput** device poll + **libwacom** tablet identification |
| Windows | GDK only; status reports libinput is Linux-only |

`InputHub.pressure()` prefers the last libinput tablet sample when present, else GDK.

Linux tip: readable `/dev/input` (often `input` group) for enrichment. Drawing still works via GDK without it.

> **Security note:** membership in the `input` group lets *every* program you run read all input devices directly, including keyboards (so a malicious program could log keystrokes, even outside its own window). GTK already delivers tablet pressure and tilt, so only add yourself to `input` if you need the extra libinput data.

Related: [[Windows]] · [[Install and Run]] · [[Architecture]]
