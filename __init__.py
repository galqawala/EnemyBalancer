from mods_base import build_mod, get_pc, hook, SliderOption, BoolOption
from unrealsdk.hooks import Type
from unrealsdk import logging
import unrealsdk


nerf_multiplier_pct = SliderOption("Health/Shield Multiplier (%)", 99, 1, 99, 1)


def _safe_str(value) -> str:
    try:
        return str(value)
    except Exception:
        return "<unprintable>"


def _safe_float(value):
    try:
        return float(value)
    except Exception:
        return None


def _nerf_value(value, multiplier):
    numeric = _safe_float(value)
    if numeric is None:
        return None
    new_value = int(numeric * multiplier) - 1
    if new_value < 1:
        new_value = 1
    return new_value


@hook("WillowGame.WillowHUD:ShowRespawnDialog", Type.POST)
def on_show_respawn_dialog(obj, __args, __ret, __func):
    logging.info("You died! Nerfing enemies...")

    pc = get_pc()
    if not pc or not pc.Pawn:
        return
    
    all_minds = unrealsdk.find_all("WillowMind")
    enemies = [mind.Pawn for mind in all_minds if mind.Pawn]
    
    logging.info(f"Found {len(enemies)} enemies via WillowMind")
    for enemy in enemies:
        # Log basic object name
        logging.info(f"Nerfing: {enemy}")
        
        # Try to get more descriptive name/type from the pawn or its AI controller
        try:
            def get_attr(obj, attr):
                return _safe_str(getattr(obj, attr, "N/A"))

            logging.info(f"  Archetype: {get_attr(enemy, "ObjectArchetype")}")
            
            if hasattr(enemy, "ConsumerHandle") and enemy.ConsumerHandle:
                 definition = get_attr(enemy.ConsumerHandle, "Definition")
                 logging.info(f"  Definition: {definition}")

            multiplier = nerf_multiplier_pct.value / 100.0

            # Health: prefer HealthPool resource pool so we can adjust max too
            health_pool = getattr(enemy, "HealthPool", None)
            health_data = getattr(health_pool, "Data", None) if health_pool else None
            if health_data:
                old_health = health_data.GetCurrentValue()
                old_max_health = health_data.GetMaxValue()

                new_max_health = _nerf_value(old_max_health, multiplier)
                new_health = _nerf_value(old_health, multiplier)

                if (old_max_health or 0) > 0 and new_max_health is not None:
                    try:
                        health_data.MaxValue = new_max_health
                    except Exception as e:
                        logging.warning(f"  Health MaxValue set failed: {e}")

                if (old_health or 0) > 0 and new_health is not None:
                    try:
                        health_data.SetCurrentValue(new_health)
                    except Exception as e:
                        logging.warning(f"  Health SetCurrentValue failed: {e}")

                updated_health = health_data.GetCurrentValue()
                updated_max_health = health_data.GetMaxValue()
                logging.info(f"  Health: {old_health} -> {updated_health} / {old_max_health} -> {updated_max_health}")

            # Shield: use ShieldArmor ResourcePoolReference if present
            shield_ref = getattr(enemy, "ShieldArmor", None)
            shield_data = getattr(shield_ref, "Data", None) if shield_ref else None
            if shield_data:
                old_shield = shield_data.GetCurrentValue()
                old_max_shield = shield_data.GetMaxValue()

                new_max_shield = _nerf_value(old_max_shield, multiplier)
                new_shield = _nerf_value(old_shield, multiplier)

                if (old_max_shield or 0) > 0 and new_max_shield is not None:
                    try:
                        shield_data.MaxValue = new_max_shield
                    except Exception as e:
                        logging.warning(f"  Shield MaxValue set failed: {e}")

                if (old_shield or 0) > 0 and new_shield is not None:
                    try:
                        shield_data.SetCurrentValue(new_shield)
                    except Exception as e:
                        logging.warning(f"  Shield SetCurrentValue failed: {e}")

                updated_shield = shield_data.GetCurrentValue()
                updated_max_shield = shield_data.GetMaxValue()
                logging.info(f"  Shield: {old_shield} -> {updated_shield} / {old_max_shield} -> {updated_max_shield}")

        except Exception as e:
            logging.error(f"  Error nerfing enemy: {e}")
    logging.info(f"{len(enemies)} enemies nerfed.")


build_mod()