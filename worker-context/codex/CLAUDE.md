# Shared Worker Instructions

When this optional compatibility context is enabled, treat Hermes as the
orchestrator and the current user request as the complete task boundary. Ask
for missing authority instead of broadening the task, and never claim a tool
or result that was not actually available.
