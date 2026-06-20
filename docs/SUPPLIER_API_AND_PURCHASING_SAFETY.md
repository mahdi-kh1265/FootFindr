# Supplier API and Purchasing Safety

## Overview

FootFindr integrates with multiple electronic component suppliers for part
lookup, stock/price checking, and BOM compatibility verification.

**No automatic purchasing is implemented or allowed in the current release.**

## Supplier Providers

| Provider | Purpose | Status |
|----------|---------|--------|
| DigiKey | Full-service distributor (V4 API with OAuth2) | Stub — not implemented |
| Mouser | Full-service distributor | Stub — not implemented |
| Nexar/Octopart | Multi-supplier aggregator | Stub — not implemented |
| JLCPCB/LCSC | PCB assembly service | Stub — not implemented |
| Mock | Testing and development | Implemented (canned data) |

## Allowed Operations (Current Release)

The following operations are safe and implemented:

- **`ff supplier providers`** — List all registered providers with status
- **`ff supplier lookup <MPN> --mock`** — Look up a part via the mock provider
- **`ff supplier refresh <MPN> --mock`** — Refresh cached data from mock provider
- **`ff supplier cache info`** — View cache statistics
- **`ff supplier cache show <MPN>`** — Show cached supplier data
- **`ff supplier cache clear`** — Clear supplier cache
- **`ff jlc check`** — Read-only JLCPCB compatibility check
- **`ff jlc annotate --dry-run`** — Propose LCSC annotations without writing
- **`ff jlc annotate --apply`** — Write exact-match LCSC codes only

## Forbidden Operations

The following operations are explicitly **NOT** implemented and will raise
`NotImplementedError` if called:

- **Cart creation** — `create_cart()` on any provider
- **Cart item addition** — `add_to_cart()` on any provider
- **Quote generation** — `quote()` on any provider
- **Order submission** — `submit_order()` on any provider

## Purchasing Safety Rules

1. **No automatic purchasing.**
   FootFindr will never automatically submit an order or add items to a
   supplier cart without an explicit user command.

2. **No hidden cart submission.**
   `ff resolve`, `ff bom`, `ff jlc check`, and `ff jlc annotate` will
   never trigger purchasing or cart operations.

3. **No automatic substitutions.**
   Supplier APIs must not influence `ff resolve --apply` unless parts are
   explicitly promoted and approved by the user.

4. **Future purchasing requires explicit commands.**
   When purchasing is eventually implemented, it will require:
   ```bash
   ff order submit --supplier digikey --from-cart <cart_id>
   ```
   with explicit user confirmation before any order is placed.

5. **Supplier data is cached, not trusted.**
   Cached supplier data (stock, price, availability) is informational
   only and does not influence the resolver's part selection.

## Supplier Cache Architecture

The supplier cache at `.footfindr/supplier_cache/cache.sqlite` stores:

- Part lookups from supplier APIs
- Stock/price refresh results
- LCSC part number mappings

Uniqueness is enforced on `(manufacturer, mpn, supplier)` to prevent
collisions from inconsistent MPN formatting.

The cache is independent of the part index and is never wiped by
`ff lib index rebuild`. To clear it:

```bash
ff supplier cache clear                 # Clear all
ff supplier cache clear --supplier mock # Clear specific supplier
```

## Future Implementation Plan

1. **Phase 1 (M8)**: Live DigiKey V4 API with OAuth2
2. **Phase 2 (M9)**: Live Mouser API
3. **Phase 3 (M10)**: Nexar/Octopart aggregation
4. **Phase 4 (M11)**: JLCPCB/LCSC live lookup
5. **Phase 5 (M12+)**: Cart and ordering (with mandatory safety gates)
