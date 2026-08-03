# EnemyBalancer

Works on Borderlands 1, Borderlands 2, and Borderlands: The Pre-Sequel. Each time your
respawn dialog appears — that is, each time you die — every currently alive enemy's
health and shield is scaled down by a configurable percentage. A fight that's been
killing you repeatedly gets a little easier each time, rather than staying exactly as
hard.

## Installation

1. Place the `EnemyBalancer` folder (or the `.sdkmod`) in your `sdk_mods` directory
2. Configure it in the mod menu

The mod is **enabled by default** the very first time it's installed — mods_base itself
only defaults to disabled until you've opened the mod menu once, so this mod explicitly
enables itself on its first-ever launch (detected by there being no settings file for it
yet). If you disable it, that choice is remembered normally on every later launch.

## Configuration

- **Health/Shield Multiplier (%)** — Default: 95, range 1–99

Applied again, compounding, on every later death. At 95%, dying three times in a row
against the same pack leaves them at roughly 95% × 95% × 95% ≈ 86% of their original
health and shield.

## How It Works

`WillowMind` is the AI controller class for every living enemy across all three games;
its `Pawn` gives the actual creature. Health and shield are read and reduced through
`Pawn.HealthPool` and `WillowPawn.ShieldArmor`, the same `ResourcePoolReference` /
`ResourcePool` structure — `GetCurrentValue()`, `GetMaxValue()`, and a directly settable
`MaxValue` — in all three games. Confirmed directly against each game's decompiled
UnrealScript rather than assumed from one to the others, since BL1 (Willow1) and
BL2/TPS (Willow2) are otherwise substantially different codebases; this particular
mechanism happens to be shared engine-level plumbing that never diverged.

## What it can and can't reach

The mod nerfs whatever `unrealsdk.find_all("WillowMind")` returns at the moment you die —
a global scan of every currently existing enemy object, not a proximity search. Your
distance from them makes no difference, including after you've been moved to a respawn
point far from the fight.

The real limit is existence, not distance: an enemy a population trigger hasn't spawned
yet (an on-approach spawn point you haven't reached, for instance) doesn't exist as an
object yet and can't be touched. Dying again after it appears will catch it fine.

## Verifying it

`verify_enemybalancer.py` checks the mod against the running game rather than a mock
of it. Put it in your `sdk_mods` folder, then from the SDK console (tilde), while any
enemies are currently alive somewhere in the level:

```
pyexec verify_enemybalancer.py
```

It reports every currently alive enemy's real health and shield, and what the nerf
math would turn them into — without changing anything. It never calls the actual
nerf function or the respawn hook, so no enemy is touched; the functions that do change
state are checked for existence only.

## Confirming it actually worked

The mod logs one line per enemy it touches:

```
[EnemyBalancer] Skag (lvl 12): health 480/480 -> 455/455, shield 200/200 -> 189/189
```

Note that the health bar itself will still look exactly as full afterwards — it's a bar
showing current/max as a *ratio*, and this mod scales both numbers by the same amount, so
the ratio barely moves. That's expected, not a sign anything failed; the log line is the
only place the actual numbers are visible.
