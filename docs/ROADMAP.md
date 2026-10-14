# Roadmap (prioritised)

**P0 — next (reliability & verification)**
1. First green Android CI build; fix any compile errors; instrumented smoke test on an emulator (login, chat, offline queue, approvals).
2. Run the Docker sandbox path for real; add gVisor (`runsc`) to the default compose stack.
3. Live-provider integration tests behind a secret (Anthropic + one OpenAI-compatible), with a tiny daily budget.
4. PostgreSQL CI job (service container) to validate ADR-003.

**P1 — security hardening**
5. Passkeys/WebAuthn login. 6. Rootless Docker or a dedicated sandbox VM instead of the socket proxy. 7. Pin resolved IPs during fetch (close DNS-rebinding window). 8. SQLCipher for the Android database. 9. Persistent rate limiter.

**P2 — capability**
10. Native tool-calling for providers that support it (better plans), still gated by the same gateway. 11. Embedded on-device inference (llama.cpp via NDK) with per-device benchmark gate. 12. OCR for scanned PDFs. 13. pgvector + knowledge graph for cross-project search. 14. Email/calendar connectors (approval-gated). 15. Permissioned browser automation. 16. Long-running research agents with checkpointed sub-questions.

**P3 — experience**
17. Markdown rendering in chat/report views. 18. Home-screen widget and share-to-SASTRA intent. 19. Visual workflow builder for schedules. 20. Multiple specialised agents under the same permission system.
