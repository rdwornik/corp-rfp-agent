# RFP Answer Engine – Multi-Domain (v0.3.0)

This repo hosts an **RFP answer engine** for Blue Yonder solutions, supporting **Planning, AI/ML, WMS**, and future product lines.

The idea:
Take historical presales answers → build a **canonical KB** → let LLMs draft answers for new RFPs in **CSV/Excel/Word** form, in a way that is:

- **KB-first**: no hallucinated product facts.
- **Multi-domain**: Planning, AI/ML, WMS knowledge in unified database.
- **Multi-LLM**: supports Gemini, Claude, GPT-5, DeepSeek, GLM, and more.
- **Multi-format**: Excel, CSV, and Word document support.
- **Privacy-aware**: anonymization layer protects customer names.
- **Local-first analysis**: Ollama for document analysis (zero API cost).
- **Auditable**: easy to review, tweak, and re-run.

---

## What's New in v0.3.0 (Jan 2026)

| Feature | Description |
|---------|-------------|
| **Word RFP Agent** | Process Word documents (.docx) through 3-stage pipeline |
| **Local LLM Analysis** | Ollama integration for document analysis (zero API cost) |
| **Document Structure Detection** | Auto-detect questions, tables, and placeholders in Word docs |
| **Clean Architecture** | Restructured codebase: `src/` for core modules, `scripts/` for utilities |

### Word RFP Agent Pipeline
```
STAGE 1: Analyze document structure (Ollama - local, free)
    ↓
STAGE 2: Generate answers with RAG (External LLM - Gemini/Claude/etc)
    ↓
STAGE 3: Fill answers back into Word document (python-docx)
```

---

## What's New in v0.2.1 (Dec 2024)

| Feature | Description |
|---------|-------------|
| **Solution-Aware Responses** | `--solution` flag injects platform service context for product-specific answers |
| **Multi-Domain KB** | Unified KB with Planning (807), AI/ML (54), WMS (38) entries |
| **KB Workflow Tools** | `kb_transform_knowledge.py` + `kb_merge_canonical.py` for easy domain additions |
| **Scope Classification** | Auto-classify entries as `platform` vs `product_specific` |
| **Fixed RAG Retrieval** | Resolved "Not in KB" issue with domain-prefixed ChromaDB IDs |
| **Debug Mode** | `DEBUG_RAG=1` for detailed retrieval logging |

### Previous (v0.2)

| Feature | Description |
|---------|-------------|
| **Universal RAG** | Local ChromaDB with BGE embeddings (no vendor lock-in) |
| **Multi-LLM Router** | Switch between 9+ LLM providers with one flag |
| **Anonymization** | Protect customer names before API calls |
| **BGE Embeddings** | Upgraded from MiniLM to `bge-large-en-v1.5` for better retrieval |

---

## High-level Architecture
```
┌─────────────────────────────────────────────────────────────┐
│                     RFP Questions (CSV/Excel)               │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  ANONYMIZATION LAYER (optional)                             │
│  - Remove customer names from blocklist                     │
│  - Replace with [CUSTOMER] placeholder                      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  RETRIEVAL (ChromaDB + BGE-large)                           │
│  - Local vector database                                    │
│  - Top-k similar KB entries                                 │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  LLM ROUTER                                                 │
│  - Gemini, Claude, GPT-5, DeepSeek, GLM, Kimi, Llama, Grok  │
│  - KB-first system prompt                                   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  DE-ANONYMIZATION                                           │
│  - Restore customer names in output                         │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     Answered RFP (CSV)                      │
└─────────────────────────────────────────────────────────────┘
```

---

## Quick Start

### 1. Setup
```powershell
pip install -r requirements.txt
```

Create `.env` file:
```env
GEMINI_API_KEY=your_key
ANTHROPIC_API_KEY=your_key      # Optional: for Claude
OPENAI_API_KEY=your_key         # Optional: for GPT-5
DEEPSEEK_API_KEY=your_key       # Optional: for DeepSeek
ZHIPU_API_KEY=your_key          # Optional: for GLM
```

### 2. Build KB

#### Option A: Transform New Knowledge (Recommended)
```powershell
# 1. Transform raw knowledge (JSONL) to canonical format
python src/kb_transform_knowledge.py --input data/kb/raw/knowledge_wms.jsonl --domain wms --source-type video_workshop --version 2025.1

# 2. Merge all domain KBs into unified database
python src/kb_merge_canonical.py

# 3. Index to ChromaDB (local, free)
python src/kb_embed_chroma.py
```

#### Option B: Use Existing KB (Legacy)
```powershell
# Build canonical KB from raw data
python src/kb_build_canonical.py

# Index to ChromaDB
python src/kb_embed_chroma.py
```

See [docs/KB_WORKFLOW.md](docs/KB_WORKFLOW.md) for detailed workflow documentation.

### 3. Run Batch Processor (CSV)
```powershell
# Test mode with Gemini
python src/rfp_batch_universal.py --test --model gemini

# Production with Claude + anonymization
python src/rfp_batch_universal.py --model claude --anonymize

# Solution-aware mode (platform service integration context)
python src/rfp_batch_universal.py --solution wms_native
python src/rfp_batch_universal.py --solution planning -m claude

# Combined flags (all short form)
python src/rfp_batch_universal.py -t -m deepseek -a -w 8 -s wms

# Debug mode (see what KB entries are retrieved)
$env:DEBUG_RAG=1; python src/rfp_batch_universal.py -t
```

### 4. Process Excel with Green Cells
```powershell
# Dry run - analyze without processing
python src/rfp_excel_agent.py --input "data/input/RFP.xlsx" --client acme --dry-run

# Process green-highlighted cells only
python src/rfp_excel_agent.py --input "data/input/RFP.xlsx" --client acme --solution planning --model gemini

# With anonymization and parallel workers
python src/rfp_excel_agent.py --input "data/input/RFP.xlsx" --client acme --solution wms_native --model claude --anonymize --workers 8
```

**How it works:**
1. Scans Excel for cells with green fill (`FF00FF00`)
2. Auto-detects question column (Requirement, Question, Description)
3. Auto-detects answer column (Vendor Comment, BY Response, etc.)
4. Generates answers only for green-highlighted rows
5. Preserves all other cells, images, and formatting

### 5. Process Word Documents
```powershell
# Full pipeline: analyze → generate answers → fill document
python src/rfp_word_agent.py --input "data/input/RFP_Document.docx" --solution planning --model gemini

# Analyze only (uses local Ollama - no API cost)
python src/rfp_word_agent.py --input "data/input/RFP_Document.docx" --analyze-only

# With custom local LLM model
python src/rfp_word_agent.py --input "data/input/RFP_Document.docx" --local-llm "llama3.2:7b"
```

---

## Supported LLM Models

| Model | Provider | Flag | Cost (per 1M tokens) |
|-------|----------|------|----------------------|
| Gemini 2.5 Pro | Google | `gemini` | $2 in / $12 out |
| Gemini 2.5 Flash | Google | `gemini-flash` | $0.15 in / $0.60 out |
| Claude Sonnet 4 | Anthropic | `claude` | $3 in / $15 out |
| GPT-5 | OpenAI | `gpt5` | $1.25 in / $10 out |
| DeepSeek V3 | DeepSeek | `deepseek` | $0.28 in / $0.42 out |
| GLM 4.7 | Zhipu | `glm` | $0.60 in / $2.20 out |
| Kimi K2 | Moonshot | `kimi` | $0.60 in / $2.05 out |
| Llama 4 Maverick | Together | `llama` | $0.27 in / $0.80 out |
| Grok 3 | xAI | `grok` | $3 in / $15 out |

---

## Anonymization System

Protect customer names before sending to external LLM APIs.

### Configure Blocklist

Edit `config/anonymization.yaml`:
```yaml
blocklist:
  kb_sources:
    - Acme Corp           # Customers whose data built the KB
  customers:
    - Walmart             # Other names to protect
    - Target
  projects:
    - Project Phoenix     # Internal project names
  internal: []

session:
  customer_name: "Carrefour"   # Current RFP customer
  placeholder: "[CUSTOMER]"

settings:
  anonymize_api_calls: true
  anonymize_local_calls: false
```

### CLI Commands
```powershell
# Scan KB for sensitive terms
python -m src.anonymization.scan_kb

# Preview cleaning (dry run)
python -m src.anonymization.clean_kb --dry-run

# Clean KB
python -m src.anonymization.clean_kb

# Run with anonymization
python src/rfp_batch_universal.py -t -m gemini -a
```

### How It Works
```
Input:  "Does Walmart need SSO integration?"
    ↓ anonymize()
API:    "Does [CUSTOMER] need SSO integration?"
    ↓ LLM response
Output: "Blue Yonder supports SSO for [CUSTOMER]..."
    ↓ deanonymize()
Final:  "Blue Yonder supports SSO for Walmart..."
```

---

## Project Structure
```
.
├── config/
│   ├── anonymization.yaml             # Blocklist and session config
│   └── platform_matrix.json           # Platform services matrix
├── data/
│   ├── kb/
│   │   ├── raw/                       # Raw knowledge (JSONL from workshops)
│   │   ├── canonical/                 # Canonical KB files by domain
│   │   │   ├── RFP_Database_AIML_CANONICAL.json
│   │   │   ├── RFP_Database_Cognitive_Planning_CANONICAL.json
│   │   │   ├── RFP_Database_WMS_CANONICAL.json
│   │   │   └── RFP_Database_UNIFIED_CANONICAL.json  # ← Used by system
│   │   └── chroma_store/              # Local vector database
│   ├── input/                         # RFP input files (Excel/Word)
│   └── output/                        # Generated RFP answers
├── docs/
│   ├── KB_WORKFLOW.md                 # KB transformation workflow guide
│   └── BUGFIX_NOT_IN_KB.md            # Recent bugfix documentation
├── prompts/
│   ├── rfp_system_prompt_universal.txt     # Universal RAG prompt
│   ├── platform_context.md            # Platform service templates
│   └── kb_distiller_prompt.txt        # KB distillation prompt
├── scripts/                           # Utility scripts
│   ├── test_api_keys.py               # Test API key connectivity
│   ├── debug_rag_retrieval.py         # Debug RAG retrieval
│   └── check_api_keys.py              # Check API key presence
├── src/                               # Core Python modules
│   ├── __init__.py
│   ├── llm_router.py                  # Multi-LLM router + RAG
│   ├── rfp_batch_universal.py         # Universal batch processor (Excel/CSV)
│   ├── rfp_excel_agent.py             # Excel agent for green-cell processing
│   ├── rfp_word_agent.py              # Word document agent (NEW)
│   ├── word_analyzer.py               # Ollama-powered document analysis
│   ├── word_filler.py                 # Fill answers into Word docs
│   ├── kb_build_canonical.py          # Build KB from raw sources
│   ├── kb_transform_knowledge.py      # Transform JSONL → Canonical
│   ├── kb_merge_canonical.py          # Merge all KBs into unified
│   ├── kb_embed_chroma.py             # Index to ChromaDB
│   ├── excel_to_platform_matrix.py    # Convert Excel to platform_matrix.json
│   ├── solution_filter.py             # Solution filtering utilities
│   └── anonymization/                 # Anonymization package
│       ├── __init__.py
│       ├── config.py                  # YAML loader
│       ├── core.py                    # anonymize(), deanonymize()
│       ├── middleware.py              # Pipeline middleware
│       ├── scan_kb.py                 # CLI: scan KB for sensitive terms
│       └── clean_kb.py                # CLI: clean KB with backup
├── tests/                             # Test files
├── CLAUDE.md                          # Project context for Claude Code
├── requirements.txt
└── README.md
```

---

## CLI Reference

### Batch Processor (CSV)
```powershell
python src/rfp_batch_universal.py [OPTIONS]

# Options:
#   -t, --test          Use test input folder
#   -m, --model MODEL   LLM to use (default: gemini)
#   -w, --workers N     Parallel workers (default: 4)
#   -a, --anonymize     Enable anonymization
#   -s, --solution CODE Solution-aware context (41 solutions available)
```

### Excel Agent (Green Cells)
```powershell
python src/rfp_excel_agent.py [OPTIONS]

# Options:
#   -i, --input PATH    Input Excel file (required)
#   -c, --client NAME   Client name for output filename (required)
#   -s, --solution CODE Solution context (see config/platform_matrix.json)
#   -m, --model MODEL   LLM to use (default: gemini)
#   -a, --anonymize     Enable anonymization
#   -d, --dry-run       Analyze without processing
#   -w, --workers N     Parallel workers (default: 4)
#   -o, --output PATH   Output path (auto-generated if not provided)
```

### Word RFP Agent
```powershell
python src/rfp_word_agent.py [OPTIONS]

# Options:
#   --input PATH        Path to Word document (.docx)
#   --solution CODE     Solution context (default: planning)
#   --model MODEL       External LLM for answer generation (default: gemini)
#   --local-llm MODEL   Ollama model for analysis (default: qwen2.5:7b)
#   --analyze-only      Only run analysis stage (no answer generation)
#   --output-dir PATH   Output directory for filled document
```

### KB Management
```powershell
# Transform new knowledge
python src/kb_transform_knowledge.py -i data/kb/raw/knowledge.jsonl -d wms

# Merge all domain KBs
python src/kb_merge_canonical.py

# Re-index ChromaDB
python src/kb_embed_chroma.py

# Legacy KB builder
python src/kb_build_canonical.py
```

### Anonymization
```powershell
# Scan KB
python -m src.anonymization.scan_kb

# Clean KB
python -m src.anonymization.clean_kb --dry-run
```

---

## Legacy: Google File Search

The original File Search approach is still available but deprecated in favor of local RAG.

---

## Knowledge Base Statistics

| Domain | Entries | Scope: Platform | Scope: Product-Specific |
|--------|---------|-----------------|-------------------------|
| Planning | 807 | - | - |
| AI/ML | 54 | - | - |
| WMS | 38 | 10 | 28 |
| **Total** | **899** | **10** | **28** |

Knowledge is automatically classified by:
- **Domain**: planning, aiml, wms, catman, logistics
- **Scope**: platform (shared across products) vs product_specific
- **Category**: SLA, Integration, Security, WMS Features, etc.

---

## Documentation

- **[KB Workflow Guide](docs/KB_WORKFLOW.md)** - How to add new knowledge to the system
- **[Bugfix: "Not in KB"](docs/BUGFIX_NOT_IN_KB.md)** - Resolved RAG retrieval issue
- **[Platform Context Guide](prompts/platform_context.md)** - Response framing for solution-aware RFP answers
- **[CLAUDE.md](CLAUDE.md)** - Project context for AI assistants

---

## Solution-Aware Response System

The `--solution` flag enables product-specific platform service context injection:

**How it works:**
1. Loads `config/platform_matrix.json` with 41 solutions and platform service statuses
2. Injects solution-specific context into LLM prompts
3. Adjusts response framing based on integration level:
   - **Native**: "[Capability] for [Product] is configured through Blue Yonder Platform"
   - **Planned**: "Blue Yonder Platform supports this on infrastructure level and full native integration is planned"
   - **Infrastructure**: "Blue Yonder Platform supports this functionality on an infrastructure level"

**Available Solutions (41):**
- Planning: `planning`, `planning_ibp`, `planning_pps`
- WMS: `wms`, `wms_native`, `wms_labor`, `wms_tasking`, `wms_billing`, `wms_robotics`
- Logistics: `logistics`, `logistics_ba`, `logistics_modeling`, `logistics_fom`, etc.
- Retail: `retail_ar`, `retail_ap`, `retail_clearance`, `retail_markdown`, etc.
- And more (see `config/platform_matrix.json`)

**Example Usage:**
```powershell
python src/rfp_batch_universal.py --solution wms_native -m claude
```

---

## Word RFP Agent

The Word agent processes `.docx` files through a 3-stage pipeline, combining local LLM analysis with external RAG-powered answer generation.

### Prerequisites
- **Ollama** installed locally with a model (default: `qwen2.5:7b`)
- External LLM API key (Gemini, Claude, etc.)

### Pipeline Stages

| Stage | Engine | Purpose | Cost |
|-------|--------|---------|------|
| 1. Analyze | Ollama (local) | Detect questions, tables, placeholders | Free |
| 2. Generate | External LLM | RAG-powered answer generation | API cost |
| 3. Fill | python-docx | Insert answers back into document | Free |

### Features
- **Auto-detection**: Finds questions based on patterns, table structures, placeholder text
- **Anonymization**: Customer names replaced before external API calls
- **De-anonymization**: Customer names restored in final document
- **Category classification**: Questions auto-tagged (security, integration, etc.)

### Example
```powershell
# Full pipeline
python src/rfp_word_agent.py --input "data/input/Customer_RFP.docx" --solution planning --model claude

# Output: data/output/Customer_RFP_answered_YYYYMMDD_HHMM.docx
```

---

## Roadmap

- [x] Multi-domain KB support (Planning, AI/ML, WMS)
- [x] Scope classification (platform vs product_specific)
- [x] Debug mode for RAG retrieval
- [x] Solution-aware response system with platform service context
- [x] Local LLM support (Ollama integration for document analysis)
- [x] Word document support (.docx) with 3-stage pipeline
- [x] Clean project architecture (src/ module structure)
- [ ] Deprecation system for versioned KB entries
- [ ] Add CatMan and Logistics domains
- [ ] A/B testing across models
- [ ] Cost tracking per batch
- [ ] PowerPoint RFP support
- [ ] Web UI for non-technical users

---

## License

Internal use only – Blue Yonder Presales.