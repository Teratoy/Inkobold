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

Related: [[Windows]] · [[Install and Run]] · [[Architecture]]
