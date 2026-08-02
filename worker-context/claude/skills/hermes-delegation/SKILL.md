---
name: hermes-delegation
description: Execute a bounded coding task delegated by Hermes inside the isolated worker workspace. Use when Hermes supplies a concrete implementation, diagnosis, review, or test task.
---

# Hermes Delegation

Treat the delegated prompt as the task contract. Inspect only relevant files,
make the smallest sufficient change, run verification permitted by the current
worker profile, and return a concise list of changed files, checks, and any
remaining limitation. Never access authentication state or alter worker
rights.
