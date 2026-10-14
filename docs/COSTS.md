# Cost Estimates

Prices checked 14 Oct 2026. **Model prices below come from third-party pricing pages, not Anthropic's official page — verify on `platform.claude.com/docs/en/about-claude/pricing` and update with Models → or `PUT /api/budget/pricing`.** SASTRA refuses to call any paid model whose price is not configured.

## Unit prices used
| Item | Price | Source |
|---|---|---|
| Claude Haiku 5.5 | $0.10 / 1M input · $0.50 / 1M output | MetricNexus (third-party) |
| Claude Sonnet 5.5 | $2.00 / 1M input · $10.00 / 1M output | AnotherWrapper (third-party, citing Anthropic) |
| Brave Search API | $5 per 1,000 requests, $5 free credit/month (~1,000 free requests) | CostBench, verified 17 Aug 2026 |
| SearXNG (self-hosted) | $0 | — |
| Hetzner cloud (cheapest orderable EU plan, 1 vCPU/1 GB) | €6.49/month | AgentDeals, read 7 Oct 2026 |
| Home PC + Tailscale | ₹0 (electricity only) | — |
| On-device model | ₹0 (battery/heat) | — |

## Monthly estimates (separated)
Assumptions: a chat turn ≈ 1,500 input / 400 output tokens; a research report ≈ 8,000 / 1,500 tokens with 2 searches; a code task ≈ 2 generations of 3,000 / 3,000 tokens.

| | Small | Moderate | Intensive |
|---|---|---|---|
| Usage | 30 chats/day (Haiku), 20 reports, 5 code tasks | 100 chats/day (half Haiku/half Sonnet), 100 reports, 60 code tasks | 300 chats/day (Sonnet), 400 reports, 300 code tasks, daily scheduled briefs |
| Chat inference | ≈ $0.32 | ≈ $11 | ≈ $63 |
| Research inference | ≈ $0.62 | ≈ $3.10 | ≈ $12.40 |
| Code inference | ≈ $0.36 | ≈ $4.30 | ≈ $21.60 |
| Search API | $0 (within free credit or SearXNG) | ≈ $0 (200 req ≈ $1, covered by credit) | ≈ $0 (800 req ≈ $4, covered) — more with heavy schedules |
| Hosting | $0 home PC, or ≈ €6.49 VPS | ≈ €6.49 VPS | larger VPS (verify current price) |
| Storage | negligible (< 1 GB) | < 5 GB | < 20 GB |
| **Total (approx.)** | **≈ $1.30 (home) – $8.50 (VPS)** | **≈ $26** | **≈ $100 + hosting** |
| Caps you must set | defaults are enough ($0.50/day, $10/month) | raise to ~$1.50/day, $30/month | raise to ~$5/day, $120/month |

Development cost: $0 in tools (open-source stack, free GitHub Actions minutes for public repos; private repos have a monthly free allowance).

## How to keep it low
- Use SearXNG (free) for search; use Haiku-class models for chat (`simple` routing is automatic for short messages).
- Run a self-hosted model on your PC (Ollama) as `local` first in the routing order: $0 per token.
- Keep the default caps until you see real usage in `/api/budget/report`.
- Scheduled tasks each have their own budget (default $0.05/run).
