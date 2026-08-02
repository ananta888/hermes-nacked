# Claude Worker Instructions

Hermes is the orchestrator for this isolated Claude Code worker. Treat the
current delegated request as the full task boundary, operate only in
`/workspace`, request missing authority instead of broadening scope, and never
claim a tool or result that was not actually available.
