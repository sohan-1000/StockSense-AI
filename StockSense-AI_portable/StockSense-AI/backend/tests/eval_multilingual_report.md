# NL2SQL evaluation [eval_multilingual.yaml]  (2026-11-14 11:42)
- cases: 30 (24 SQL, 6 routing/unsupported) | wall time 62s | avg 6.1s per case
- routing accuracy:      30/30 = 100%
- execution accuracy:    24/24 = 100%
- result accuracy:       24/24 = 100%
- first-attempt correct: 22/24 = 92%  (schema-linking proxy)
- LLM-written answers that passed the number-grounding check: 24/24
- reply written in the requested language: 30/30

## By category (result accuracy for SQL cases / routing for others)
- sales: 8/8
- dates: 6/6
- inventory: 4/4
- expiry: 2/2
- product: 2/2
- supplier: 2/2
- unsupported: 2/2
- rag_route: 4/4

## Failures