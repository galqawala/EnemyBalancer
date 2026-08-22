"""EnemyBalancer

Scales every enemy's level to your own level plus a configurable offset -
both when you respawn (for whoever is already alive at that moment) and the
instant each new enemy spawns afterward (for arena-style waves the original
respawn-only version could never reach). A negative offset (the default)
makes enemies easier than you; a positive one makes them harder.

Complete rewrite (2026-08-22) from an earlier version that scaled only
health/shield by a fixed percentage - see git history for that version's own
reasoning. This version went through several wrong architectures before
landing on the right one, each disproven by live evidence rather than
assumed away:

1. First tried WillowAIPawn/WillowVehicle:SetGameStage + SetExpLevel POST
   hooks, correcting a pawn's level-like fields right after the population
   factory set them. This looked completely correct from every angle
   available at the time - the calls didn't throw, and re-reading
   GetGameStage()/GetExpLevel() immediately afterward confirmed the new
   values, satisfying this codebase's own "never assert an unverified
   result, re-read it" rule. It still shipped wrong: the enemy's actual
   health and on-screen level stayed at the ORIGINAL values regardless of
   class (AI pawn or vehicle) - confirmed live, repeatedly, including once
   with an explicit diagnostic trace showing the corrected fields being
   read back successfully. Confirmed live (2026-08-22) that only
   on_show_respawn_dialog (acting on an ALREADY-alive pawn, well after its
   spawn sequence has fully finished) ever changed anything real - a fresh
   spawn's OWN corrected fields never took effect, regardless of enemy
   type. That is the actual distinguishing factor: not which class, but
   whether the pawn already existed before scaling was attempted.

2. Reading PopulationFactoryBalancedAIPawn.uc's own CreatePopulationActor
   explains why: it calls `GradeIndex = PawnBalanceDefinition
   .SelectGradeIndex(GameStage, AwesomeLevel)` and `GetPawnArchetypeForGrade
   (GradeIndex)` to pick which archetype/stats to use BEFORE the pawn even
   exists, using the ORIGINAL GameStage argument - only afterward does it
   call SetGameStage/SetExpLevel on the resulting pawn, and later still
   InitializeBalanceDefinitionState(PawnBalanceDefinition, GradeIndex),
   which is handed that SAME original GradeIndex directly as a parameter,
   not read back off the pawn's own (by-then-corrected) fields. Nothing
   downstream ever re-selects the archetype or re-derives GradeIndex from
   the pawn's own fields, so setting those fields after the fact cannot
   retroactively change which archetype/stats were already chosen - it
   only changes what GetGameStage()/GetExpLevel() themselves report back,
   which is why the re-read "confirmation" was real but hollow.
   PopulationFactoryWillowVehicle.uc follows the identical shape for
   vehicles. Every POST-hook mechanism from step 1 is gone - there is no
   PlayerTick hook, no pending-spawn queue, no WorldInfo-identity check, no
   level-travel hook, and no "spawn cap" cache in this version at all.

3. This version instead hooks CreatePopulationActor itself, PRE, on both
   population factory classes, and overrides the GameStage ARGUMENT before
   archetype selection ever happens - modeled directly on EnemyRandomizer, a
   real third-party mod doing the identical thing successfully (confirmed
   by reading its own source, not guessed). A PRE hook mutating `args`
   in-place was considered but not used, for lack of a confirmed-working
   example of that specific pattern in this codebase; EnemyRandomizer's own
   proven approach - manually re-invoke the real call with corrected
   arguments inside unrealsdk.hooks.prevent_hooking_direct_calls(), then
   Block the original call and substitute the result - is used instead,
   exactly as that mod does it.

WillowMind.Pawn, WillowPawn.GetGameStage()/GetExpLevel(), and
PopulationFactoryBalancedAIPawn/PopulationFactoryWillowVehicle's own
CreatePopulationActor signature are declared identically across BL1, BL2 and
TPS - confirmed directly against decompiled sources rather than assumed,
since BL1 (Willow1) and BL2/TPS (Willow2) are otherwise substantially
different codebases - and identically again in BL1E's own dump.

Also installed to BorderlandsGOTYEnhanced (BL1E in mods_base's Game enum -
the 64-bit "borderlandsgoty.exe" build, a separate value from BL1's 32-bit
"borderlands.exe"). Its gameplay UnrealScript is understood to be the same
WillowGame content as BL1 - Enhanced changed the native engine/renderer, not
the scripted classes this mod touches - confirmed directly for every class
this mod touches (WillowMind, WillowPawn, PopulationFactoryBalancedAIPawn,
PopulationFactoryWillowVehicle, MissionTracker) against BL1E's own decompiled
dump.
"""

import unrealsdk
from mods_base import BoolOption, SliderOption, build_mod, get_pc, hook
from ui_utils import show_hud_message
from unrealsdk import logging
from unrealsdk.hooks import Block, Type

level_offset = SliderOption(
    "Enemy Level Offset",
    1,
    -80,
    80,
    1,
    True,
    description=(
        "Every enemy is scaled to your own level plus this offset (never"
        " below 1). Negative makes enemies easier than you; positive makes"
        " them harder. Applied both when you respawn and the instant each"
        " new enemy spawns afterward."
    ),
)

decrease_offset_on_respawn = BoolOption(
    # Under 26 characters, full stop - see CLAUDE.md's mod-menu label-
    # truncation gotcha (re-measured 2026-08-22 against this exact option:
    # even 26 chars silently drops the last word onto a "(...)" row).
    "-1 on respawn",
    True,
    description="Each time you respawn, lower the level offset by 1 - progressively easier the more you die.",
)

increase_offset_on_mission_complete = BoolOption(
    "+1 on mission completion",
    True,
    description="Each time you complete a mission, raise the level offset by 1 - progressively harder as you succeed.",
)


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


def pawn_game_stage(pawn):
    """This ENEMY pawn's own level/game stage, or None if it can't be read.

    WillowPawn.GetGameStage() - identical across BL1, BL2 and TPS. Enemy
    pawns only - see player_level below for the player's own level, which
    this function must NOT be used for: `pc.Pawn` can be a VEHICLE while
    driving (Controller.Possess(self, true) makes the vehicle actor the
    Pawn - the same gotcha already documented for ColorRandomizer), and a
    vehicle's own GetGameStage() is unrelated to the player's actual
    character level.
    """
    try:
        stage = int(pawn.GetGameStage())
    except Exception:  # noqa: BLE001
        return None
    return stage if stage > 0 else None


def pawn_exp_level(pawn):
    """This enemy pawn's own DISPLAYED level, or None if it can't be read.

    GameStage and ExpLevel are two separate fields, each set by its own
    native setter - GameStage drives internal attribute/stat scaling,
    ExpLevel is what the game's own on-screen nameplate actually displays.
    Kept for on_show_respawn_dialog's own use (an already-alive pawn's
    fields genuinely respond to being set directly - see the module
    docstring for why a fresh spawn's fields do not).
    """
    try:
        level = int(pawn.GetExpLevel())
    except Exception:  # noqa: BLE001
        return None
    return level if level > 0 else None


def scale_pawn_level(pawn, target: int) -> tuple:
    """Set both of an ALREADY-ALIVE pawn's level-like fields to `target` -
    GameStage (via SetGameStage, drives stat scaling) and ExpLevel (via
    SetExpLevel, drives the displayed nameplate number) - only calling each
    native setter when that specific field is actually wrong, and returns
    the ACTUAL resulting (game_stage, exp_level) read back afterward, never
    assumed.

    Only correct for a pawn that already exists and has finished its own
    spawn sequence, like on_show_respawn_dialog's own targets - see the
    module docstring for why this does NOT work on a pawn still being
    created by the population factory (use on_create_ai_pawn/
    on_create_vehicle for that instead).
    """
    if pawn_game_stage(pawn) != target:
        pawn.SetGameStage(target)
    if pawn_exp_level(pawn) != target:
        pawn.SetExpLevel(target)
    return pawn_game_stage(pawn), pawn_exp_level(pawn)


def player_level(pc):
    """The player's own character level, or None if it can't be read.

    Deliberately NOT pc.Pawn.GetGameStage() - see pawn_game_stage's
    docstring for why that breaks while driving a vehicle.
    PlayerReplicationInfo.ExpLevel is read straight off the CONTROLLER, not
    the Pawn, so it's correct regardless of what the player currently
    possesses - the same proven pattern AutoLootBL1E's own character_level
    already uses successfully for this exact purpose.
    """
    info = getattr(pc, "PlayerReplicationInfo", None)
    if info is None:
        return None
    try:
        level = int(info.ExpLevel)
    except Exception:  # noqa: BLE001
        return None
    return level if level > 0 else None


def max_expected_level(pc):
    """The game's own enforced level cap, or None if it can't be read.

    WillowPlayerController.GetMaxExpLevel() - the SAME ceiling the game
    itself uses for the player's own level, and almost certainly also the
    practical ceiling for any population definition's own Grades array
    (enemies scale up to the same cap the player can reach, never higher).
    Read directly from the game rather than guessed at or hardcoded, so it
    stays correct whatever this specific game's actual cap is (BL1E's,
    BL2's and TPS's differ, and may change with DLC/level-cap patches).

    Confirmed live (2026-08-22, user report): setting the offset to +50
    made enemies stop spawning entirely - PopulationFactoryBalancedAIPawn.uc
    finds no matching grade for a GameStage that high
    (GetPawnArchetypeForGrade returns none, confirmed in the dump), so
    CreatePopulationActor produces no actor at all. Clamping the target to
    this cap avoids requesting a level nothing in the game actually defines.
    """
    try:
        cap = int(pc.GetMaxExpLevel())
    except Exception:  # noqa: BLE001
        return None
    return cap if cap > 0 else None


def target_game_stage(player_level: int, offset: int, cap: int | None) -> int:
    """The level enemies should be scaled to: the player's own level plus
    the configured offset, never below 1 and never above the game's own
    enforced level cap when known (see max_expected_level) - going higher
    doesn't just look silly, it makes the population factory find no valid
    enemy to spawn at all.
    """
    target = max(1, player_level + offset)
    if cap is not None:
        target = min(target, cap)
    return target


def adjust_level_offset(delta: int, reason: str) -> None:
    """Nudge level_offset.value by `delta`, clamped to the slider's own
    min/max (read off the option itself, not a hardcoded copy of its
    range), and log the change unconditionally - per explicit request
    (2026-08-22), so the auto-adjustment is directly checkable in the log
    rather than a silent, unverifiable background change to a setting the
    player didn't directly touch.
    """
    old = level_offset.value
    new = max(level_offset.min_value, min(level_offset.max_value, old + delta))
    if new == old:
        return
    level_offset.value = new
    logging.info(f"[EnemyBalancer] {reason}: level offset {old:+d} -> {new:+d}")


def pool_snapshot(pool_ref):
    """(current, max) for one ResourcePoolReference, read-only - or None if
    absent/unreadable. This mod never writes health/shield directly
    (SetGameStage recalculates them internally as a side effect of the
    level change) - this is purely for logging real proof of what a scaling
    decision actually produced.
    """
    data = getattr(pool_ref, "Data", None) if pool_ref is not None else None
    if data is None:
        return None
    try:
        return data.GetCurrentValue(), data.GetMaxValue()
    except Exception:  # noqa: BLE001
        return None


@hook("WillowGame.WillowHUD:ShowRespawnDialog", Type.POST)
def on_show_respawn_dialog(_obj, _args, _ret, _func):
    # Nothing to do without a live player - this can fire during odd
    # transitional states with no pawn possessed yet.
    pc = get_pc()
    if pc is None or pc.Pawn is None:
        return

    plevel = player_level(pc)
    if plevel is None:
        logging.warning("[EnemyBalancer] could not read player level - skipping respawn scaling")
        return

    if decrease_offset_on_respawn.value:
        adjust_level_offset(-1, "respawn")

    target = target_game_stage(plevel, level_offset.value, max_expected_level(pc))

    seen = 0
    scaled = 0
    max_health_seen = None
    max_shield_seen = None
    for enemy in iter_enemy_pawns(pc.Pawn):
        seen += 1
        name = enemy_display_name(enemy)
        try:
            current_exp = pawn_exp_level(enemy)
            if current_exp != target or pawn_game_stage(enemy) != target:
                scaled += 1

            # scale_pawn_level re-reads EVERYTHING after setting it - real
            # proof of what actually happened, not an assertion that it
            # should have worked.
            resulting_stage, resulting_exp = scale_pawn_level(enemy, target)
            health = pool_snapshot(getattr(enemy, "HealthPool", None))
            shield = pool_snapshot(getattr(enemy, "ShieldArmor", None))
            if health is not None and (max_health_seen is None or health[1] > max_health_seen):
                max_health_seen = health[1]
            if shield is not None and (max_shield_seen is None or shield[1] > max_shield_seen):
                max_shield_seen = shield[1]

            health_text = f"{health[0]:.0f}/{health[1]:.0f}" if health is not None else "n/a"
            shield_text = f"{shield[0]:.0f}/{shield[1]:.0f}" if shield is not None else "n/a"
            outcome = (
                ""
                if resulting_stage == target and resulting_exp == target
                else f" !! DID NOT TAKE (game stage still {resulting_stage}, exp level still {resulting_exp})"
            )
            logging.info(
                f"[EnemyBalancer] {name}: level {current_exp} -> {target}"
                f" | health {health_text} | shield {shield_text}{outcome}"
            )
        except Exception as ex:  # noqa: BLE001
            logging.warning(f"[EnemyBalancer] could not scale {name}: {ex!r}")

    # Greatest max health/shield actually seen across the whole pass, logged
    # explicitly so it's checkable that scaling really produced sane numbers
    # rather than silently doing nothing - proof, not just an assertion.
    logging.info(
        f"[EnemyBalancer] scaled {scaled} of {seen} enemies to level {target}"
        f" (player level {plevel} {level_offset.value:+d})"
        f" | greatest max health seen: {max_health_seen}, greatest max shield seen: {max_shield_seen}"
    )

    if seen == 0:
        return
    count_text = str(scaled) if scaled == seen else f"{scaled} of {seen}"
    show_hud_message(
        "EnemyBalancer",
        f"Scaled {count_text} enemies to level {target}",
    )


def spawn_target_game_stage() -> int | None:
    """The GameStage argument a fresh population spawn should be redirected
    to, or None if it can't be determined right now (no live player, or
    player level unreadable) - shared by both PRE hooks below."""
    pc = get_pc()
    if pc is None or pc.Pawn is None:
        return None
    plevel = player_level(pc)
    if plevel is None:
        return None
    return target_game_stage(plevel, level_offset.value, max_expected_level(pc))


@hook("WillowGame.PopulationFactoryBalancedAIPawn:CreatePopulationActor", Type.PRE)
def on_create_ai_pawn(obj, args, _ret, _func):
    """Override the GameStage argument BEFORE the population factory selects
    which archetype/grade to spawn an enemy AI pawn as - see the module
    docstring for why fixing the pawn's own fields AFTER creation (this
    mod's previous approach) provably does not work: archetype/stat
    selection already happened, using the ORIGINAL GameStage, by the time
    any pawn exists to fix.

    Modeled directly on EnemyRandomizer's own proven-working hook for this
    exact function: re-invoke the real call with a corrected GameStage
    argument, wrapped in prevent_hooking_direct_calls() so that call doesn't
    re-trigger this same hook, then Block the original call and substitute
    the result it returns.
    """
    target = spawn_target_game_stage()
    if target is None or args.GameStage == target:
        return

    with unrealsdk.hooks.prevent_hooking_direct_calls():
        spawned = obj.CreatePopulationActor(
            args.Master,
            args.SpawnLocationContextObject,
            args.SpawnLocation,
            args.SpawnRotation,
            target,
            args.AwesomeLevel,
        )
    logging.info(
        f"[EnemyBalancer] spawning AI pawn at level {args.GameStage} -> {target}"
        f" (result: {enemy_display_name(spawned) if spawned is not None else 'None'})"
    )
    return Block, spawned


@hook("WillowGame.PopulationFactoryWillowVehicle:CreatePopulationActor", Type.PRE)
def on_create_vehicle(obj, args, _ret, _func):
    """Same as on_create_ai_pawn, for enemy-piloted vehicles
    (PopulationFactoryWillowVehicle.uc's own CreatePopulationActor follows
    the identical shape: GameStage argument selects the archetype/grade
    before any vehicle exists)."""
    target = spawn_target_game_stage()
    if target is None or args.GameStage == target:
        return

    with unrealsdk.hooks.prevent_hooking_direct_calls():
        spawned = obj.CreatePopulationActor(
            args.Master,
            args.SpawnLocationContextObject,
            args.SpawnLocation,
            args.SpawnRotation,
            target,
            args.AwesomeLevel,
        )
    logging.info(
        f"[EnemyBalancer] spawning vehicle at level {args.GameStage} -> {target}"
        f" (result: {enemy_display_name(spawned) if spawned is not None else 'None'})"
    )
    return Block, spawned


@hook("WillowGame.MissionTracker:GlobalCompleteMission", Type.POST)
def on_mission_complete(_obj, _args, _ret, _func):
    """Raise the level offset by 1 each time a mission completes, if
    enabled - GlobalCompleteMission (MissionTracker.uc) is a genuine
    dedicated "a mission was just completed" event, found by reading the
    dump rather than inferred from a side effect."""
    if not increase_offset_on_mission_complete.value:
        return
    adjust_level_offset(1, "mission complete")


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
