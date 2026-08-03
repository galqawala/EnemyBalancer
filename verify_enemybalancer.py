"""Check EnemyBalancer against the REAL game. Changes nothing.

Run it from the SDK console (tilde) while any enemies are currently alive
somewhere in the loaded level - your distance from them does not matter,
`unrealsdk.find_all` is a global scan, not a proximity search:

    pyexec verify_enemybalancer.py

The real constraint is not distance but existence: the mod can only touch
enemies that are already alive (already exist as objects) at the moment you die. Anything
a population trigger has not yet created (e.g. an on-approach spawn point you
have not reached) is not nerfable, because it does not exist yet - dying again
after it appears will still catch it.

Every call below only reads state or does pure arithmetic. It never calls
nerf_resource_pool or the respawn-dialog hook on a real enemy, so no enemy's
health or shield is actually touched. The functions that DO change state are
checked for existence only, by name.

This exists because a mock of the engine can only confirm what the mod's
author already believed about it - see AutoLoot's verify_autoloot.py and the
three shipped bugs a mock-based suite let through undetected.
"""

import sys

from mods_base import get_pc

enemybalancer = sys.modules.get("EnemyBalancer")
WAS_ALREADY_LOADED = enemybalancer is not None
if enemybalancer is None:  # not loaded as a mod, e.g. running from an extracted folder
    import EnemyBalancer as enemybalancer  # noqa: F401


FAILURES = []


def show(label, value):
    print(f"  {label:<44} {value}")


def check(label, actual, expected):
    """Assert against a value worked out by hand, not by rerunning the code."""
    if actual == expected:
        show(f"PASS  {label}", actual)
    else:
        FAILURES.append(label)
        show(f"FAIL  {label}", f"{actual!r} != expected {expected!r}")


def section(title):
    print("")
    print(f"--- {title} ".ljust(72, "-"))


def attempt(label, call):
    """Run one read-only probe, reporting a failure instead of aborting."""
    try:
        show(label, call())
    except Exception as ex:  # noqa: BLE001
        show(label, f"!! FAILED: {ex!r}")


def main():
    pc = get_pc()
    print("")
    print("=" * 72)
    print("EnemyBalancer verification - read only, nothing is changed")
    print("=" * 72)

    section("which EnemyBalancer is being inspected")
    show("module file", getattr(enemybalancer, "__file__", "?"))
    show("version", getattr(enemybalancer, "__version__", "?"))
    show(
        "source",
        "the running mod" if WAS_ALREADY_LOADED else "!! freshly imported, NOT the live mod",
    )

    section("logic checks - real function, values worked out by hand")
    # 1000 * 0.95 = 950, minus 1 = 949
    check("nerf_value(1000, 0.95)", enemybalancer.nerf_value(1000, 0.95), 949)
    # 100 * 0.99 = 99, minus 1 = 98
    check("nerf_value(100, 0.99)", enemybalancer.nerf_value(100, 0.99), 98)
    # 1 * 0.01 = 0, minus 1 = -1, clamped to the floor of 1
    check("nerf_value(1, 0.01) floors at 1", enemybalancer.nerf_value(1, 0.01), 1)
    check("nerf_value(None, 0.95) is None", enemybalancer.nerf_value(None, 0.95), None)
    check("nerf_value('nope', 0.95) is None", enemybalancer.nerf_value("nope", 0.95), None)

    section("current settings")
    show("Health/Shield Multiplier (%)", enemybalancer.nerf_percent.value)
    mod = getattr(enemybalancer, "mod", None)
    if mod is not None:
        show("mod.is_enabled", mod.is_enabled)
        settings_file = getattr(mod, "settings_file", None)
        show(
            "settings file exists",
            settings_file is not None and settings_file.exists(),
        )
        show(
            "note",
            "if this is a fresh install and is_enabled is already True with no"
            " settings file yet, the enable-on-first-run logic did its job",
        )

    section("enemies in the level right now")
    minds = []
    try:
        import unrealsdk

        minds = list(unrealsdk.find_all("WillowMind"))
    except Exception as ex:  # noqa: BLE001
        show("unrealsdk.find_all('WillowMind')", f"!! FAILED: {ex!r}")

    show("WillowMind count", len(minds))
    pawns = [m.Pawn for m in minds if m.Pawn is not None]
    show("of those, with a Pawn", len(pawns))
    if minds and not pawns:
        show("", "!! SUSPECT: WillowMind objects exist but none have a Pawn")

    multiplier = enemybalancer.nerf_percent.value / 100.0
    shown = 0
    for pawn in pawns:
        if shown >= 5:
            break
        shown += 1
        name = enemybalancer.enemy_display_name(pawn)
        class_name = getattr(getattr(pawn, "Class", None), "Name", "?")
        level = enemybalancer.enemy_level(pawn)
        section(f"sample enemy {shown}: {name} lvl {level} (class {class_name})")

        health_pool = getattr(pawn, "HealthPool", None)
        health_data = getattr(health_pool, "Data", None) if health_pool else None
        if health_data is None:
            show("HealthPool", "none")
        else:
            attempt("  HealthPool current/max", lambda d=health_data: (
                d.GetCurrentValue(), d.GetMaxValue()
            ))
            attempt(
                "  would become (current/max)",
                lambda d=health_data: (
                    enemybalancer.nerf_value(d.GetCurrentValue(), multiplier),
                    enemybalancer.nerf_value(d.GetMaxValue(), multiplier),
                ),
            )

        shield_ref = getattr(pawn, "ShieldArmor", None)
        shield_data = getattr(shield_ref, "Data", None) if shield_ref else None
        if shield_data is None:
            show("ShieldArmor", "none")
        else:
            attempt("  ShieldArmor current/max", lambda d=shield_data: (
                d.GetCurrentValue(), d.GetMaxValue()
            ))
            attempt(
                "  would become (current/max)",
                lambda d=shield_data: (
                    enemybalancer.nerf_value(d.GetCurrentValue(), multiplier),
                    enemybalancer.nerf_value(d.GetMaxValue(), multiplier),
                ),
            )

    section("mutating calls - existence only, never invoked here")
    for label, present in (
        ("nerf_resource_pool", hasattr(enemybalancer, "nerf_resource_pool")),
        ("on_show_respawn_dialog", hasattr(enemybalancer, "on_show_respawn_dialog")),
    ):
        show(label, "present" if present else "!! MISSING")

    print("")
    if FAILURES:
        print(f"{len(FAILURES)} LOGIC CHECK(S) FAILED: {', '.join(FAILURES)}")
    else:
        print("All logic checks passed.")
    print("Done. Nothing was changed, and no enemy was actually nerfed.")
    print("=" * 72)


main()
