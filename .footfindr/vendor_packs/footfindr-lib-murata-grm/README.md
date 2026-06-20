# Murata GRM MLCC Library

This pack contains normalized Murata GRM part data for FootFindr.

## Status

- **Kind**: raw vendor library
- **Not POSM-approved** by default
- Parts require promotion before auto-resolve

## Source

| Field | Value |
|-------|-------|
| Source type | manual_csv |
| Real source | True |
| Complete catalog | True |
| Source file | murata-grm.csv |
| Generated | 2026-06-19T19:48:59.716433+00:00 |
| Imported parts | 9223 |
| Skipped rows | 0 |

## Usage

```bash
# Install this pack
ff lib install .

# Search for parts
ff lib search cap 10u --raw --vendor Murata

# Promote a part to POSM approved
ff lib promote <MPN> --to POSM --as CAP-10U-16V-X7R-0805
```

## License

Redistribution status: **unknown**

Verify vendor licensing before redistribution.
