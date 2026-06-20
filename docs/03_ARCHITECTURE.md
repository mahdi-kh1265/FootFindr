# 03 — Architecture

## High-level architecture

```text
FootFindr CLI/API
│
├── KiCad I/O
│   ├── schematic reader/writer
│   ├── symbol field editor
│   ├── optional netlist exporter/parser
│   └── footprint library indexer
│
├── Core model
│   ├── components
│   ├── nets
│   ├── project/build sessions
│   ├── implementation variants
│   └── decisions
│
├── Part intelligence
│   ├── approved parts DB
│   ├── local inventory
│   ├── supplier providers
│   ├── datasheet cache
│   └── IC profiles
│
├── Resolve engine
│   ├── exact MPN resolver
│   ├── capacitor resolver
│   ├── resistor resolver
│   ├── IC resolver
│   ├── scoring/ranking
│   └── risk/block rules
│
├── BOM/cart/build layer
│   ├── BOM compiler
│   ├── export profiles
│   ├── inventory checker
│   ├── cart generator
│   └── freeze/manifest system
│
├── AI sidecar
│   ├── datasheet extractor
│   ├── eval-board importer
│   ├── draft IC profiles
│   └── report explanation assistant
│
└── Extensions
    ├── ff pwr     power calculation/checks
    ├── ff pwrlib  power-electronics libraries/templates
    └── ff rf      RF component/model extension
```

## Package layout

Recommended implementation tree:

```text
footfindr/
  pyproject.toml
  README.md
  docs/
  src/
    footfindr/
      __init__.py
      cli.py
      api.py
      config.py

      kicad/
        schematic.py
        sexpr.py
        fields.py
        writer.py
        netlist.py
        footprint_lib.py

      core/
        models.py
        units.py
        selectors.py
        graph.py
        decisions.py
        errors.py

      db/
        schema.py
        sqlite.py
        approved_parts.py
        migrations.py

      resolve/
        engine.py
        exact.py
        capacitors.py
        resistors.py
        ics.py
        scoring.py
        constraints.py
        risk.py

      inventory/
        backend.py
        local.py
        reservation.py
        build.py

      suppliers/
        base.py
        digikey.py
        mouser.py
        nexar.py
        cache.py

      datasheets/
        cache.py
        downloader.py
        extractor.py
        profiles.py

      bom/
        compiler.py
        profiles.py
        exporters.py
        cart.py

      ai/
        base.py
        profile_drafter.py
        validators.py

      reports/
        html.py
        json.py
        diff.py
        rich.py

      extensions/
        pwr.py
        pwrlib.py
        rf.py
```

## Core abstraction: implementation variant

A schematic symbol is not enough. FootFindr resolves symbols into implementation variants.

```python
@dataclass
class ImplementationVariant:
    internal_pn: str
    category: str
    manufacturer: str | None
    mpn: str | None
    package: str | None
    footprint: str | None
    specs: dict[str, Any]
    supplier_offers: list[SupplierOffer]
    documents: list[Document]
    models: list[SimulationModel]
    approved: bool
```

A footprint is just one property of the implementation variant.

## Typical `resolve` pipeline

```text
1. Load config and project context.
2. Read `.kicad_sch`.
3. Extract components and fields.
4. Build schematic graph/net context.
5. Load approved parts DB and policy.
6. Select target components from CLI target expression.
7. For each target:
   a. Skip if locked unless forced.
   b. Build ComponentContext.
   c. Generate Requirements.
   d. Generate candidate ImplementationVariants.
   e. Filter by hard constraints.
   f. Score candidates.
   g. Choose Decision.
   h. Verify footprint exists/matches.
8. Render diff/report.
9. If `--apply`, backup schematic and write selected fields.
10. Write decision log.
```

## Dry-run versus apply

Dry-run:

- Compute everything.
- Print summary.
- Write optional report/decision file.
- Do not edit schematic.

Apply:

- Backup schematic.
- Write only fields for decisions with status `AUTO_APPLY` and confidence >= threshold, unless user forced target.
- Preserve existing fields unless policy says overwrite.
- Generate decision log with old/new values.

## Decision statuses

```text
AUTO_APPLY      safe/high confidence, may write fields
SUGGEST_REVIEW  plausible but needs review, do not write by default
BLOCKED         high-risk or insufficient data, do not write
ERROR           parse/DB/footprint error, do not write
UNCHANGED       skipped because already correct or locked
```

## Confidence

Confidence is not ML confidence. It is an explainable engineering score based on provenance and checks:

- Exact approved InternalPN: high.
- Exact approved MPN with footprint: high.
- Cap between known rail/GND with approved part: high.
- Resistor with explicit current/power and approved part: high.
- Ambiguous context: lower.
- Supplier-only non-approved part: lower.
- Missing footprint verification: block/error.

## Plugin architecture

Design extension registration early, even if not fully implemented.

Example plugin concepts:

```python
class ResolverPlugin(Protocol):
    name: str
    categories: set[str]
    def resolve(self, ctx: ComponentContext, env: ResolveEnvironment) -> Decision | None: ...

class CliPlugin(Protocol):
    def register(self, app: typer.Typer) -> None: ...
```

Potential packages:

- `footfindr-rf`
- `footfindr-pwrlib`
- `footfindr-partdb`
- `footfindr-inventree`

## Technology choices

Recommended:

- CLI: Typer
- Terminal UI: Rich
- Schemas: Pydantic v2
- DB: SQLite + SQLModel or SQLAlchemy
- Units: Pint
- Graph: NetworkX
- Reports: Jinja2 + Rich
- PDFs: PyMuPDF for local extraction, optional AI provider for profile drafting
- Tests: pytest + golden fixtures + Hypothesis for parsing/unit handling

## Why not a GUI first

KiCad GUI/plugin integration can be brittle and version-sensitive. CLI gives:

- Reproducibility.
- Easy testing.
- CI compatibility.
- Scriptable build pipeline.
- Independent evolution of the engine.

A future KiCad GUI should simply call the same Python API.
