"""EnemyBalancer

Each time your respawn dialog appears (i.e. each time you die), every
currently alive enemy's health and shield is scaled down by a configurable
percentage - so a fight that was killing you repeatedly gets a little easier
each time, rather than staying exactly as hard.

Works unchanged across BL1, BL2 and TPS. WillowMind.Pawn, Pawn.HealthPool
(inherited from Engine.Pawn) and WillowPawn.ShieldArmor are declared
identically in all three games' UnrealScript - confirmed directly against
their decompiled sources rather than assumed, since BL1 (Willow1) and BL2/TPS
(Willow2) are otherwise substantially different codebases. This particular
mechanism happens to be shared engine-level plumbing.

Also installed to BorderlandsGOTYEnhanced (BL1E in mods_base's Game enum - the
64-bit "borderlandsgoty.exe" build, a separate value from BL1's 32-bit
"borderlands.exe"). Its gameplay UnrealScript is understood to be the same
WillowGame content as BL1 - Enhanced changed the native engine/renderer, not
the scripted classes this mod touches - but that has not been independently
confirmed against a decompiled BL1E dump the way BL1/BL2/TPS were.
"""

import unrealsdk
from mods_base import SliderOption, build_mod, get_pc, hook
from ui_utils import show_hud_message
from unrealsdk import logging
from unrealsdk.hooks import Type

nerf_percent = SliderOption(
    "Health/Shield Multiplier (%)",
    88,
    1,
    99,
    1,
    True,
    description=(
        "Each time you die, every currently alive enemy's health and shield"
        " is multiplied by this percentage. Applies again, compounding, on"
        " every later death."
    ),
)


def nerf_value(value, multiplier):
    """The reduced stat value, or None if `value` is not numeric.

    The -1 after scaling guarantees the value strictly decreases even where
    the multiplier alone would round back up to the original integer, and the
    result is never allowed below 1.
    """
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return max(1, int(numeric * multiplier) - 1)


def nerf_resource_pool(pool_ref, multiplier):
    """Scale one ResourcePoolReference's current and max value down in place.

    Health and shield are the identical struct, so one function handles both
    rather than repeating the same logic per stat.

    Returns (old_current, old_max, new_current, new_max), or None if there was
    nothing to nerf. Both current and max are scaled by the same multiplier,
    so the current/max RATIO barely moves - an enemy whose health bar was full
    stays visually full, just over a smaller pool. The bar cannot show this;
    only the numbers logged here can.
    """
    data = getattr(pool_ref, "Data", None) if pool_ref is not None else None
    if data is None:
        return None

    old_current = data.GetCurrentValue()
    old_max = data.GetMaxValue()
    new_current = nerf_value(old_current, multiplier)
    new_max = nerf_value(old_max, multiplier)

    touched_max = bool(old_max) and new_max is not None
    touched_current = bool(old_current) and new_current is not None
    if not (touched_max or touched_current):
        return None

    if touched_max:
        data.MaxValue = new_max
    if touched_current:
        data.SetCurrentValue(new_current)

    return old_current, old_max, new_current, new_max


def iter_enemy_pawns(player_pawn):
    """Every living, hostile pawn currently under AI control.

    unrealsdk.find_all("WillowMind") returns EVERY AI-controlled pawn, not
    just enemies - companion pets/turrets, neutral critters and lingering
    corpses all have a WillowMind too. Those were previously counted as
    "found" but never nerfed (no health pool, or already at 0), which made
    the "nerfed X of Y" log read as if live enemies were being skipped.
    Pawn.IsDead() and Pawn.IsEnemy() are native Engine.Pawn functions -
    shared engine-level plumbing, not Gearbox gameplay script - so this
    filter is as safe as the HealthPool/ShieldArmor access below it.
    """
    for mind in unrealsdk.find_all("WillowMind"):
        pawn = mind.Pawn
        if pawn is None:
            continue
        try:
            if pawn.IsDead() or not pawn.IsEnemy(player_pawn):
                continue
        except Exception:  # noqa: BLE001
            pass
        yield pawn


def enemy_display_name(enemy) -> str:
    """The enemy's actual name ("Skag", "Bandit"), not its shared UnrealScript
    class name ("WillowAIPawn") - every enemy in a level is typically the same
    class, distinguished only by its BalanceDefinitionState.

    This is the same call the game itself uses for the name shown above an
    enemy's health bar (WillowAIPawn.GetTransformedName, TargetName) - present
    identically in BL1 and BL2/TPS. Falls back to the class name if a pawn
    somehow has no balance definition (e.g. player-summoned allies).
    """
    state = getattr(enemy, "BalanceDefinitionState", None)
    if state is not None:
        definition = getattr(state, "BalanceDefinition", None)
        if definition is not None:
            try:
                name = definition.GetDisplayNameAtGrade(state.GradeIndex)
                if name:
                    return str(name)
            except Exception:  # noqa: BLE001
                pass
    return str(getattr(getattr(enemy, "Class", None), "Name", "?"))


def enemy_level(enemy):
    """The enemy's level, or None if it can't be read.

    WillowPawn.GetGameStage() - identical across BL1, BL2 and TPS, and the
    same quantity an item's own level is drawn from - is the closest thing to
    a displayed enemy level; there is no separate "GameStageToPlayerLevel"
    mapping in any of the three games' scripts.
    """
    try:
        stage = int(enemy.GetGameStage())
    except Exception:  # noqa: BLE001
        return None
    return stage if stage > 0 else None


@hook("WillowGame.WillowHUD:ShowRespawnDialog", Type.POST)
def on_show_respawn_dialog(_obj, _args, _ret, _func):
    # Nothing to do without a live player - this can fire during odd
    # transitional states with no pawn possessed yet.
    pc = get_pc()
    if pc is None or pc.Pawn is None:
        return

    multiplier = nerf_percent.value / 100.0
    seen = 0
    nerfed = 0
    for enemy in iter_enemy_pawns(pc.Pawn):
        seen += 1
        name = enemy_display_name(enemy)
        try:
            health = nerf_resource_pool(getattr(enemy, "HealthPool", None), multiplier)
            shield = nerf_resource_pool(getattr(enemy, "ShieldArmor", None), multiplier)
            if health is None and shield is None:
                continue
            nerfed += 1

            # One line per enemy: "health current/max -> current/max", the
            # same for shield if present. Compact enough to read at a glance
            # across a whole pack, rather than several lines per enemy.
            parts = []
            if health is not None:
                oc, om, nc, nm = health
                parts.append(f"health {oc:.0f}/{om:.0f} -> {nc:.0f}/{nm:.0f}")
            if shield is not None:
                oc, om, nc, nm = shield
                parts.append(f"shield {oc:.0f}/{om:.0f} -> {nc:.0f}/{nm:.0f}")
            level = enemy_level(enemy)
            label = name if level is None else f"{name} (lvl {level})"
            logging.info(f"[EnemyBalancer] {label}: {', '.join(parts)}")
        except Exception as ex:  # noqa: BLE001
            logging.dev_warning(f"[EnemyBalancer] could not nerf {name}: {ex!r}")

    logging.info(
        f"[EnemyBalancer] nerfed {nerfed} of {seen} enemies found to {nerf_percent.value}%"
    )

    # "of seen" is only shown when it actually diverges from nerfed (a
    # found enemy whose health/shield pools were both empty right then, or
    # threw while being read) - otherwise it's just noise, since seen no
    # longer counts allies/critters/corpses after the iter_enemy_pawns filter.
    if seen == 0:
        return
    count_text = str(nerfed) if nerfed == seen else f"{nerfed} of {seen}"
    show_hud_message(
        "EnemyBalancer", f"Nerfed {count_text} enemies to {nerf_percent.value}%"
    )


mod = build_mod()

# mods_base only restores a PREVIOUS enabled/disabled choice (auto_enable,
# true by default) - it never starts a mod enabled the very first time, before
# any choice has been made. default_load_mod_settings only calls .enable()
# when a settings file already exists with "enabled": true; on a fresh
# install there is no file yet, so is_enabled stays at its dataclass default
# of False until the player opens the mod menu once. A missing settings file
# is a reliable "never touched" signal precisely because mods_base only ever
# writes one from an explicit enable/disable (or an option change) - nothing
# saves it just for having loaded. So this fires exactly once, ever, and never
# overrides an explicit disable made on any later launch.
if mod.settings_file is not None and not mod.settings_file.exists():
    mod.enable()
