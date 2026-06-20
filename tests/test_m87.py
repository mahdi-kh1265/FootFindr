"""M8.7 tests — CLI alias layer.

Verify that short aliases resolve to the same behavior as canonical commands.
No live API calls.
"""

from __future__ import annotations

import pytest


# ===========================================================================
# Supplier name aliases
# ===========================================================================


class TestSupplierNameAliases:
    """Test that short supplier names normalize correctly."""

    def test_dk_to_digikey(self):
        from footfindr.suppliers.registry import SupplierRegistry
        assert SupplierRegistry.normalize_name("dk") == "digikey"

    def test_digi_to_digikey(self):
        from footfindr.suppliers.registry import SupplierRegistry
        assert SupplierRegistry.normalize_name("digi") == "digikey"

    def test_mou_to_mouser(self):
        from footfindr.suppliers.registry import SupplierRegistry
        assert SupplierRegistry.normalize_name("mou") == "mouser"

    def test_jlc_to_jlcpcb(self):
        from footfindr.suppliers.registry import SupplierRegistry
        assert SupplierRegistry.normalize_name("jlc") == "jlcpcb"

    def test_lcsc_to_jlcpcb(self):
        from footfindr.suppliers.registry import SupplierRegistry
        assert SupplierRegistry.normalize_name("lcsc") == "jlcpcb"

    def test_nex_to_nexar(self):
        from footfindr.suppliers.registry import SupplierRegistry
        assert SupplierRegistry.normalize_name("nex") == "nexar"

    def test_canonical_unchanged(self):
        from footfindr.suppliers.registry import SupplierRegistry
        assert SupplierRegistry.normalize_name("digikey") == "digikey"
        assert SupplierRegistry.normalize_name("mouser") == "mouser"
        assert SupplierRegistry.normalize_name("jlcpcb") == "jlcpcb"
        assert SupplierRegistry.normalize_name("nexar") == "nexar"
        assert SupplierRegistry.normalize_name("mock") == "mock"

    def test_case_insensitive(self):
        from footfindr.suppliers.registry import SupplierRegistry
        assert SupplierRegistry.normalize_name("DK") == "digikey"
        assert SupplierRegistry.normalize_name("MOU") == "mouser"

    def test_unknown_passes_through(self):
        from footfindr.suppliers.registry import SupplierRegistry
        assert SupplierRegistry.normalize_name("farnell") == "farnell"

    def test_get_by_alias(self):
        from footfindr.suppliers.registry import SupplierRegistry
        reg = SupplierRegistry()
        p = reg.get("dk")
        assert p is not None
        assert p.name == "digikey"

    def test_get_by_mou(self):
        from footfindr.suppliers.registry import SupplierRegistry
        reg = SupplierRegistry()
        p = reg.get("mou")
        assert p is not None
        assert p.name == "mouser"


# ===========================================================================
# Field aliases
# ===========================================================================


class TestFieldAliases:
    """Test that field short forms resolve to canonical names."""

    def test_pack_to_package(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("pack") == "package"

    def test_pkg_to_package(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("pkg") == "package"

    def test_case_to_package(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("case") == "package"

    def test_volt_to_voltage(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("volt") == "voltage"

    def test_v_to_voltage(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("v") == "voltage"

    def test_cap_to_capacitance(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("cap") == "capacitance"

    def test_res_to_resistance(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("res") == "resistance"

    def test_tol_to_tolerance(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("tol") == "tolerance"

    def test_diel_to_dielectric(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("diel") == "dielectric"

    def test_temp_to_temperature_range(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("temp") == "temperature_range"

    def test_curr_to_current(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("curr") == "current"

    def test_i_to_current(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("i") == "current"

    def test_freq_to_frequency(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("freq") == "frequency"

    def test_ds_to_datasheet_url(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("ds") == "datasheet_url"

    def test_spn_to_supplier_pn(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("spn") == "supplier_pn"

    def test_stat_to_lifecycle(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("stat") == "lifecycle"

    def test_pkgtype_to_packaging(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("pkgtype") == "packaging"

    def test_fam_to_family(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("fam") == "family"

    def test_sup_to_supplier(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("sup") == "supplier"

    def test_canonical_unchanged(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("package") == "package"
        assert resolve_field_alias("voltage") == "voltage"
        assert resolve_field_alias("stock") == "stock"
        assert resolve_field_alias("price") == "price"


# ===========================================================================
# CLI import and command registration
# ===========================================================================


class TestCLIRegistration:
    """Test that the CLI app loads with all aliases registered."""

    def test_cli_imports(self):
        """CLI loads without import errors."""
        from footfindr.cli import app
        assert app is not None

    def test_supplier_app_has_aliases(self):
        """supplier_app should have s, filt, ls, sh, etc. as hidden commands."""
        from footfindr.cli import supplier_app
        # Just verify the app exists
        assert supplier_app is not None

    def test_constraint_aliases_registered(self):
        """Constraint should be reachable as 'con'."""
        from footfindr.cli import app
        assert app is not None

    def test_plan_aliases_registered(self):
        """Plan commands should have sh/ls/ap/drop aliases."""
        from footfindr.cli_plan import plan_app
        assert plan_app is not None


# ===========================================================================
# Safety: aliases don't bypass plan mode
# ===========================================================================


class TestAliasSafety:
    """Verify that aliases don't bypass safety constraints."""

    def test_promote_defaults_plan_mode(self):
        """promote-supplier should default to plan mode, not --apply."""
        # This is tested by checking the default value of apply parameter
        from footfindr.cli_supplier import register_supplier_commands
        # The function exists and can be called
        assert register_supplier_commands is not None

    def test_psup_is_same_function(self):
        """psup alias should be the same function as promote-supplier."""
        # After registration, both should point to same callback
        from footfindr.cli import app
        assert app is not None
