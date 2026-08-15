# StateSync — Strategy & Technical Design Document

**Project:** StateSync — Next-Gen Multi-Agent E-Commerce Analyst: Unifying Multilingual Semantic Parsing, Secure Context Protocols, and Declarative Visualizations
**Program:** B.Tech Capstone, AY 2026-27, Semester VII, Dept. of Artificial Intelligence, NMIMS MPSTME (Mumbai)
**Team:** Neel Jaiswal (I032), Ronak Daniel (I015) — 2 members
**Guide:** Prof. Jish Joy
**Document type:** Strategy & Technical Design. Establishes vision, capability pillars, roles, architecture, and phase planning. Exact schemas, formulas, API surfaces, prompt templates, and component-level acceptance criteria are deliberately absent and belong to the Master Build Specification, which will be written from this document.

---

## 1. Executive Summary & Vision

StateSync is a question-answering system for people who analyse decentralized commerce networks. An analyst types a question in plain language — in English, in Hindi, or in the Hinglish that most Indian operations teams actually speak — and StateSync answers it using both the company's relational fulfillment data and its pile of unstructured operational documents: supplier agreements, SOPs, dispute policies. The answer comes back as prose plus a chart, grounded in real rows, with a visible trail of how it got there.

That sounds like a thing that already exists. It mostly does not, and the reasons are specific.

**Schema amnesia.** Ask a conventional text-to-SQL agent the same class of question twice and it re-derives the schema from scratch both times. It re-reads table names, re-guesses which key joins to which, re-discovers that a timestamp column is stored as an integer. This is not merely wasteful — it makes the system *non-deterministic in cost and in quality*. The same question can succeed on Monday and fail on Tuesday because the model took a different exploratory path. StateSync treats prior reasoning as a first-class, persistent asset rather than something thrown away at the end of a request.

**The Indic Gap.** This is the sharpest problem in the project and the one most worth a capstone's attention. Frontier models degrade measurably when the question arrives in an Indian language: benchmarking work on IndicDB (Dawar et al., 2026) isolates a 9.00% execution-accuracy drop from English to Indic languages on normalized relational schemas. On the harder MultiSpider 2.0 benchmark — enterprise-grade schema complexity across eight languages — state-of-the-art models managed roughly **4% execution accuracy** under pure zero-shot reasoning. Four percent is not a system with rough edges; it is a system that does not work. And this is precisely the population StateSync targets: Indian logistics operations, where the question is in Hinglish and the entity names in the database are transliterated proper nouns that must survive translation *unchanged*.

**Execution risk in the last mile.** The conventional way to make an LLM draw a chart is to have it write Python and then run that Python on the server. This is a remote-code-execution vulnerability wearing a data-science costume, and it composes badly with the other risk in the stack: an agent that reads database fields is an agent that can be instructed by database fields. A malicious string in a customer review is a prompt. If that prompt can reach a code path that executes, the analytics tool becomes an exfiltration tool.

StateSync's response to all three is architectural rather than incremental. Reasoning is **remembered** in a semantic memory keyed to the schema it was derived from. Language is **normalized before schema linking**, not hoped through it. Database access happens exclusively through a **Model Context Protocol server** that holds the credentials the agents never see, over a connection that is read-only at the database user level — so a successful prompt injection still cannot write. And the final visualization is emitted as **declarative Vega-Lite JSON**, a data structure that describes a chart rather than a program that draws one.

The vision, stated plainly: *an analyst asks a hard, multilingual, hybrid question about a decentralized supply chain and gets a correct, cited, charted answer — and the system is structurally incapable of being talked into doing anything else.*

**What success looks like for this capstone.** A working prototype over synthetic ONDC-shaped data, demonstrating measurable improvements on three axes a panel can independently check: token and trajectory reduction from semantic memory, accuracy lift on a fixed Indic/Hinglish query set against a no-mitigation control, and a passing injection test suite. Not a product. A defensible, demonstrable argument.

---

## 2. Capability Pillars

Five pillars. Every agent, decision, story, metric, and phase in the rest of this document maps to at least one.

### Pillar I — Stateful Orchestration & Memory
The system holds a single typed state object that every node reads from and writes to, and it remembers across requests. This covers query decomposition and routing, the self-correction loop that catches and repairs failed queries, and the semantic memory that turns yesterday's successful reasoning into today's shortcut. *This is the pillar that makes StateSync a system rather than a prompt.*

### Pillar II — Secure Structured Data Access
Every touch of the database goes through the MCP server. Agents receive tools, never credentials. The connection is read-only at the database user level, the exposed tool surface is deliberately small, and protocol interactions run isolated. *This is the pillar the security narrative rests on.*

### Pillar III — Multilingual Query Understanding
Detection of Indic/Hinglish input and normalization of *intent* to English for schema linking, while localized entity strings are preserved verbatim so they still match the rows they name. *This is the pillar carrying the project's clearest novelty claim.*

### Pillar IV — Hybrid Knowledge Fusion
Answering questions that no single store can answer: relational fulfillment metrics joined against unstructured policy documents, and both filtered by geography at a precision traditional postal codes cannot express. Structured querying, layout-aware document ingestion, semantic vector search, and DIGIPIN-derived spatial filtering all live here. *This is the pillar that makes the answers useful rather than merely retrievable.*

### Pillar V — Safe Output & Continuous Evaluation
The synthesis step, constrained to a declarative output contract, plus the evaluation pipeline that runs continuously rather than once before the demo. *This is the pillar that keeps the other four honest.*

Note that **spatial intelligence is folded into Pillar IV rather than standing alone.** DIGIPIN decoding is a deterministic coordinate transform feeding a spatial filter — it is a capability of hybrid querying, not a peer of it. Section 4 explains why this reclassification also changes the agent roster.

---

## 3. Roles & Permissions

StateSync is a research and analytics tool operated by a small number of people. An elaborate role hierarchy would be fiction. Two roles, honestly scoped:

### Analyst (end user)
The person asking questions. Can submit natural-language queries in any supported language, view returned answers with their supporting data and rendered charts, inspect the reasoning trace for a given answer (which agents ran, which query was generated, which documents were retrieved), and re-run or refine a prior query within a session.

Cannot: see database credentials, write or modify data, add or alter the exposed tool surface, edit ingested documents, change feature flags, or invoke the MCP server outside a graph-mediated request.

### Administrator (configuration)
The person operating the deployment — in practice, a team member. Can manage which MCP tools are exposed and their throttling policy, trigger and inspect document ingestion, manage the schema version registered with the system, inspect and invalidate semantic-memory trajectories, toggle feature flags governing stretch components, and view evaluation-pipeline output.

Cannot: bypass the read-only database constraint. **This is deliberate.** The `SELECT`-only restriction is enforced at the database user level, not in application logic, which means it is not an administrator-adjustable setting — it is a property of the connection. An administrator who needs to write to the database does so through a separate, non-agent-facing path entirely outside StateSync. This is the single most important permission statement in this section: *no role in this system, and therefore no successful attack against any role in this system, can write to the database through the agent layer.*

### A note on tenancy
Security language across the source material refers to cross-tenant leakage prevention. StateSync as built is **single-tenant** — one analyst population, one dataset. Claiming multi-tenancy to a panel while shipping a single-tenant demo would be dishonest, and building real multi-tenancy would materially inflate MCP server complexity for zero demonstrable benefit at this scale. The build is single-tenant; the *seam* for tenancy is preserved (Section 6). Reasoning is logged in Section 9.

---

## 4. High-Level Architecture

### 4.1 Critique of the proposed agent roster

The source design specifies six agents: Supervisor, SQL, Unstructured Knowledge Engine, COLA, Geocoding, and Synthesizer. Before describing what to build, it is worth asking whether six is right — because in multi-agent systems the agent count is a cost, not a feature. Every node boundary is a serialization point, a place where context is lost in translation, a routing decision the Supervisor can get wrong, and an extra hop of latency. A six-node graph looks impressive on an architecture slide. It is not automatically better than a four-node one.

Two of the six do not earn their node boundary.

**The Geocoding Agent should not be an agent.** Its entire job is to take a 10-character DIGIPIN string and return a latitude/longitude pair. That is a deterministic function call against an open-source decode library. It involves no reasoning, no model inference, no possibility of being wrong in an interesting way, and no decision the Supervisor benefits from routing around. Making it a peer agent means the Supervisor must classify a query as "spatial", route to a node, receive floats back, and route again to the SQL Agent — three coordination steps to accomplish a library call the SQL Agent could have made itself. **Decision: DIGIPIN decoding becomes a deterministic pre-step inside the SQL Agent's query-construction path.** Capability is unchanged; three routing decisions become zero. This also removes an entire class of failure — the Supervisor misclassifying a spatial query — because there is no longer a spatial route to miss.

**COLA should be a preprocessor, not a peer.** The source workflow places COLA as one of four branches off the Supervisor, which implies a multilingual query goes to COLA *instead of* going to the SQL Agent. But that is not what COLA does. It normalizes the language of a query and then the query still has to be answered — possibly by the SQL Agent, possibly by the document engine, possibly by both. Language is a property of the *input*, orthogonal to which capability the question needs. Modelling it as a sibling branch conflates two independent dimensions and forces the Supervisor to make one decision where it should make two. **Decision: language normalization runs as a conditional gate on the way *into* the Supervisor's routing decision, not as a destination it routes to.** A Hinglish question about supplier policies then correctly reaches the document engine, which in the source topology it structurally could not.

**The other four survive the review unchanged in principle.** Supervisor, SQL, Unstructured Knowledge, Synthesizer are genuinely distinct reasoning responsibilities with genuinely different failure modes. Merging any pair of *those* would recreate exactly the divided-attention problem this project exists to solve.

Result: **four reasoning nodes, two deterministic subroutines.** The capability surface is identical to the six-agent design. The coordination surface is a third smaller. For a two-person team, that difference is the difference between a working demo and an integration debugging spiral.

### 4.2 The roster as built

**Supervisor.** The cognitive core and the only node that decides where work goes. It parses the analyst's intent, decomposes a multifaceted question into sub-tasks, dispatches them to the specialist nodes via conditional edges, and aggregates returning payloads for synthesis. Its most important design property is *determinism under load*: the more capabilities it can see, the worse it routes. Research on MCP server architecture patterns (Anonymous, 2026) formally identifies that LLM tool-selection accuracy drops below 90% once the context window is crowded with too many exposed capabilities. The Supervisor's tool surface is therefore capped in the 10–15 range and throttled per context window — a constraint on the whole system, not a tuning parameter.

**SQL Agent.** Owns everything relational. Given a sub-task, it first consults semantic memory for a prior reasoning trajectory against the current schema; on a hit, it adapts the remembered approach rather than rediscovering it. On a miss — a genuinely novel question — it falls back to active interrogation: issuing cheap preliminary sampling queries to verify its hypotheses about the data's actual shape before committing to a full synthesis. It also performs DIGIPIN decoding inline when a query carries spatial constraints, converting grid codes to coordinates and building the corresponding spatial filter. Every query it produces executes through MCP; it never holds a credential. When execution fails, the error is categorized and written back into shared state, and the agent gets another attempt with that diagnosis in hand.

**Unstructured Knowledge Engine.** Owns everything documentary. Operates over policy documents, SOPs, and supplier agreements that were ingested through a layout-aware parsing pipeline — a hard requirement, because standard OCR destroys the row/column relationships in tables, and tables are where the answers in supplier agreements actually live. Retrieval is semantic, executed through vector search expressed inside SQL rather than as a separate retrieval system, so that a single query can filter on a relational condition and a semantic condition at once. For genuinely hybrid questions it decomposes into sub-queries and reasons about whether it has retrieved enough before answering.

**Synthesizer.** The terminal node. Aggregates every payload in state into a natural-language answer plus a chart specification. Its defining property is a negative one: **its output contract admits only declarative JSON.** It cannot emit a program, because there is no field in its output where a program would go and no consumer downstream that would run one.

**Deterministic subroutines** (not nodes): DIGIPIN decode, inside the SQL Agent; language normalization, gating the Supervisor.

### 4.3 End-to-end workflow

A query moves through the system as follows.

The analyst's question arrives and is written into a typed global state object. Before routing, a **language gate** inspects it: if the query is Indic or Hinglish, a normalization pass rewrites the *intent* into English suitable for schema linking while carrying localized entity strings through untouched — a shop name transliterated from Devanagari must still match the row it names, so translating it would be actively harmful. English queries pass through the gate unmodified.

The **Supervisor** then decomposes the normalized query and routes. A question about delivery times goes to the SQL Agent. A question about return policy goes to the Unstructured Knowledge Engine. A question about *whether delivery performance in a given area violates the supplier's contracted SLA* goes to both, as parallel sub-tasks.

The **SQL Agent**, on receiving a sub-task, checks semantic memory against the current schema fingerprint. A valid hit gives it a prior trajectory to adapt. A miss sends it to active interrogation: sample first, synthesize second. If the sub-task carries a DIGIPIN, it decodes inline and builds the spatial filter. The finished query goes to the **MCP server**, which is the only component holding credentials and which executes strictly read-only against PostgreSQL/PostGIS and the vector store. Success returns a dataframe into state. Failure returns a *categorized* error into state — not a raw string, but a classified diagnosis in the manner of SQL-of-Thought's error taxonomy (Chadha et al., 2025) — and the graph loops back to the SQL Agent for a repair attempt with that diagnosis available. This loop is a first-class edge in the graph, bounded by a retry ceiling, not an exception handler bolted on the side.

The **Unstructured Knowledge Engine**, in parallel, retrieves against ingested documents, decomposing iteratively if the question needs multiple hops, and writes its findings and their provenance into state.

When all dispatched sub-tasks have returned, the **Synthesizer** reads the accumulated state and produces prose plus a Vega-Lite specification. The **evaluation pipeline** scores that specification against expectations continuously, not as a pre-demo ritual. The **frontend** parses the JSON and renders the chart natively.

The state object is the spine of all of this. Every node reads it and writes to it; reducers govern how concurrent writes merge and how the error history accumulates across correction cycles. There is no side channel between nodes.

### 4.4 Core technical bets

**Semantic memory over stateless re-derivation.** *Endorsed as proposed.* AgentSM (Biswal et al., 2026) reports 44.8% execution accuracy on Spider 2.0 Lite alongside a 25% average token reduction and 35% shorter trajectories — and crucially, it stores *abstracted task-level insight* (which joins were used, how schema linking resolved, what dialect quirks bit) rather than raw question-answer pairs, which is what makes it generalize to a rephrased question instead of only matching an identical one. The efficiency numbers are the headline, but the reduction in *reasoning variance* is the stronger argument for a system that has to behave the same way in front of a panel as it did in testing. The source material's one gap here — no mitigation for schema drift invalidating cached traces — is closed below and in Section 9.

**MCP over direct database connections.** *Endorsed, with the security framing sharpened.* The source correctly adopts MCP's Stateful Session Server pattern for multi-turn context preservation. But MCP is not secure by default, and the strategy narrative should say so rather than treating the protocol as a safety guarantee. Security analysis of the specification (Maloyan & Namiot, 2026) finds standard implementations lack mandatory sandboxing and origin boundaries, leaving them open to tool poisoning — a malicious instruction embedded in a database field, such as a customer review, ingested and acted upon by the agent, potentially triggering exfiltration through whatever other tools are enabled. **The security property StateSync claims comes from defense-in-depth on top of MCP, not from MCP:** isolated execution of protocol interactions, a `SELECT`-only database user so destructive commands are impossible regardless of what the model was persuaded to emit, and a throttled tool surface that keeps routing deterministic. The honest claim is "credentials never enter the agent layer and the blast radius of a successful injection is bounded to reads" — which is a strong claim, and a true one.

**Declarative Vega-Lite over dynamic code execution.** *Endorsed, with the reasoning corrected.* The rejected approach — LLM writes Matplotlib, backend executes it — is a genuine RCE surface and correctly discarded. But the source material is careful about something the strategy must not overstate: **WebAssembly sandboxes such as Pyodide are not being rejected because they are insecure.** They are a legitimate, safer alternative in the literature. StateSync goes declarative-only for UX, payload weight, and lifecycle-management reasons — a dashboard that ships a JSON spec is lighter and more interactive than one shipping a Python runtime. RCE-immunity is a *structural consequence* of that choice: with no execution path anywhere in the pipeline, the vulnerability class is absent rather than mitigated. That is the stronger and more defensible framing, and it survives a panel member who knows what Pyodide is.

**DIGIPIN over generic geocoding.** *Endorsed as proposed.* India Post's DIGIPIN divides the country into uniform 4m × 4m grid squares, each with a unique 10-character alphanumeric code derived from exact coordinates. The alternatives fail in different ways: traditional postal PIN codes cover thousands of households and break down precisely where last-mile delivery is hardest, in dense urban centres; descriptive landmark addresses are human-readable but unparseable for analytics and error-prone in transcription. DIGIPIN is the only Indian addressing standard that is simultaneously precise enough for meaningful spatial aggregation and structured enough to compute on. Using it rather than a generic geocoder is not a novelty flourish — it is the correct engineering choice for hyperlocal Indian logistics, and it is what makes hyperlocal analytics *possible* rather than approximate.

**A bet the source material makes implicitly and this document makes explicit: the retrieval stack is over-specified.** The source names CoRAG, CSR-RAG, TableRAG, and Text2VectorSQL as the unstructured/hybrid retrieval layer. These substantially overlap, and adopting all four reads as citing the literature rather than choosing from it. Assessed on merit for this team:

- **Text2VectorSQL** (Wang et al., 2025) is *core*. Embedding a semantic vector function directly inside SQL `WHERE` clauses, executing approximate-nearest-neighbour search in the database, collapses what would otherwise be two systems into one and shifts unstructured filtering from linear scans to logarithmic. It is the single highest-leverage component in the retrieval stack.
- **Layout-aware ingestion** is *core and non-negotiable*, because everything downstream inherits its quality — Text2VectorSQL's documented failure mode is recall degradation from poor upstream chunking.
- **CoRAG-style iterative decomposition** (Marketsmüller et al., 2026) is *core but gated*. It delivers a statistically significant improvement in hybrid settings at a documented 2–3x LLM call overhead. It therefore runs only when a query is genuinely hybrid, never on pure-SQL or pure-document paths.
- **CSR-RAG** (Singh et al., 2026) is *deferred to stretch*. Its hypergraph pre-computation over schema foreign-key dependencies exists to solve massively denormalized enterprise schemas, and it requires continuous re-computation whenever the schema changes. A synthetic capstone schema does not have that problem, and the maintenance burden is real. Building it would be solving a problem this project does not have, to cite a paper.
- **TableRAG** (Yu et al., 2025) is *absorbed, not built as a component*. Its central insight — that tabular data in documents should be queried programmatically rather than chunked into a vector index — is already realized by feeding layout-aware-parsed tables into the Text2VectorSQL path. Its documented cost is an intensive offline database-construction phase; adopting the insight without the separate framework avoids paying that cost twice.

### 4.5 Core versus stretch

**Core** — the project is not complete without these:

- Typed global state, graph orchestration, conditional routing, and the bounded self-correction loop with categorized errors.
- MCP server as sole database access path; `SELECT`-only enforcement at the database user level; throttled tool surface; isolated protocol execution.
- SQL Agent with semantic-memory lookup and active-interrogation fallback.
- Language normalization gate with entity preservation (prompt-based).
- Inline DIGIPIN decode and PostGIS spatial filtering.
- Layout-aware document ingestion and Text2VectorSQL hybrid retrieval, with gated iterative decomposition for hybrid queries.
- Synthesizer with a declarative-JSON-only output contract; frontend native rendering.
- Deterministic specification-correctness scoring in a continuous evaluation pipeline.
- An injection and RCE test suite that demonstrably passes.
- Containerized deployment of the full stack.

**Stretch** — valuable, documented as future work if not reached:

- GRPO/contrastive-reward fine-tuned multilingual model behind the normalization interface.
- VLM-based Vision Score alongside deterministic spec scoring.
- CSR-RAG hypergraph schema retrieval.
- Real multi-tenancy with cross-tenant isolation testing.
- Hypergraph-based root-cause analysis in the manner of HEAR.

**Two resolutions the source material left open, decided here.**

*GRPO fine-tuning is stretch, designed but not trained.* The result is genuinely impressive — a 3-billion-parameter model gaining **+27.43 percentage points** of execution accuracy from only 3,000 training examples, outperforming an 8-billion-parameter zero-shot model (Kattamuri et al., 2025). It is also a full ML-engineering project: constructing the training set, standing up the training loop, tuning heuristically-chosen reward weights, and depending on the quality of a multilingual encoder. Two students in one semester cannot do that *and* deliver the orchestration, security, retrieval, and evaluation work above. Prompt-based collaborative normalization is the guaranteed core, and it is not a weak fallback — the COLA baseline (Pham et al., 2025) lifts MultiSpider 2.0 execution accuracy from the 4% zero-shot floor to as much as 15% with no task-specific fine-tuning. The document is explicit that 15% is not production-grade; for a capstone, a measurable and defensible lift over a control path is the deliverable, not a production system.

*Graph-based orchestration is retained.* The source adopted LangGraph without evaluating it, which is a fair criticism and worth answering rather than deflecting. The counter-proposal — a hand-rolled state machine — is genuinely simpler to start and steadily worse to finish. The three things this system depends on most are exactly the three things the framework provides: a typed shared state with reducer-governed merges, conditional edges, and a cycle in the graph for self-correction. Hand-rolling those means writing and debugging a small orchestration framework as a side quest, with the bugs landing in the retry-and-error-accumulation logic — the least observable part of the system and the part a demo failure would most likely come from. The framework's learning cost is bounded and front-loaded; the hand-rolled cost is unbounded and arrives late. Retained, with the reasoning now on record.

### 4.6 Extension seams

Wherever something above is simplified or deferred, a specific seam preserves the option. Each is elaborated in Section 6.

| Deferred / simplified | Seam that preserves it |
|---|---|
| Prompt-based normalization instead of a fine-tuned model | Language normalization is an abstract interface with a swappable provider; the Supervisor never learns which implementation answered |
| DIGIPIN decode demoted from an agent | Decode sits behind a spatial-resolution interface inside the SQL Agent; a richer geocoding provider slots in without touching routing |
| Deterministic spec scoring only | The evaluation pipeline takes a list of scorers; adding a vision scorer is registering another one |
| CSR-RAG not built | Schema retrieval is called through one boundary; the hypergraph implementation is an alternative behind it |
| Single-tenant | A tenant identifier is threaded through MCP session context and memory trace keys from day one, unused but present |
| Semantic memory could be wrong | Every trajectory carries a schema fingerprint and is validated on read |
| Stretch components generally | Feature-flag gated, shipped disabled |

---

## 5. Representative User Stories

Illustrative, not exhaustive. Acceptance criteria are deferred to the Build Specification.

### Pillar I — Stateful Orchestration & Memory
- As an **analyst**, I want a question I asked last week to be answered faster and more consistently this week, so that I can rely on the system rather than re-checking whether it got lucky.
- As an **analyst**, I want to see which agents ran and what query was generated, so that I can trust an answer before I act on it.
- As an **analyst**, I want a query that initially fails to be repaired automatically, so that a malformed join is the system's problem rather than mine.
- As an **administrator**, I want cached reasoning to be discarded automatically when the schema changes, so that the system never confidently answers using a stale assumption.

### Pillar II — Secure Structured Data Access
- As an **administrator**, I want the agents to have no database credentials at all, so that compromising the model layer does not compromise the data layer.
- As an **administrator**, I want to control exactly which capabilities the Supervisor can see, so that routing stays deterministic as the system grows.
- As an **analyst**, I want to know that no question I ask — and no instruction hidden in the data — can modify or delete anything, so that I can explore freely.

### Pillar III — Multilingual Query Understanding
- As an **analyst**, I want to ask "Mumbai mein pichhle mahine kitne orders late the?" and get the same answer as the English equivalent, so that I can work in the language I think in.
- As an **analyst**, I want transliterated shop and city names in my question to match the records they refer to, so that translation does not silently destroy my filter.
- As an **administrator**, I want the multilingual path to be measurable against a control path, so that the improvement is a number rather than a claim.

### Pillar IV — Hybrid Knowledge Fusion
- As an **analyst**, I want to ask whether a supplier's delivery performance breaches the terms in their agreement, so that I get one answer instead of a spreadsheet and a PDF.
- As an **analyst**, I want answers drawn from documents to cite the passage they came from, so that I can verify the system did not invent the clause.
- As an **analyst**, I want to aggregate fulfillment metrics within a precise radius of a DIGIPIN location, so that I can diagnose last-mile failures at the granularity they actually occur.
- As an **analyst**, I want tables inside supplier PDFs to be queryable, so that the numbers in the appendix are as reachable as the numbers in the database.

### Pillar V — Safe Output & Continuous Evaluation
- As an **analyst**, I want an appropriate chart alongside the prose, so that I can see the shape of the answer rather than reading it.
- As an **administrator**, I want every generated chart specification scored automatically, so that a regression is caught by the pipeline rather than by the panel.
- As an **administrator**, I want an adversarial query suite to run continuously, so that "it is secure" is a passing test rather than an assertion.

---

## 6. Extension Hooks & Deferred Scope

Deliberately not built now; deliberately not precluded. Each entry names the mechanism.

**Fine-tuned multilingual model.** *Mechanism: provider slot behind an abstract interface.* Language normalization is invoked through one interface with one contract — take a query, return a normalized query plus a preserved-entity map. A prompt-based implementation ships. Swapping in a GRPO/contrastive-reward fine-tuned open-weight model is registering a different provider; the Supervisor, the state schema, and every downstream node are untouched. The A/B path this creates is itself useful: it lets the improvement be measured against the shipped implementation rather than asserted.

**Vision-based visualization scoring.** *Mechanism: pluggable scorer registry, feature-flag gated.* The evaluation pipeline invokes an ordered list of scorers. Deterministic specification scoring — free, fast, no model call — is registered and enabled. A VLM-based scorer of the rendered image is designed and gated off. The correlation gap with human judgment is real but modest (roughly 0.71 versus 0.65 Pearson), which does not justify a model call per evaluation run during the build.

**Hypergraph schema retrieval.** *Mechanism: alternative implementation behind the schema-retrieval boundary.* The SQL Agent asks one component "which schema elements matter for this query." Direct inspection plus semantic memory answers it now. A CSR-RAG hypergraph implementation — contextual, structural, and relational pipelines in parallel, with its documented pre-computation requirement — is an alternative behind the same boundary, worth adding only if the schema grows to a scale where the current approach measurably degrades.

**Multi-tenancy.** *Mechanism: tenant identifier threaded but unused.* A tenant identifier is part of MCP session context and forms part of every semantic-memory trace key from the first commit. In the single-tenant build it holds one constant value. Enabling real multi-tenancy means enforcing it at the MCP policy layer and testing isolation — not retrofitting an identifier through every store, which is the expensive half and the half that is done up front. This is why the security narrative can honestly say cross-tenant trace leakage is *designed against* while the build is *tested single-tenant*.

**Root-cause analysis.** *Mechanism: reserved specialist node behind a routing flag.* HEAR (Wang et al., 2026) reports 94.7% accuracy on supply-chain root-cause tasks using a stratified hypergraph ontology with open-weight backbones. That is directly relevant to StateSync's stated application of automated supply-chain diagnostics, and it is a natural next agent. It is not this project's scope. The Supervisor's routing table is data, not hardcoded branches, so adding a specialist is registering a node and a route — the seam is the routing table itself.

**Richer spatial resolution.** *Mechanism: spatial-resolution interface inside the SQL Agent.* Today it decodes DIGIPIN. Free-text address resolution, reverse geocoding, or consent-mediated address-label sharing in the direction of India Post's DHRUVA framework are alternative implementations of the same interface. Because decoding was deliberately kept out of the routing layer, extending it never touches the Supervisor.

**Live network integration.** *Mechanism: data-source boundary at ingestion.* All data is synthetic and ONDC-shaped. Every store is populated through an ingestion boundary rather than by hand, so a live Beckn-protocol source would be a new ingestion implementation, not a change to the query path. Explicitly out of scope for every phase.

**Write operations.** *Mechanism: none, intentionally.* There is no seam here and there should not be one. The read-only property is enforced at the database user level precisely so that it cannot be relaxed by configuration. Any future write capability would require a separate, non-agent-mediated path and a fresh threat model. Recording this as a deliberate non-hook: *the absence of an extension point is itself a design decision.*

---

## 7. Success Metrics

### §7.0 — Model configuration and what the reported numbers mean

All development and Verification to date runs on a **local generator
(`qwen2.5:7b`) and local embedder (`mxbai-embed-large`)**, selected through the
BD1 provider seam (`flags.llm.provider`) and BD2 embedding config. The
**graded/production configuration uses Anthropic** through that same seam; the
key is provisioned separately.

The metric set in §7 divides into two classes with respect to model choice:

**Model-independent — stand as measured on any model:**
- **M1 direction** (memory reduces tokens/trajectory — a control-vs-treatment A/B).
- **M8** (adversarial security suite — binary, structural).
- **M9** (routing determinism ≥ 90% — a classification property).
- **M10** (reproducibility — the stack stands up and answers).
- All structural Verification steps (network isolation, credential absence, the
  three SELECT-only layers, role separation, tool ceiling, seam resolution,
  output-contract enforcement).

**Model-sensitive — measured and reported on the local model, but re-baselined
on the graded configuration before being reported as final:**
- **M4** (query correctness, cache-hit and novel).
- **M6** (grounding precision / recall / faithfulness).
- **M7** (visualization *quality* score; M7's *validity/rejection* half is
  model-independent and stands).
- **M2** (consistency) and any Verification step whose written pass-condition is
  sensitive to generation quality — specifically **C1-V3**, and the
  quality-dependent conditions of **C2-V1** and **D-V6** (see BD6).

Local-model figures are retained on record for honesty and for the A/B, and are
never deleted or smoothed. They are simply **not** the graded figures. The
day the Anthropic key lands, the re-baseline runbook (`docs/rebaseline_runbook.md`)
is executed and BD6 is resolved (confirmed model-ceiling, or reverted to open
blocks) before any model-sensitive metric is claimed as final.

---

Checkable by a panel, grounded in the figures the design is justified by.

**M1 — Memory efficiency.** On a fixed set of repeated and rephrased queries, the semantic-memory path shows a measurable reduction in token consumption and reasoning-trajectory length against a memory-disabled control on identical inputs. AgentSM's published figures (−25% tokens, −35% trajectory length) are the reference point, not the pass mark; a capstone-scale reproduction of the *direction and rough magnitude* is the goal, and the control-versus-treatment comparison is what makes it evidence.

**M2 — Consistency.** Repeated execution of the same query set produces stable results across runs, with variance measurably lower on the memory-enabled path. This matters as much as raw accuracy: a system that answers differently each time cannot be demonstrated.

**M3 — Multilingual lift.** On a held-out set of Hindi and Hinglish queries, the normalization path achieves execution accuracy measurably higher than a no-normalization control on the same queries against the same schema. Additionally: localized entity strings are preserved verbatim through normalization at a high, reported rate — a translated entity name is counted as a failure even when the surrounding SQL is correct, because it silently returns the wrong rows. The 9.00% Indic Gap and the 4%→15% baseline are the framing; the deliverable is StateSync's own measured delta against its own control.

**M4 — Query correctness.** Execution-based evaluation over a held-out query set: run generated and ground-truth queries, compare resulting dataframes, so that syntactically different but semantically identical queries count as correct. Reported separately for cache-hit and novel-query paths, since these are different mechanisms and averaging them hides which one is working.

**M5 — Self-correction efficacy.** A measurable proportion of queries that fail on first execution are repaired within the retry bound. Reported with the failure taxonomy attached, so the panel can see *which* error classes the loop handles and which it does not — an honest limitation is worth more than a headline percentage.

**M6 — Grounding.** For document-derived answers, every factual claim traces to a retrieved passage, evaluated on retrieval precision and answer faithfulness in the RAGAS sense. Ungrounded claims are counted as failures regardless of whether they happen to be true.

**M7 — Visualization validity.** Generated chart specifications parse and render at a near-total rate, with deterministic specification-correctness scoring against reference specifications, recall-weighted — a missing encoding is penalized far more heavily than an extra stylistic property, because a chart missing an axis is wrong while a chart with an unexpected colour is merely different.

**M8 — Security, as a passing test suite.** This is the hardest metric to fake and therefore the most valuable. An adversarial suite must pass in full: injection attempts embedded in database field content do not cause the agent to invoke unintended tools; every attempted write, drop, or alter is rejected at the database user level; no credential appears anywhere in the agent layer or in any log; and no path exists from any model output to code execution. **This metric is binary. A partial pass is a fail.**

**M9 — Routing determinism.** With the tool surface held within the 10–15 ceiling, the Supervisor routes a fixed query set to the correct specialist above the 90% threshold the MCP architecture research identifies as the degradation point. A deliberate over-exposure test — flooding the tool surface and observing accuracy fall — turns the design constraint into a demonstrated finding rather than a cited one.

**M10 — Reproducibility.** The full stack stands up from the containerized definition on a clean machine and answers a canonical query set. A system a panel cannot run is a system a panel cannot evaluate.

---

## 8. Phased Roadmap

Five phases, ordered by dependency. No calendar dates — sequence is the constraint, time is not. Each phase ends in something that can be shown working at a milestone review, and each builds on a predecessor that already worked. The structure deliberately avoids the failure mode where nothing is demonstrable until everything is finished.

### Phase 1 — Walking Skeleton
**Advances:** Pillars I, II.

Stand up the containerized stack: relational store with the spatial extension, vector store, MCP server, orchestration backend, minimal frontend. Load synthetic ONDC-shaped logistics data. Define the typed global state. Implement a minimal graph — input, Supervisor, SQL Agent, Synthesizer — with no memory, no fallback, no multilingual handling, no documents. Register a small tool surface on the MCP server against a `SELECT`-only database user.

**Demonstrable:** an analyst types an English question in the browser; it traverses the real graph; the SQL Agent generates a query; the MCP server executes it read-only; a correct result renders. Separately, an attempted write is rejected at the database level, and the agent process is shown to hold no credentials.

**Done looks like:** the thinnest possible end-to-end path is real, not mocked, and the security property is demonstrated on day one rather than retrofitted. Every later phase is an addition to something already running.

### Phase 2 — Reliable Structured Querying
**Advances:** Pillar I, Pillar II.

Add semantic memory: trajectory storage in the vector tier, retrieval before query construction, schema-fingerprint stamping and validate-on-read invalidation. Add active-interrogation fallback for novel queries — preliminary sampling to verify data-shape hypotheses before synthesis. Add the self-correction cycle: categorized error classification written into state via reducers, a bounded loop back to the SQL Agent, accumulated diagnostic context across attempts.

**Demonstrable:** a query fails on first attempt with a diagnosable error and the system visibly repairs it, with the classified error and the second attempt both inspectable. A repeated-query benchmark run with memory enabled and disabled shows the token and trajectory reduction as a measured number. A deliberate schema change causes stale trajectories to be rejected rather than silently used.

**Done looks like:** the structured path is trustworthy rather than merely functional. M1, M2, M4, M5 are measurable here. This is the largest and riskiest phase — the self-correction loop is where subtle bugs live — and it is placed early on purpose, while there is room to recover.

### Phase 3 — Multilingual & Spatial
**Advances:** Pillar III, Pillar IV.

Add the language gate ahead of Supervisor routing: detection, prompt-based collaborative normalization to English intent, entity preservation. Add DIGIPIN decode as a deterministic pre-step in the SQL Agent, with spatial filter construction against the location-bearing tables.

**Demonstrable:** a fixed Hindi/Hinglish query set is run through both the normalization path and a control path with the gate disabled, and the accuracy difference is reported as a number with entity-preservation tracked separately. A DIGIPIN-scoped question returns a correct radius aggregation, verifiable against a manually computed answer.

**Done looks like:** the project's central novelty claim is backed by a measured before/after on its own system, not by a cited benchmark. M3 lands here. Note that this phase is where a control path is *built deliberately as a deliverable* — the ability to disable the mitigation and compare is worth as much as the mitigation.

### Phase 4 — Hybrid Knowledge & Safe Visualization
**Advances:** Pillar IV, Pillar V.

Build the ingestion pipeline: layout-aware parsing of policy and supplier documents into structure-preserving Markdown, chunking, embedding. Implement hybrid retrieval with the semantic vector function inside SQL. Add the Unstructured Knowledge Engine node with iterative decomposition gated to genuinely hybrid queries. Extend the Synthesizer to emit declarative chart specifications under a JSON-only output contract, and implement native frontend rendering.

**Demonstrable:** a question requiring both a supplier policy document and relational fulfillment metrics returns a single grounded answer citing the source passage, accompanied by a rendered chart. A tabular figure buried in a source PDF is shown to be correctly retrievable, evidencing that layout-aware parsing preserved what OCR would have destroyed.

**Done looks like:** the system answers questions no single data store could. M6 and M7 become measurable. The output contract is demonstrably declarative — the Synthesizer's output is shown to be inert data.

### Phase 5 — Evaluation, Hardening, Freeze
**Advances:** Pillar V, and verification of all others.

Stand up the continuous evaluation pipeline over a held-out query set with the deterministic spec scorer and execution-based query comparison registered. Build the adversarial suite: injection payloads seeded into database field content, attempted writes, attempted credential extraction, tool-surface over-exposure. Run the routing-determinism experiment. Verify clean-machine reproducibility from the container definition. Freeze for panel review, with documentation of every stretch item as designed-not-built and its seam identified.

**Demonstrable:** the full metric set from Section 7 reported as measured values, the adversarial suite passing in full, and the over-exposure experiment showing routing accuracy degrading as the tool surface grows — reproducing the sub-90% finding on StateSync's own system.

**Done looks like:** every claim in this document is a number or a passing test, and the limitations are stated rather than discovered by the panel.

### Scope sanity-check against the team constraint

Two students, part-time, alongside other coursework, one semester. Being honest about what that means:

**This fits, but with little slack, and only because of the reductions made in Section 4.** The four-node roster instead of six, DIGIPIN as a subroutine, one retrieval framework as core instead of four, and prompt-based normalization instead of a trained model together remove a substantial fraction of the original design's integration surface — which is where two-person projects fail, not in any individual component.

Where the risk actually sits: **Phase 2 is the phase that will overrun.** Self-correction loops are subtle, and the interaction between memory invalidation and the retry cycle is the kind of thing that works in isolation and misbehaves in combination. It is scheduled second, immediately after the skeleton, so an overrun eats slack rather than eating Phase 5.

Work splits naturally along a clean seam: one member on orchestration, memory, and the structured path (Phases 1–2, then multilingual in 3); one member on ingestion, retrieval, visualization, and evaluation (Phases 4–5, with the containerized stack and spatial work in 1 and 3). The MCP server and the state schema are shared interfaces both depend on and both must agree on early — these are the coordination points to settle in Phase 1 and then leave alone.

**If the timeline compresses, the pre-agreed order of retreat is:** Vision Score first (it is already stretch), then narrowing the document corpus in Phase 4 rather than dropping hybrid retrieval, then reducing the multilingual evaluation set size while keeping the control comparison intact. **What does not get cut under any circumstance:** the security test suite and the end-to-end demonstrable path. A StateSync that answers fewer question types but is verifiably secure and verifiably works is a passing capstone. A StateSync with every component half-integrated is not.

---

## 9. Decisions Log

Every open question from the source material, every conflicting framing, and every independent deviation. Format: decision, alternative considered, reasoning, revisit hook.

### D1 — Agent roster reduced from six to four nodes plus two subroutines
*Resolves source open question 1.*
**Decided:** Supervisor, SQL Agent, Unstructured Knowledge Engine, Synthesizer as reasoning nodes. DIGIPIN decode becomes a deterministic pre-step inside the SQL Agent. Language normalization becomes a gate ahead of Supervisor routing.
**Alternative:** keep all six as peer agents, as originally proposed.
**Why:** the Geocoding Agent performs a deterministic library call with no reasoning content — a node boundary around it buys three routing decisions and one new failure mode for nothing. COLA as a sibling branch conflates language (a property of the input) with capability (a property of the question), which structurally prevented a Hinglish document query from reaching the document engine. The remaining four have genuinely distinct failure modes and merging them would recreate the divided-attention problem the project exists to solve.
**Hook:** the Supervisor's routing table is data, not hardcoded branches — promoting a subroutine back to a node, or adding a new specialist, is a registration rather than a refactor.

### D2 — GRPO/contrastive fine-tuning is stretch, designed but not trained
*Resolves source open question 2.*
**Decided:** prompt-based collaborative normalization is the guaranteed core deliverable. The fine-tuned model is designed, documented, and unbuilt.
**Alternative:** train the model as a core deliverable.
**Why:** the published result is strong (+27.43pp for a 3B model on 3,000 examples, beating an 8B zero-shot model) but the work is a full ML-engineering project — dataset construction, training loop, heuristic reward-weight tuning, dependence on multilingual encoder quality. Two students cannot deliver that alongside orchestration, security, retrieval, and evaluation. The prompt-based baseline is not a token fallback: it lifts MultiSpider 2.0 accuracy from 4% to as much as 15% with no fine-tuning, and a measured lift over a control is what a capstone must demonstrate.
**Hook:** the abstract normalization interface with a swappable provider (Section 6) — the fine-tuned model drops in behind it and can be A/B'd against the shipped implementation.

### D3 — Graph-based orchestration retained
*Resolves source open question 3.*
**Decided:** keep LangGraph.
**Alternative:** a hand-rolled state machine, on the grounds that the framework was adopted without evaluation.
**Why:** the criticism of the *process* is fair and is answered here. On the merits, the three capabilities this system leans on hardest — typed shared state with reducer-governed merges, conditional edges, and a cycle for self-correction — are exactly what the framework provides. Hand-rolling them means debugging a homegrown orchestration layer as a side quest, with the bugs concentrated in retry and error-accumulation logic: the least observable code in the system. The framework's cost is bounded and front-loaded; the alternative's cost is unbounded and arrives late in the schedule.
**Hook:** nodes are written as plain functions over the state object, with framework specifics confined to graph wiring — the orchestration layer is replaceable without rewriting agent logic.

### D4 — Semantic memory staleness handled by schema fingerprinting with validate-on-read
*Resolves source open question 4, which no source document addressed.*
**Decided:** every stored trajectory is stamped with a fingerprint of the schema it was derived against. On retrieval, a mismatched fingerprint means the trace is discarded and the agent takes the novel-query path.
**Alternative:** scheduled background revalidation, or time-based expiry.
**Why:** validate-on-read is correct by construction — a stale trace can never be used, because the check happens at the moment of use rather than on a timer that might not have fired. It requires no background job for a two-person team to maintain and no tuning of an expiry window. Time-based expiry is strictly worse: it discards valid traces and retains invalid ones, and gets both wrong for the same reason, which is that elapsed time is not what makes a trace stale.
**Hook:** fingerprint granularity is a single policy decision — whole-schema today, per-table if invalidation proves too aggressive, without changing where the check happens.

### D5 — HEAR and Text2DB remain literature influence only
*Resolves source open question 5.*
**Decided:** neither becomes a component. Both are cited for the ideas they contribute.
**Alternative:** promote HEAR to a stretch component for root-cause analysis.
**Why:** HEAR's reported 94.7% on supply-chain root-cause tasks is directly relevant to StateSync's stated diagnostics application, which is exactly why it is tempting — and exactly why it should be resisted. It is a substantial new capability, not an increment on an existing one, and a stretch list that includes a whole new reasoning paradigm is a wish list. Text2DB is a conceptual influence on document ingestion, not an implementable component here.
**Hook:** HEAR is named in Section 6 as a reserved specialist node behind the routing table; the seam is the same one D1 created.

### D6 — Single-tenant build with a tenancy seam
*Resolves source open question 6.*
**Decided:** build and test single-tenant. Thread a tenant identifier through MCP session context and semantic-memory trace keys from the start, holding one constant value. Drop multi-tenancy claims from the narrative.
**Alternative:** build real multi-tenancy with isolation testing, matching the source material's security language.
**Why:** the source language claims a property the build would not have. Overclaiming to a panel is worse than scoping honestly, and real multi-tenancy would materially inflate MCP server complexity for no demonstrable benefit at capstone scale. Threading the identifier up front is cheap and does the expensive half of the work — retrofitting an identifier through every store later is the painful part; enforcing a policy on an identifier that already exists is not.
**Hook:** the threaded identifier itself, plus MCP policy enforcement as the single place tenancy would be enforced.

### D7 — Retrieval stack reduced from four frameworks to one core plus one gated
**Decided:** Text2VectorSQL and layout-aware ingestion are core. Iterative decomposition is core but gated to genuinely hybrid queries. CSR-RAG is deferred to stretch. TableRAG's insight is absorbed into the ingestion path rather than built as a separate framework.
**Alternative:** implement all four as named components, as the source material lists them.
**Why:** this is the clearest case in the source material of citing the literature rather than choosing from it. The four overlap heavily. Iterative decomposition's documented 2–3x call overhead is only worth paying on hybrid queries. CSR-RAG solves massively denormalized enterprise schemas and requires continuous pre-computation on schema change — a synthetic capstone schema does not have that problem, so building it would be solving a non-problem to earn a citation. TableRAG's core insight, that document tables should be queried programmatically rather than vector-chunked, is realized by feeding layout-aware-parsed tables into the Text2VectorSQL path, without paying its documented intensive offline construction phase.
**Hook:** schema retrieval is called through one boundary, behind which the hypergraph implementation is an alternative; the hybrid-query gate is a policy, adjustable if the cost/benefit shifts.

### D8 — Declarative visualization justified on UX grounds, RCE-immunity as consequence
**Decided:** present declarative-only output as a choice made for UX, payload weight, and lifecycle management, whose structural consequence is the absence of an execution path. Do not claim WebAssembly sandboxes are insecure.
**Alternative:** the stronger-sounding framing that sandboxed execution is unsafe and declarative output is the secure alternative.
**Why:** it would not survive a panel member familiar with Pyodide. WASM sandboxing is a legitimate safer alternative in the literature, and the source material explicitly cautions against overstating its risk. The honest framing is also the stronger one: *the vulnerability class is absent rather than mitigated* is a better claim than *the vulnerability is contained*, and it is defensible under questioning.
**Hook:** none needed — the absence of an execution path is the feature, and Section 6 records the deliberate non-hook for write and execute capability.

### D9 — MCP security framed as defense-in-depth, not as a property of the protocol
**Decided:** state explicitly that MCP is not secure by default — that standard implementations lack mandatory sandboxing and origin boundaries and are vulnerable to tool poisoning — and attribute StateSync's security property to the mitigations layered on top.
**Alternative:** present MCP adoption as the security solution, as the source framing tends to imply.
**Why:** the threat-modeling research is unambiguous, and a panel that has read it will ask. Attributing the property to database-level read-only enforcement, isolated protocol execution, and a throttled tool surface is both accurate and a stronger technical claim: the blast radius of a successful injection is bounded to reads by a mechanism outside the model's reach.
**Hook:** mitigations are independent layers — additional measures such as handshake-time attestations can be added, at their documented latency cost, without altering the tool contract.

### D10 — Spatial intelligence folded into hybrid fusion rather than made a peer pillar
**Decided:** four pillars plus safe output; spatial capability lives inside Hybrid Knowledge Fusion.
**Alternative:** a standalone spatial intelligence pillar, as the prompt's example grouping suggests.
**Why:** consistent with D1. DIGIPIN decoding is a coordinate transform enabling a spatial filter on a query that is otherwise a normal hybrid query. Elevating it to a pillar would overstate a capability that is, in implementation terms, a decode call plus a bounding-box predicate — and pillar inflation is the same mistake as agent inflation.
**Hook:** the spatial-resolution interface inside the SQL Agent (Section 6) — if spatial reasoning grows into genuine route optimization or multi-hop spatial inference, it earns its own pillar and its own node then.

### D11 — Vision scoring deferred, deterministic spec scoring core
**Decided:** register the deterministic recall-weighted specification scorer; design and gate off the VLM-based vision scorer.
**Alternative:** implement both, as the source evaluation design implies.
**Why:** the deterministic scorer is free, fast, and needs no model call, and it is honest about its own limitation (over-sensitivity to schema complexity, blindness to styling). The vision scorer's correlation advantage with human judgment is real but modest — roughly 0.71 against 0.65 Pearson — which does not justify a VLM call per evaluation run during active development, when the pipeline runs constantly.
**Hook:** the pluggable scorer registry.

### D12 — Precision on cited statistics
**Decided:** the GRPO result is **+27.43 percentage points**, not the ~26pp an earlier internal draft rounded to. The Indic Gap is **9.00%**. The MultiSpider 2.0 zero-shot floor is **4%**, lifted **up to 15%** by prompt-based collaboration. Semantic memory reports **−25% tokens** and **−35% trajectory length**. The tool-selection degradation threshold is **below 90%**, motivating a **10–15 tool ceiling**.
**Alternative:** carry the rounded figures forward.
**Why:** a panel checks numbers against the cited papers, and a single loose figure undermines every other claim in the document. Recorded here so the Build Specification inherits the corrected values rather than re-importing the draft's error.
**Hook:** none — this is a correctness constraint, not a design choice.

### D13 — Control paths are deliverables, not test scaffolding
**Decided:** the memory-disabled path (Phase 2) and the normalization-disabled path (Phase 3) are built and maintained as first-class, feature-flagged capabilities rather than temporary comparison rigs.
**Alternative:** measure against published benchmark figures, as the source material's framing implies.
**Why:** this is an addition to the source design, and it is the one that most directly serves the capstone objective. A panel cannot verify a claim that StateSync improves on a published benchmark — different data, different models, different conditions. It *can* verify a live A/B on the same system, same data, same questions, one flag toggled. Every efficiency and accuracy claim in Section 7 becomes independently checkable in the room, which converts the project's novelty argument from citation into demonstration.
**Hook:** the flags themselves — the same mechanism gates every stretch component, so control paths and future providers share one switching layer.

### D14 — Errors are classified into state, not passed through as strings
**Decided:** execution failures are categorized before being written into shared state, following the structured error-taxonomy approach of SQL-of-Thought, with diagnoses accumulating across retry cycles via reducers.
**Alternative:** append the raw database error and retry, which is the simpler reading of the source workflow.
**Why:** raw errors give the agent a symptom; classified errors give it a diagnosis, and the published approach reports 91.59% execution accuracy on Spider by systematically rectifying logical flaws that static prompts miss. It also makes M5 reportable per error class, which turns the self-correction loop from a claim into evidence — and lets the document state honestly which failure classes are *not* handled.
**Hook:** the taxonomy is a registry — categories can be added from observed failures without touching the loop's control flow.

### D15 — Security demonstrated in Phase 1, not hardened at the end
**Decided:** database-level read-only enforcement and credential isolation are proven in the walking skeleton; the adversarial suite in Phase 5 extends a property that already holds rather than establishing one.
**Alternative:** implement functionality first and harden before the freeze, which is the conventional and the source-implied ordering.
**Why:** an architectural security property has to be true from the first commit or it is not architectural. Deferring it invites application-layer workarounds during development that are painful to unwind later, and it puts the project's most important binary metric (M8) at the point in the schedule with the least slack.
**Hook:** none — this is a sequencing commitment, and the point is that it is not adjustable.

---

## Appendix — Literature Grounding

Fifteen canonical sources. Listed with the specific claim each supports in this document; full review is out of scope here.

| Source | Claim it supports |
|---|---|
| AgentSM — Biswal et al., 2026 | Semantic trajectory memory; 44.8% Spider 2.0 Lite; −25% tokens, −35% trajectory length; abstracted task-level insight over raw Q&A |
| APEX-SQL — Cao et al., 2026 | Active interrogation via preliminary sampling; dual-pathway pruning; 70.7% BIRD-Dev, 51.0% Spider 2.0-Snow; added latency from round-trips |
| IndicDB — Dawar et al., 2026 | The Indic Gap: 9.00% execution-accuracy drop, English → Indic; benchmark only, no mitigation proposed |
| COLA / Multilingual Text-to-SQL — Pham et al., 2025 | Prompt-based collaborative mitigation; 4% MultiSpider 2.0 zero-shot floor lifted up to 15%; insufficiency of prompting alone |
| GRPO Contrastive Rewards — Kattamuri et al., 2025 | +27.43pp for a 3B model on 3,000 examples; reward-weight tuning fragility; basis for the stretch scoping in D2 |
| MCP Server Architecture Patterns — Anonymous, 2026 | Stateful Session Server pattern; sub-90% tool-selection accuracy under capability overload; the 10–15 tool ceiling |
| Breaking the Protocol — Maloyan & Namiot, 2026 | ProtoAmp threat framework; absent default sandboxing and origin boundaries; tool poisoning; attestation latency cost |
| Text2VectorSQL — Wang et al., 2025 | Vector functions inside SQL `WHERE`; O(N) → O(log N) unstructured filtering; recall degradation from poor chunking |
| CoRAG / RAG Variants — Marketsmüller et al., 2026 | Iterative decomposition with sufficiency reasoning; significant hybrid-setting improvement; 2–3x call overhead motivating the gate |
| CSR-RAG — Singh et al., 2026 | Hypergraph schema retrieval; >40% precision, ~80% recall, ~30ms latency; pre-computation burden motivating deferral in D7 |
| TableRAG — Yu et al., 2025 | Programmatic querying over document tables; avoidance of chunking loss; offline construction cost motivating absorption in D7 |
| SQL-of-Thought — Chadha et al., 2025 | Structured error taxonomy for guided repair; 91.59% Spider; token cost of correction loops |
| VegaChat — Hostnik et al., 2026 | Recall-weighted Spec Score and VLM Vision Score; ~0.65 vs ~0.71 human correlation motivating D11 |
| Text2DB — Jiao et al., 2024 | Conceptual influence on document ingestion as database integration; sublinear processing reduction |
| HEAR — Wang et al., 2026 | 94.7% on supply-chain root-cause tasks; basis for the reserved specialist node in Section 6 |

---

*End of Strategy & Technical Design Document. Exact schemas, field types, API surfaces, prompt templates, scoring formulas, and component-level acceptance criteria are deferred to the Master Build Specification, to be written from this document.*
