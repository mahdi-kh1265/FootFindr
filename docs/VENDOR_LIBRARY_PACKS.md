# Vendor Library Packs

FootFindr uses **vendor library packs** as the primary way to import
part data from component manufacturers (e.g. Murata, TDK, KEMET).

## Why vendor packs?

1. **Offline-first**: No internet required after the initial CSV download.
2. **Versioned**: Packs can be tracked in Git for reproducibility.
3. **Auditable**: Every part has provenance metadata (source file, row, timestamp, SHA256 hash).
4. **Portable**: Share packs across teams or projects via Git repos.
5. **No scraping**: We do not scrape vendor websites on every invocation.
6. **Integrity-verified**: Source CSV and normalized outputs have SHA256 checksums.

## Mental model

```
Vendor website / exported CSV / official library zip
        ↓
ff lib pack build      (normalize into FootFindr format)
        ↓
versioned FootFindr vendor pack (directory with SHA256 hashes)
        ↓
ff lib install         (register in local workspace)
        ↓
raw vendor library available locally
        ↓
ff lib search          (find parts offline)
        ↓
ff lib promote         (move selected parts into POSM approved)
        ↓
ff resolve --apply     (only uses approved parts)
```

## Raw vendor pack vs POSM approved library

| Property | Raw vendor pack | POSM approved |
|----------|----------------|---------------|
| Status | `raw` | `approved` |
| Used by resolver | **No** | **Yes** |
| Footprint | Candidate only | Verified |
| Created by | `ff lib pack build` | `ff lib promote` |
| Modifiable | No (read-only) | Yes (bind, approve, deprecate) |

**Important**: Raw vendor pack parts are NEVER auto-used by `ff resolve --apply`.
They must be explicitly promoted into an approved library first.

## Private Git Vendor-Pack Workflow

This is the recommended workflow for using real vendor data:

### 1. Download the source CSV

Visit the vendor's website and export/download their product catalog:

- **Murata GRM**: https://www.murata.com/en-global/products/capacitor/mlcc/lineup
  - Filter for GRM series, export as CSV

### 2. Build the pack

```cmd
REM Build private Murata GRM vendor pack from real downloaded CSV
ff lib pack build murata-grm "C:\Users\mahdi\Downloads\murata-grm.csv" ^
  --out ".\vendor-packs\footfindr-lib-murata-grm" ^
  --source-type manual_csv ^
  --real-source
```

Bash:
```bash
ff lib pack build murata-grm ./downloads/murata_grm.csv \
  --out ./vendor-packs/footfindr-lib-murata-grm \
  --source-type manual_csv \
  --real-source
```

### 3. Validate the pack

```bash
ff lib pack validate ./vendor-packs/footfindr-lib-murata-grm
```

### 4. Commit to a private Git repo

```bash
cd ./vendor-packs/footfindr-lib-murata-grm
git init
git add .
git commit -m "Add Murata GRM FootFindr vendor pack (9223 parts)"
```

> **⚠️ Licensing warning**: Do not publish vendor data publicly unless
> redistribution rights are confirmed. Use **private repos** for
> internal/POSM workflows until licensing is checked with the vendor.

### 5. Install the pack

```bash
ff lib install ./vendor-packs/footfindr-lib-murata-grm
```

Or from a cloned repo:

```bash
git clone git@github.com:your-org/footfindr-lib-murata-grm.git
ff lib install ./footfindr-lib-murata-grm
```

### 6. Search installed parts

```bash
ff lib search cap 10u --raw --vendor Murata --package 0805 --voltage-min 16V
ff lib search cap 100n --raw --vendor Murata --package 0603 --voltage-min 16V
ff lib search cap 1u --raw --vendor Murata --package 0603 --voltage-min 10V
ff lib search cap 100p --raw --vendor Murata --dielectric C0G --voltage-min 50V
```

### 7. Promote selected parts

```bash
ff lib promote GRM21BR71C106KE15L --to POSM --as CAP-10U-16V-X7R-0805
ff part show CAP-10U-16V-X7R-0805
```

### 8. Export

```bash
# Export approved parts
ff lib export POSM --out ./exports/posm-approved-parts.yaml

# Export summary of installed raw vendor library
ff lib export Murata-GRM --out ./exports/murata-grm-summary.yaml --summary
```

## Pack directory format

```
footfindr-lib-murata-grm/
  footfindr_pack.yaml        # Pack manifest (with SHA256 hashes)
  README.md                  # Auto-generated
  LICENSE_NOTES.md           # Licensing caution
  source/
    murata_grm.csv           # Original source CSV
  normalized/
    parts.yaml               # Normalized FootFindr library data
    parts.jsonl              # Same data in JSONL format
  manifests/
    normalization_report.yaml # Import statistics
```

### footfindr_pack.yaml

```yaml
pack_name: footfindr-lib-murata-grm
display_name: Murata GRM MLCC Library
vendor: Murata
series: GRM
category: capacitor
kind: raw_vendor
version: 1.0.0
generated_at: 2024-01-01T00:00:00+00:00
footfindr_min_version: 0.1.0
source:
  source_type: manual_csv
  real_source: true
  is_complete_catalog: true
  source_file: murata-grm.csv
  source_sha256: <64-char hex digest>
  source_url: null
  downloaded_at: 2024-01-01T00:00:00+00:00
  notes: null
counts:
  raw_rows: 9223
  imported_parts: 9223
  skipped_rows: 0
parser:
  name: MurataGRMParser
  slug: murata-grm
  version: 1.0.0
hashes:
  source_csv: <SHA256 of source CSV>
  normalized_yaml: <SHA256 of normalized/parts.yaml>
  normalized_jsonl: <SHA256 of normalized/parts.jsonl>
license:
  redistribution_status: unknown
  notes: Verify vendor licensing before redistribution.
```

### Source types

| `source_type` | `real_source` | `is_complete_catalog` | Meaning |
|---------------|--------------|----------------------|---------|
| `fixture` | `false` | `false` | Test/sample data only |
| `manual_csv` | `true` | `true` | Real vendor catalog download |
| `api` | `true` | varies | Fetched from vendor API |

### Integrity verification

SHA256 hashes are computed for:

1. **Source CSV** (`source.source_sha256` and `hashes.source_csv`)
2. **Normalized parts.yaml** (`hashes.normalized_yaml`)
3. **Normalized parts.jsonl** (`hashes.normalized_jsonl`)

These enable:
- **Reproducibility**: Verify the same source CSV produces the same pack.
- **Auditing**: Confirm normalized data hasn't been tampered with.
- **Versioning**: Detect when source catalogs have been updated.

## Adding a new vendor parser

The pack system is generic. Adding a new vendor requires only one file:

```python
# footfindr/libraries/vendor_parsers/tdk_mlcc.py
from footfindr.libraries.vendor_parsers.base import VendorParser, VendorParseResult
from footfindr.libraries.vendor_parsers import register_parser

class TDKMLCCParser:
    vendor = "TDK"
    series = "C"
    category = "capacitor"
    display_name = "TDK MLCC Library"
    pack_slug = "footfindr-lib-tdk-mlcc"

    def parse(self, source_path, *, limit=None, source_file=None, source_pack=None):
        # Parse TDK CSV format...
        return VendorParseResult(records=records, raw_rows=..., ...)

register_parser("tdk-mlcc", TDKMLCCParser)
```

Then add one import in `vendor_parsers/__init__.py`:

```python
import footfindr.libraries.vendor_parsers.tdk_mlcc  # noqa: F401
```

No changes to `packs.py` required.

Currently available parsers:
- `murata-grm` — Murata GRM MLCC (37 MPN size codes, Vdc/µ/± normalization)
- `generic` / `generic-csv` — Universal CSV fallback (50+ column aliases)

## Licensing / provenance caution

**Do NOT assume vendor catalog data can be redistributed publicly.**

All generated packs default to `redistribution_status: unknown`.

For private/internal use within your organization, using vendor product
catalog data is generally acceptable for engineering reference.

Before publishing a pack publicly (GitHub, npm, etc.):
1. Check the vendor's terms of use for their product catalog data.
2. Contact the vendor if terms are unclear.
3. Update `footfindr_pack.yaml` with the correct `redistribution_status`.

## Available CLI commands

```bash
# Pack building
ff lib pack build <vendor-type> <csv> --out <dir> [--source-type X] [--real-source]
ff lib pack validate <dir>
ff lib pack info <dir>
ff lib pack list

# Pack management
ff lib install <dir>
ff lib uninstall <name>
ff lib update <name> <new-dir>
ff lib info <name>

# Searching installed packs
ff lib search <category> <query> --raw [--vendor X] [--package X] [--voltage-min X]

# Promoting to approved
ff lib promote <MPN> --to <library> --as <internal-pn>

# Exporting
ff lib export <name> --out <file.yaml> [--summary]
```
