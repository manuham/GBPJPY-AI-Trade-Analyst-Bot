# Workspace Exploration Report — Index

This directory contains a comprehensive exploration of the AI Trade Bot ICT workspace as of **February 17, 2026**.

## Three Key Documents Generated

### 1. WORKSPACE_EXPLORATION.md (17 KB, 408 lines)
**Comprehensive deep-dive analysis** — Read this if you want the full story.

**Covers:**
- Project overview (what it is, how it works)
- Complete directory structure with file descriptions
- Last 20 commits with impact analysis
- Current work-in-progress status (10 files modified, 0 staged)
- Git branch status and review
- Three major feature phases (Backtesting, Dashboard, Multi-pair)
- Code organization by responsibility
- Claude models used (Sonnet, Opus, Haiku pricing)
- SQLite database schema
- Deployment & infrastructure details
- Telegram commands and features
- Code quality observations and known gotchas
- TODO items and next likely tasks
- Quick reference for common tasks
- Executive summary

**Best for:** Understanding the full system architecture and recent development history.

### 2. QUICK_REFERENCE.md (12 KB, 337 lines)
**Visual cheat sheet** — Read this for quick lookup and debugging.

**Contains:**
- ASCII architecture diagram (MetaTrader → FastAPI → Claude)
- File map with line counts
- Trade lifecycle flow diagram
- API endpoints table
- Telegram commands list
- 12-point ICT checklist
- Key configuration settings
- Python dependencies
- Database table structure
- Deployment quick start (VPS and local)
- Claude model selection guide
- Known gotchas and patterns
- Recent major features (Feb 2026)
- File size reference
- Uncommitted changes summary
- Key documentation files
- Quick debugging commands
- Repository info

**Best for:** Quick lookups, getting unstuck, remembering file locations.

### 3. CLAUDE.md (8.1 KB)
**Official v3.0 project instructions** — Read if questions arise about methodology or system flow.

**Already in the repo** — This is the authoritative source for:
- What the project is and how it flows
- File map and line counts
- API endpoints and their purposes
- Claude models used and costs
- ICT analysis methodology (12-point checklist)
- Risk management rules
- Common deployment tasks
- Known patterns and gotchas

**Note:** This pre-exists in the repo as official project context.

---

## At a Glance

| Metric | Value |
|--------|-------|
| **Total Code** | 10,150 lines |
| **Total Commits** | 79 |
| **Python Server Files** | 13 |
| **MQL5 Expert Advisors** | 2 (1 active, 1 legacy) |
| **Dashboard (Streamlit)** | 1 |
| **Core API Endpoints** | 9 |
| **Telegram Commands** | 7 |
| **Supported Pairs** | 3 (GBPJPY, EURUSD, GBPUSD) |
| **Main Branch Status** | Clean, production-ready |
| **Uncommitted Changes** | 10 files (code cleanup, docs) |
| **Days to Latest** | 0 (as of Feb 17, 2026) |

---

## Project Status Summary

**AI Trade Bot ICT v3.0** is an actively developed, well-structured automated trading system that:

1. **Captures** D1/H4/H1/M5 screenshots at London open (08:00 MEZ)
2. **Analyzes** using Claude's 3-tier AI (cost-optimized: Sonnet → Opus → Haiku)
3. **Detects** ICT-based trade setups (12-point checklist scoring)
4. **Confirms** entries with M1 reaction check (max 10 attempts)
5. **Executes** market orders when zone + confirmation align
6. **Manages** positions with adaptive TP1 closes, break-even, trailing stops
7. **Tracks** everything in SQLite (trades.db) with Telegram alerts at each stage
8. **Reports** performance via Telegram, dashboard, and API endpoints
9. **Backtests** strategies against historical data
10. **Scales** to multiple pairs (EURUSD, GBPUSD in beta)

**Production Status:**
- Main branch is clean and up-to-date
- 10 files have pending minor changes (code cleanup, documentation)
- Zero blocking issues
- Deployment instructions are clear and tested
- Code quality was recently improved (11 fixes in commit df6d175)

---

## How to Navigate

### I want to understand the system...
→ Read **WORKSPACE_EXPLORATION.md** (full context)

### I need a quick fact or file location...
→ Check **QUICK_REFERENCE.md** (tables, diagrams)

### I'm implementing a feature and need methodology...
→ See **CLAUDE.md** (official project instructions)

### I'm deploying or debugging...
→ Use **QUICK_REFERENCE.md** (quick start, debugging)

### I'm reviewing the codebase...
→ Start with **WORKSPACE_EXPLORATION.md** (file map, organization)

---

## Key Files in This Repo

| File | Size | Purpose |
|------|------|---------|
| `CLAUDE.md` | 8.1 KB | Official v3.0 project context (already in repo) |
| `WORKSPACE_EXPLORATION.md` | 17 KB | Complete workspace analysis (newly generated) |
| `QUICK_REFERENCE.md` | 12 KB | Visual cheat sheet and quick lookup (newly generated) |
| `README.md` | 7.7 KB | Project overview and quick start |
| `setup.md` | Various | Full deployment guide |
| `docker-compose.yml` | Various | Container orchestration config |

---

## Git Status (as of Feb 17, 2026)

```
Branch: main
Status: Up to date with origin/main
Commits: 79 total
Recent: Phase 3: Multi-pair expansion (063a159)

Uncommitted changes:
  Modified: 10 files (code cleanup, docs, Docker config)
  Untracked: Scaling_Roadmap.docx (new document)
  Staged: 0 files
```

---

## Workspace Location

```
/sessions/cool-quirky-maxwell/mnt/GBPJPY-AI-Trade-Analyst-Bot/
├── CLAUDE.md                    ← Official instructions
├── README.md                    ← Project overview
├── QUICK_REFERENCE.md           ← New: Quick lookup guide
├── WORKSPACE_EXPLORATION.md     ← New: Complete analysis
├── README_EXPLORATION.md        ← This file
├── setup.md
├── server/                      ← Python FastAPI backend
├── mt5/                         ← Expert Advisors
├── dashboard/                   ← Streamlit web UI
├── data/                        ← SQLite databases
├── docs/                        ← Additional documentation
└── .git/                        ← Git repository
```

---

## Quick Commands

```bash
# View the analysis
cat /sessions/cool-quirky-maxwell/mnt/GBPJPY-AI-Trade-Analyst-Bot/WORKSPACE_EXPLORATION.md

# View the cheat sheet
cat /sessions/cool-quirky-maxwell/mnt/GBPJPY-AI-Trade-Analyst-Bot/QUICK_REFERENCE.md

# Check git status
cd /sessions/cool-quirky-maxwell/mnt/GBPJPY-AI-Trade-Analyst-Bot && git status

# View recent commits
cd /sessions/cool-quirky-maxwell/mnt/GBPJPY-AI-Trade-Analyst-Bot && git log --oneline -10

# Check code statistics
cd /sessions/cool-quirky-maxwell/mnt/GBPJPY-AI-Trade-Analyst-Bot && wc -l server/*.py dashboard/*.py mt5/*.mq5
```

---

## Questions Answered by These Documents

### About the Project
- What is GBPJPY AI Trade Analyst Bot? → WORKSPACE_EXPLORATION.md § 1
- What does it do? → QUICK_REFERENCE.md (top) or WORKSPACE_EXPLORATION.md § 1
- How does it work? → QUICK_REFERENCE.md (Trade Lifecycle diagram)
- What's the architecture? → QUICK_REFERENCE.md (system diagram) or CLAUDE.md § System Flow

### About the Code
- What files are there? → QUICK_REFERENCE.md (File Map) or WORKSPACE_EXPLORATION.md § 2
- Which file does X? → QUICK_REFERENCE.md (File Map table)
- How many lines of code? → WORKSPACE_EXPLORATION.md § 1 (10,150 total)
- What's the largest file? → QUICK_REFERENCE.md (File Size Reference)

### About Recent Work
- What was done last? → WORKSPACE_EXPLORATION.md § 3 (git history)
- What's being worked on? → WORKSPACE_EXPLORATION.md § 4 (uncommitted changes)
- What are the three phases? → WORKSPACE_EXPLORATION.md § 6
- Is the main branch clean? → WORKSPACE_EXPLORATION.md § 5 (git status)

### About Deployment
- How do I deploy? → QUICK_REFERENCE.md (Deployment Quick Start)
- What ports are used? → WORKSPACE_EXPLORATION.md § 10
- What's the VPS? → WORKSPACE_EXPLORATION.md § 10 (46.225.66.110)
- What env vars? → QUICK_REFERENCE.md (Environment Variables)

### About APIs
- What endpoints exist? → QUICK_REFERENCE.md (API Endpoints table)
- How do I call them? → CLAUDE.md § Key API Endpoints
- What auth is needed? → WORKSPACE_EXPLORATION.md § 13 (Known Gotchas)

### About Claude Models
- Which models are used? → WORKSPACE_EXPLORATION.md § 8
- What do they cost? → QUICK_REFERENCE.md (Claude Model Selection)
- When is each used? → CLAUDE.md § Claude Models Used

### About ICT Methodology
- What's the 12-point checklist? → QUICK_REFERENCE.md (12-Point ICT Checklist)
- What scoring system? → QUICK_REFERENCE.md (same section)
- How is analysis done? → CLAUDE.md § ICT Analysis Methodology

### About Debugging
- How do I check health? → QUICK_REFERENCE.md (Quick Debugging)
- How do I view logs? → QUICK_REFERENCE.md (same section)
- How do I query trades? → QUICK_REFERENCE.md (same section)

---

## Document Statistics

| Document | Type | Size | Lines | Focus |
|----------|------|------|-------|-------|
| WORKSPACE_EXPLORATION.md | Markdown | 17 KB | 408 | Comprehensive analysis |
| QUICK_REFERENCE.md | Markdown | 12 KB | 337 | Quick lookup, tables |
| CLAUDE.md | Markdown | 8.1 KB | 288 | Official instructions |
| README.md | Markdown | 7.7 KB | 230 | Overview, quick start |

**Total Documentation:** ~45 KB across 4 files

---

## Generated By

- **Tool:** Claude Code workspace explorer
- **Date:** February 17, 2026
- **Project Version:** v3.0
- **Location:** /sessions/cool-quirky-maxwell/mnt/GBPJPY-AI-Trade-Analyst-Bot/

---

**Start with QUICK_REFERENCE.md for a fast overview, then dive into WORKSPACE_EXPLORATION.md for details.**
