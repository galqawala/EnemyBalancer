"""Check EnemyBalancer against the REAL game. Changes nothing.

Run it from the SDK console (tilde) while any enemies are currently alive
somewhere in the loaded level - your distance from them does not matter,
`unrealsdk.find_all` is a global scan, not a proximity search:

    pyexec verify_enemybalancer.py

Every call below only reads state or does pure arithmetic. It never calls
SetGameStage on a real enemy, so no enemy's level is actually touched.

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
    check("target_game_stage(30, -3, None)", enemybalancer.target_game_stage(30, -3, None), 27)
    check("target_game_stage(5, -3, None) floors at 1", enemybalancer.target_game_stage(5, -3, None), 1)
    check("target_game_stage(1, -30, None) floors at 1", enemybalancer.target_game_stage(1, -30, None), 1)
    check("target_game_stage(20, 5, None)", enemybalancer.target_game_stage(20, 5, None), 25)
    check("target_game_stage(0, 0, None) floors at 1", enemybalancer.target_game_stage(0, 0, None), 1)
    check("target_game_stage(20, 50, 40) caps at 40", enemybalancer.target_game_stage(20, 50, 40), 40)
    check("target_game_stage(5, -30, 40) floor beats cap", enemybalancer.target_game_stage(5, -30, 40), 1)

    section("current settings")
    show("Enemy Level Offset", enemybalancer.level_offset.value)
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

    section("your own level")
    # player_level() reads PlayerReplicationInfo.ExpLevel off the
    # controller, NOT pc.Pawn.GetGameStage() - the latter breaks while
    # driving a vehicle (pc.Pawn becomes the vehicle actor), confirmed live
    # (2026-08-22) to silently scale every enemy to level 1 as a result.
    plevel = None
    level_cap = None
    if pc is not None:
        attempt("player_level(pc)", lambda: enemybalancer.player_level(pc))
        attempt("max_expected_level(pc)", lambda: enemybalancer.max_expected_level(pc))
        plevel = enemybalancer.player_level(pc)
        level_cap = enemybalancer.max_expected_level(pc)
        if plevel is not None:
            target = enemybalancer.target_game_stage(plevel, enemybalancer.level_offset.value, level_cap)
            show("would scale enemies to", target)
    else:
        show("", "!! no live player controller - cannot read your level")
    if pc is not None and pc.Pawn is not None:
        show("pc.Pawn.Class.Name (sanity check)", getattr(getattr(pc.Pawn, "Class", None), "Name", "?"))

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

    shown = 0
    for pawn in pawns:
        if shown >= 5:
            break
        shown += 1
        name = enemybalancer.enemy_display_name(pawn)
        class_name = getattr(getattr(pawn, "Class", None), "Name", "?")
        game_stage = enemybalancer.pawn_game_stage(pawn)
        exp_level = enemybalancer.pawn_exp_level(pawn)
        section(f"sample enemy {shown}: {name} lvl {exp_level} (class {class_name})")
        if game_stage != exp_level:
            show("!! game stage vs exp level disagree", f"stage={game_stage}, exp={exp_level}")

        if plevel is not None:
            target = enemybalancer.target_game_stage(plevel, enemybalancer.level_offset.value, level_cap)
            already_there = game_stage == target and exp_level == target
            show("would be scaled to", f"{target} (already there)" if already_there else target)

        health_pool = getattr(pawn, "HealthPool", None)
        health_data = getattr(health_pool, "Data", None) if health_pool else None
        if health_data is None:
            show("HealthPool", "none")
        else:
            attempt("  HealthPool current/max", lambda d=health_data: (
                d.GetCurrentValue(), d.GetMaxValue()
            ))

        shield_ref = getattr(pawn, "ShieldArmor", None)
        shield_data = getattr(shield_ref, "Data", None) if shield_ref else None
        if shield_data is None:
            show("ShieldArmor", "none")
        else:
            attempt("  ShieldArmor current/max", lambda d=shield_data: (
                d.GetCurrentValue(), d.GetMaxValue()
            ))

    section("mutating calls - existence only, never invoked here")
    for label, present in (
        ("on_show_respawn_dialog", hasattr(enemybalancer, "on_show_respawn_dialog")),
        ("on_create_ai_pawn", hasattr(enemybalancer, "on_create_ai_pawn")),
        ("on_create_vehicle", hasattr(enemybalancer, "on_create_vehicle")),
    ):
        show(label, "present" if present else "!! MISSING")

    print("")
    if FAILURES:
        print(f"{len(FAILURES)} LOGIC CHECK(S) FAILED: {', '.join(FAILURES)}")
    else:
        print("All logic checks passed.")
    print("Done. Nothing was changed, and no enemy was actually scaled.")
    print("=" * 72)


main()
