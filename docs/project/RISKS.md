# WhataBit Risk Register

| ID | Risk | Impact | Likelihood | Mitigation | Status |
| --- | --- | --- | --- | --- | --- |
| R-001 | Download engine writes incomplete files as if complete. | High | Medium | Gate final flush on completion; add stopped/incomplete state; document partial output behavior. | Mitigated |
| R-002 | Real-world peers stall due to missing timeout/retry logic. | High | High | Add block request deadlines, peer penalties, and requeue behavior. | Open |
| R-003 | User confuses uploaded `.torrent` metadata with downloaded payload. | Medium | Medium | Keep UI copy explicit; separate `.whatabit/torrents/` from `downloads/`; provide delete controls. | Mitigated |
| R-004 | Legal/safety misuse of BitTorrent client. | High | Medium | Keep README safety guidance prominent; use legal smoke-test examples only. | Open |
| R-005 | Scope creep toward Vuze/uTorrent before 0.2 reliability. | Medium | High | Maintain non-goals; defer DHT/magnet/seeding until 0.2 release criteria are met. | Open |
| R-006 | Local Web UI exposed on unsafe network. | Medium | Low | Bind to `127.0.0.1` by default; warn about `0.0.0.0`. | Mitigated |
| R-007 | Lack of tests makes protocol regressions hard to catch. | Medium | Medium | Add focused unit tests during Sprint 4, and opportunistically with engine changes. | Open |
