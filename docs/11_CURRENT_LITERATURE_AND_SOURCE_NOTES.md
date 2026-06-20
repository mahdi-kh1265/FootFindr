# 11 — Current Literature / Ecosystem Notes

This is not a citation-perfect academic literature review. It is a practical source map for Claude/developers.

## KiCad automation facts

KiCad has a command-line interface with schematic BOM export and netlist export commands. Use this where helpful, but keep direct schematic field editing independent when possible.

- KiCad CLI docs: https://docs.kicad.org/9.0/en/cli/cli.html
- KiCad 10 CLI docs: https://docs.kicad.org/10.0/en/cli/cli.html
- KiCad S-expression format introduction: https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html
- KiCad schematic file format: https://dev-docs.kicad.org/en/file-formats/sexpr-schematic/index.html

## KiCad HTTP/database libraries

KiCad HTTP libraries let KiCad source part records from an external database/ERP-like service while referencing normal KiCad symbols and footprints. This is relevant for future integration, not MVP.

- HTTP libraries docs: https://dev-docs.kicad.org/en/apis-and-binding/http-libraries/index.html
- Part-DB KiCad integration: https://www.kicad.org/external-tools/partdb/
- Part-DB EDA integration docs: https://docs.part-db.de/usage/eda_integration.html
- InvenTree KiCad HTTP library plugin: https://github.com/afkiwers/inventree_kicad

## Supplier APIs

Use local cache first. APIs change; verify before implementing.

DigiKey:

- Developer portal: https://developer.digikey.com/
- Documentation/OAuth: https://developer.digikey.com/documentation
- Product Information APIs: https://developer.digikey.com/products/product-information-v4/productsearch

Mouser:

- API docs: https://api.mouser.com/api/docs/ui/index
- Cart API hub: https://www.mouser.com/api-cart/
- Search API hub: https://www.mouser.com/api-search/

Nexar/Octopart:

- Nexar API: https://nexar.com/api
- Octopart API transition: https://octopart.com/business/api/v4/api-transition
- Altium Octopart API docs: https://www.altium.com/documentation/altium-developer-center/octopart/api

## JLCPCB BOM/CPL

FootFindr should support export profiles. JLCPCB-compatible BOM/CPL is a high-value target.

- JLCPCB KiCad BOM/CPL guide: https://jlcpcb.com/help/article/how-to-generate-the-bom-and-centroid-file-from-kicad

## Vendor model libraries / RF inspiration

Serious RF tools organize parts with MPNs, packages, models, S-parameters, footprints, and documentation. FootFindr should mirror that architecture locally, especially for the future RF extension.

- Keysight vendor component libraries: https://www.keysight.com/us/en/lib/resources/miscellaneous/vendor-component-libraries-1490117.html
- Coilcraft models: https://www.coilcraft.com/en-us/models/
- Coilcraft Genesys/ADS model/library docs: search Coilcraft ADS/Genesys model instructions.
- Murata libraries for Keysight ADS: https://www.murata.com/en-us/tool/data/librarydata/library-keysight2
- Modelithics RF models: https://www.modelithics.com/model

## AI/EDA research ideas to build on

Useful design principles from recent work:

- Use semantic intermediate representations instead of raw schematic files for AI.
- Ground part choices in component libraries/databases.
- Use datasheet-derived structured profiles/knowledge graphs.
- Use deterministic validation/checking loops.
- Treat AI output as draft/reviewable, not authoritative.

Papers to look up:

- pcbGPT: LLM-assisted PCB schematic generation with Python DSL, component search, datasheet grounding, validation, and KiCad synchronization.
- SchGen: semantic code representation for schematic generation.
- CircuitLM: component database/pinout retrieval and validation-oriented generation.
- PCBSchemaGen: datasheet-derived knowledge graphs, constraint-guided schematic synthesis, verifier loop.
- GNN-based schematic optimization/missing component recommendation.

FootFindr is intentionally narrower/easier than these: the human already drew the schematic; FootFindr resolves implementation parts and footprints.
