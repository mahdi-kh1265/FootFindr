# Tests

Initial tests should use golden KiCad schematic fixtures.

Required test cases:

1. Exact MPN resolves exact footprint.
2. Locked component is not changed.
3. Existing footprint not overwritten without force.
4. 10uF cap on +5V/GND resolves approved 0805 16V part.
5. Cap on V_EOM/GND is blocked.
6. Resistor with CurrentRMS computes power and chooses rated resistor.
7. BOM groups by InternalPN.
8. Inventory shortage calculation.
9. JLCPCB BOM profile emits expected columns.
10. Decision log can undo changes.
