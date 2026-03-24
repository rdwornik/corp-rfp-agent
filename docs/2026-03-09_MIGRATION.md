# Migration Status: corp-rfp-agent v0.3.0 -> v1.0.0

## Phase Status
| Phase | Description | Status | Started | Done |
|-------|-------------|--------|---------|------|
| 0 | Baseline + fixtures | DONE | 2026-03-09 | 2026-03-09 |
| 1 | Architecture spine | DONE | 2026-03-09 | 2026-03-09 |
| 2 | Override layer | DONE | 2026-03-09 | 2026-03-09 |
| 3 | Port KB + pipelines | DONE | 2026-03-09 | 2026-03-09 |
| 4 | Port Excel agent | DONE | 2026-03-09 | 2026-03-09 |
| 5 | Port Word agent + cleanup | DONE | 2026-03-09 | 2026-03-09 |

## Module Status
| Module | Current State | Target State | Ported? |
|--------|--------------|--------------|---------|
| LLM Router | Working, no tests | Behind LLMClient interface | Interface ready |
| Excel Agent | Working, no tests | Composition-based, tested | v2 built (v1 fallback) |
| Word Agent | Clean (just rewritten) | Behind shared services | v2 built (v1 fallback) |
| KB Pipeline | Working (3-stage) | Behind KBClient | Structure ported |
| Anonymization | Working | Mandatory gate service | Service ready |
| Config | Scattered (.env + hardcoded) | Single YAML + .env secrets | Loader ready |
| KB Embedding | Working (ChromaDB + BGE) | Behind KBClient | Ported (upsert/rebuild/query) |
| Archive Registry | Working | Preserved as-is | Ported (ArchiveRegistry) |

## Package Layout (Phase 1)
```
src/corp_rfp_agent/
    __init__.py
    core/
        __init__.py
        config.py          -- AppConfig loader (YAML + .env + env vars)
        logging.py         -- Rich logging setup
        types.py           -- Family, Category, LLMResponse, KBMatch
    llm/
        __init__.py
        client.py          -- LLMClient protocol
        router_adapter.py  -- RouterLLMClient wrapping legacy llm_router.py
    kb/
        __init__.py
        client.py          -- KBClient protocol
        chromadb_impl.py   -- ChromaKBClient wrapping existing ChromaDB
        entry.py           -- KBEntry dataclass (v2 schema)
    domain/
        __init__.py
        entry_adapter.py   -- Legacy JSONL/JSON <-> KBEntry mapping
        taxonomy.py        -- Family display names (corp-os-meta fallback)
    anonymize/
        __init__.py
        service.py         -- Anonymizer with anonymize/de_anonymize
    overrides/
        __init__.py
        models.py          -- Override, OverrideMatch, OverrideResult dataclasses
        store.py           -- YAMLOverrideStore (loads config/overrides.yaml)
        cli.py             -- CLI: list, add, remove, test, stats
    agents/
        __init__.py
        excel/
            __init__.py
            models.py      -- GreenCell, AnswerResult, ExcelAgentResult
            cell_detector.py -- CellDetector (green cell + column detection)
            answer_writer.py -- AnswerWriter (write answers, preserve formatting)
            agent.py       -- ExcelAgent v2 (composition-based pipeline)
        word/
            __init__.py
            models.py      -- Section, AnswerableBlock, SectionAnswer, WordAgentResult
            section_parser.py -- detect_heading_level, build_section_tree, collect_answerable_sections
            answer_inserter.py -- insert_answer_after, insert_blank_after, has_existing_response
            agent.py       -- WordAgent v2 (composition-based pipeline)
    pipelines/
        __init__.py
        kb_loader.py       -- KBLoader (load all/family/stats)
        kb_builder.py      -- KBBuilder (merge unified, append to family)
        kb_stats.py        -- gather_stats(), show_stats()
        archive/
            __init__.py
            registry.py    -- ArchiveRegistry + ArchiveEntry
        extraction/
            __init__.py
            models.py      -- StructureMap, RawRow, ClassifiedRow, ExtractionResult
            pipeline.py    -- ExtractionPipeline (skeleton, delegates to legacy)
    cli.py                 -- Unified CLI (kb stats, kb rebuild, overrides)
```

## Test Status (159/159 passing)

### Phase 0: Acceptance Tests (32 tests)
| Test | Fixture | Passing? |
|------|---------|----------|
| Excel: green cells detected | excel_golden.xlsx | YES |
| Excel: answered cells not green | excel_golden.xlsx | YES |
| Excel: header row detection | excel_golden.xlsx | YES |
| Excel: question column detection | excel_golden.xlsx | YES |
| Excel: answer column detection | excel_golden.xlsx | YES |
| Excel: scan_green_cells | excel_golden.xlsx | YES |
| Excel: merged cells | excel_golden.xlsx | YES |
| Excel: formulas preserved | excel_golden.xlsx | YES |
| Excel: workbook integrity | excel_golden.xlsx | YES |
| Excel: category headers skipped | excel_golden.xlsx | YES |
| Excel: special characters | excel_golden.xlsx | YES |
| Word: section tree detection | word_golden.docx | YES |
| Word: integration subsections | word_golden.docx | YES |
| Word: content paragraph counts | word_golden.docx | YES |
| Word: answerable sections | word_golden.docx | YES |
| Word: breadcrumbs | word_golden.docx | YES |
| Word: blue text position | word_golden.docx | YES |
| Word: blue text formatting | word_golden.docx | YES |
| Word: table preservation | word_golden.docx | YES |
| Word: document integrity | word_golden.docx | YES |
| Word: heading level patterns | word_golden.docx | YES |
| Router: retry on failure | mock provider | YES |
| Router: backoff timing | mock provider | YES |
| Router: max retries exceeded | mock provider | YES |
| Router: non-rate-limit errors | mock provider | YES |
| Router: model registry | none | YES |
| CLI: 6 commands --help | none | YES |

### Phase 1: Architecture Spine Tests (36 tests)
| Test | Module | Passing? |
|------|--------|----------|
| Family enum 11 families | types | YES |
| Category enum 6 categories | types | YES |
| Confidence enum | types | YES |
| LLMResponse creation | types | YES |
| KBMatch creation | types | YES |
| KBEntry valid | entry | YES |
| KBEntry empty answer invalid | entry | YES |
| KBEntry empty question invalid | entry | YES |
| KBEntry whitespace invalid | entry | YES |
| KBEntry auto ID | entry | YES |
| KBEntry same content same ID | entry | YES |
| KBEntry different content different ID | entry | YES |
| KBEntry explicit ID preserved | entry | YES |
| KBEntry last_updated default | entry | YES |
| KBEntry default values | entry | YES |
| Adapter from legacy JSONL | entry_adapter | YES |
| Adapter from v2 dict | entry_adapter | YES |
| Adapter canonical aliases | entry_adapter | YES |
| Adapter to_dict roundtrip | entry_adapter | YES |
| Adapter load_jsonl mixed | entry_adapter | YES |
| Adapter load_jsonl skips invalid | entry_adapter | YES |
| Adapter load_jsonl blank lines | entry_adapter | YES |
| Adapter load_json | entry_adapter | YES |
| Anonymize client name | anonymizer | YES |
| Anonymize case insensitive | anonymizer | YES |
| De-anonymize restores | anonymizer | YES |
| Anonymize roundtrip | anonymizer | YES |
| Anonymize multiple terms | anonymizer | YES |
| No terms unchanged | anonymizer | YES |
| Term count | anonymizer | YES |
| Config defaults | config | YES |
| Config API keys from env | config | YES |
| Config YAML loading | config | YES |
| Config paths resolve | config | YES |
| Config .env file loading | config | YES |
| Config env overrides dotenv | config | YES |

### Phase 2: Override Layer Tests (18 tests)
| Test | Module | Passing? |
|------|--------|----------|
| Load from YAML | store | YES |
| Apply simple replacement | store | YES |
| Apply case insensitive | store | YES |
| Apply whole word | store | YES |
| Apply no match | store | YES |
| Apply multiple overrides | store | YES |
| Apply family filter | store | YES |
| Disabled override skipped | store | YES |
| Skips invalid entries | store | YES |
| Audit trail | store | YES |
| List overrides | store | YES |
| Remove override | store | YES |
| Stats | store | YES |
| Empty store | store | YES |
| Get override protocol | store | YES |
| CLI list | cli | YES |
| CLI test | cli | YES |
| CLI stats | cli | YES |

### Phase 3: KB & Pipeline Tests (30 tests)
| Test | Module | Passing? |
|------|--------|----------|
| Load all finds canonical JSON | kb_loader | YES |
| Load all skips UNIFIED | kb_loader | YES |
| Load family loads specific | kb_loader | YES |
| Load family unknown returns empty | kb_loader | YES |
| Load family missing file returns empty | kb_loader | YES |
| Stats returns counts | kb_loader | YES |
| Handles empty canonical dir | kb_loader | YES |
| Merge unified combines families | kb_builder | YES |
| Merge unified deduplicates by ID | kb_builder | YES |
| Merge unified skips existing UNIFIED | kb_builder | YES |
| Append to family adds new | kb_builder | YES |
| Append to family skips duplicates | kb_builder | YES |
| Append creates file if missing | kb_builder | YES |
| Add auto ID | archive_registry | YES |
| Save/load roundtrip | archive_registry | YES |
| Search by client | archive_registry | YES |
| Search by family | archive_registry | YES |
| Search by date range | archive_registry | YES |
| Empty registry | archive_registry | YES |
| Get by ID | archive_registry | YES |
| Next ID increments | archive_registry | YES |
| RawRow defaults | extraction_models | YES |
| ClassifiedRow all fields | extraction_models | YES |
| ExtractionResult counts | extraction_models | YES |
| StructureMap holds sheet info | extraction_models | YES |
| Upsert returns count | chromadb_client | YES |
| Query returns matches | chromadb_client | YES |
| Query filters by threshold | chromadb_client | YES |
| Count returns total | chromadb_client | YES |
| Rebuild clears and reindexes | chromadb_client | YES |

### Phase 4: Excel Agent Tests (22 tests)
| Test | Module | Passing? |
|------|--------|----------|
| Green cells detected (6 rows) | cell_detector | YES |
| Answered cells not detected | cell_detector | YES |
| Category header not detected | cell_detector | YES |
| Question text extracted | cell_detector | YES |
| Merged cell handled | cell_detector | YES |
| Special characters preserved | cell_detector | YES |
| Header row detection | cell_detector | YES |
| Question column detection | cell_detector | YES |
| Answer column detection | cell_detector | YES |
| is_green_cell exact match | cell_detector | YES |
| Write answer sets value | answer_writer | YES |
| Formula preserved after write | answer_writer | YES |
| Answered cells unchanged | answer_writer | YES |
| Save produces valid workbook | answer_writer | YES |
| Merged cells survive write | answer_writer | YES |
| Process dry run | excel_agent | YES |
| Process writes answers | excel_agent | YES |
| Process preserves answered cells | excel_agent | YES |
| Process preserves formula | excel_agent | YES |
| Process applies anonymization | excel_agent | YES |
| Process applies overrides | excel_agent | YES |
| Result tracks counts | excel_agent | YES |

### Phase 5: Word Agent Tests (21 tests)
| Test | Module | Passing? |
|------|--------|----------|
| Section tree detection | section_parser | YES |
| Integration subsections | section_parser | YES |
| Content paragraph counts | section_parser | YES |
| Answerable sections | section_parser | YES |
| Breadcrumbs | section_parser | YES |
| Heading level detection patterns | section_parser | YES |
| Count sections recursive | section_parser | YES |
| Full content property | section_parser | YES |
| Full content with children | section_parser | YES |
| Insert after para correct | section_parser | YES |
| Blue text insertion position | answer_inserter | YES |
| Blue text formatting | answer_inserter | YES |
| Table preservation | answer_inserter | YES |
| Document integrity | answer_inserter | YES |
| Has existing response | answer_inserter | YES |
| Process dry run | word_agent | YES |
| Process writes answers | word_agent | YES |
| Process skip existing | word_agent | YES |
| Process applies anonymization | word_agent | YES |
| Process applies overrides | word_agent | YES |
| Result tracks counts | word_agent | YES |

### Findings
- `kb_build_canonical.py` and `kb_embed_chroma.py` have no argparse/--help (run directly) -- excluded from CLI smoke tests
- `total_answerable_sections` is 6 (not 7): chapter heading "VIII. Architecture" has no own content paragraphs
- `pythonpath = ["src"]` added to pyproject.toml for corp_rfp_agent imports in tests
- corp-os-meta not installed -- taxonomy.py uses local fallback (family_config.json data)

## Baseline Tag
- `v0.3.0-baseline` -- tagged before any architectural changes
