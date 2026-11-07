"""
Advanced Military Helicopter System with Combat, 3 Stability/Health Tiers & Dynamic Aerodynamics.

Features:
- Spawns parked in world via: /spawn helicopter 1 or /spawn heli
- Interactive boarding: Walk up to it and press 'E' (or type /heli) to enter as pilot.
- Exit anytime: Press 'E' or /exit to safely disembark.
- Full aerodynamic flight physics (pitch/roll/yaw vectors, collective lift, momentum, drag).
- 3 Health & Stability Tiers:
    * Tier 3 (201-300 HP): "Óptimo / Estable" - 100% power, rock-solid hover, zero turbulence.
    * Tier 2 (101-200 HP): "Inestable / Daño Motor" - 25% power loss, wind shudder/vibration, drift.
    * Tier 1 (1-100 HP): "Crítico / Rotor de Cola Comprometido" - Continuous yaw spin, gravity drag, violent bucking.
    * Tier 0 (0 HP): "Destrucción / Explosión" - Massive explosion, pilot casualty, debris.
- Vulnerable pilot & Combat Hitboxes:
    * Ground players can aim at the cockpit and eliminate the pilot directly with sniper/rifle!
    * Ground gunfire and grenade explosions inflict real-time damage to the helicopter fuselage.
- Rotors OFF when parked/unmanned; turn ON and spin rapidísimo upon boarding with 3D spatial audio.
"""

import math
import time
from twisted.internet.task import LoopingCall
from piqueserver.commands import command, player_only

class ActiveHelicopter:
    """Manages the 3D position, orientation, aerodynamic physics, health tiers and client sync."""
    def __init__(self, protocol):
        self.protocol = protocol
        
        # Position & Velocity
        self.x = 115.0
        self.y = 230.0
        self.z = 32.0
        
        self.vx = 0.0
        self.vy = 0.0
        self.vz = 0.0
        
        self.yaw = 0.0
        self.pitch = 0.0
        self.roll = 0.0
        
        # Combat & Health
        self.max_hp = 300.0
        self.hp = 300.0
        self.tier = 3 # 3: Óptimo, 2: Inestable, 1: Crítico, 0: Destruido
        
        self.pilot = None
        self.is_flying = False
        self.active = True
        self.time_elapsed = 0.0
        self.sync_counter = 0
        
        # 20 Hz physics update tick (50ms)
        self.tick_dt = 0.05
        self.loop = LoopingCall(self.update_physics)
        self.loop.start(self.tick_dt, now=False)
        print(f"[HELICOPTER] Military Combat Helicopter active at ({self.x:.1f}, {self.y:.1f}, {self.z:.1f})")

    def spawn_at(self, x, y, z, yaw=0.0):
        """Spawns or repairs the helicopter parked at a specified position."""
        if self.pilot and not self.pilot.disconnected:
            self.disembark(self.pilot)
            
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)
        self.vx = 0.0
        self.vy = 0.0
        self.vz = 0.0
        self.yaw = float(yaw)
        self.pitch = 0.0
        self.roll = 0.0
        self.hp = 300.0
        self.tier = 3
        self.is_flying = False
        self.active = True
        self.broadcast_sync()

    def update_tier(self):
        old_tier = self.tier
        if self.hp > 200.0:
            self.tier = 3
        elif self.hp > 100.0:
            self.tier = 2
        elif self.hp > 0.0:
            self.tier = 1
        else:
            self.tier = 0
            
        if self.tier != old_tier:
            if self.tier == 2:
                self.protocol.broadcast_chat("[HELICOPTER] ¡ADVERTENCIA! Nivel 2: Fuselaje dañado (Estabilidad Inestable / Turbina afectada).")
                if self.pilot and not self.pilot.disconnected:
                    self.pilot.send_chat("[HELICOPTER] ¡Turbina dañada! Pérdida de empuje y vibración en mandos.")
            elif self.tier == 1:
                self.protocol.broadcast_chat("[HELICOPTER] ¡PELIGRO CRÍTICO! Nivel 1: Rotor de cola comprometido (Pérdida de sustentación).")
                if self.pilot and not self.pilot.disconnected:
                    self.pilot.send_chat("[HELICOPTER] ¡ALARMA ROJA! Rotor de cola dañado. El helicóptero gira sin control.")
            elif self.tier == 0:
                self.destroy()

    def take_damage(self, amount, attacker=None):
        if self.hp <= 0.0 or not self.active:
            return
            
        self.hp = max(0.0, self.hp - amount)
        self.update_tier()
        
        if attacker and not attacker.disconnected:
            tier_str = "Óptimo" if self.tier == 3 else ("Inestable" if self.tier == 2 else ("Crítico" if self.tier == 1 else "Destruido"))
            attacker.send_chat(f"[HELICOPTER] ¡Impacto certero! Salud: {int(self.hp)}/{int(self.max_hp)} HP [Nivel {self.tier}: {tier_str}]")
            
        if self.pilot and not self.pilot.disconnected and attacker != self.pilot:
            self.pilot.send_chat(f"[HELICOPTER] ¡Fuselaje impactado por {attacker.name if attacker else 'un enemigo'}! (-{int(amount)} HP) | Salud: {int(self.hp)}/300")
            
        self.broadcast_sync()

    def destroy(self):
        """Triggers cataclysmic explosion and kills pilot upon 0 HP."""
        self.hp = 0.0
        self.tier = 0
        self.protocol.broadcast_chat("[HELICOPTER] ¡BOOM! ¡El helicóptero militar ha EXPLOTADO en mil pedazos!")
        
        # Eliminate pilot in explosion
        if self.pilot and not self.pilot.disconnected:
            p = self.pilot
            self.disembark(p)
            try:
                p.kill(killer=None, kill_type=3, grenade=None)
            except Exception:
                pass
                
        self.broadcast_sync()
        self.active = False

    def update_physics(self):
        self.time_elapsed += self.tick_dt
        self.sync_counter += 1
        
        if not self.active or self.hp <= 0.0:
            return
        
        if self.pilot and not self.pilot.disconnected:
            self.is_flying = True
            inp = getattr(self.pilot, 'heli_inputs', {})
            ori = self.pilot.world_object.orientation
            
            # --- 1. YAW HEADING CONTROL ---
            target_yaw = math.atan2(ori.x, -ori.y)
            yaw_diff = (target_yaw - self.yaw + math.pi) % (2 * math.pi) - math.pi
            self.yaw += yaw_diff * 0.16
            
            # --- TIER 1 STABILITY EFFECT: TAIL ROTOR FAILURE / TORQUE SPIN ---
            if self.tier == 1:
                # Fuselage spins uncontrollably due to lost anti-torque
                torque_spin = 2.4 # rad/sec
                self.yaw += torque_spin * self.tick_dt
            
            dir_x = math.sin(self.yaw)
            dir_y = -math.cos(self.yaw)
            right_x = dir_y
            right_y = -dir_x
            
            # --- 2. CYCLIC THRUST & BANKING ---
            fx = 0.0
            fy = 0.0
            self.pitch = 0.0
            self.roll = 0.0
            
            # Engine power modifier based on damage tier
            power_factor = 1.0 if self.tier == 3 else (0.75 if self.tier == 2 else 0.50)
            
            if inp.get('forward'):
                fx += dir_x * (15.0 * power_factor)
                fy += dir_y * (15.0 * power_factor)
                self.pitch = -0.24 # Nose down
            if inp.get('back'):
                fx -= dir_x * (11.0 * power_factor)
                fy -= dir_y * (11.0 * power_factor)
                self.pitch = 0.20  # Nose up
            if inp.get('left'):
                fx -= right_x * (11.0 * power_factor)
                fy -= right_y * (11.0 * power_factor)
                self.roll = -0.20  # Bank left
            if inp.get('right'):
                fx += right_x * (11.0 * power_factor)
                fy += right_y * (11.0 * power_factor)
                self.roll = 0.20   # Bank right
                
            # --- 3. COLLECTIVE LIFT (VERTICAL THRUST) ---
            fz = 0.0
            if inp.get('up'):
                # Ascend power
                fz -= 14.0 * power_factor
            elif inp.get('down'):
                # Descend
                fz += 10.0
            else:
                # Hover hold
                fz = -self.vz * 2.2 + 0.15 * math.sin(self.time_elapsed * 3.0)
                
            # --- TIER 2 & TIER 1 STABILITY EFFECTS (TURBULENCE, SHUDDER, LOSS OF LIFT) ---
            if self.tier == 2:
                # Tier 2: Aerodynamic turbulence & vibration
                wobble_x = math.sin(self.time_elapsed * 7.5) * 1.6
                wobble_y = math.cos(self.time_elapsed * 6.8) * 1.6
                wobble_z = math.sin(self.time_elapsed * 5.2) * 1.1
                fx += wobble_x
                fy += wobble_y
                fz += wobble_z
                self.roll += math.sin(self.time_elapsed * 8.0) * 0.07
                self.pitch += math.cos(self.time_elapsed * 7.0) * 0.05
            elif self.tier == 1:
                # Tier 1: Catastrophic bucking & heavy downward gravity drag
                wobble_x = math.sin(self.time_elapsed * 12.0) * 4.2
                wobble_y = math.cos(self.time_elapsed * 11.0) * 4.2
                fx += wobble_x
                fy += wobble_y
                fz += 6.5 # Downward gravity drag - pilot must fight to stay in the air!
                self.pitch += math.sin(self.time_elapsed * 14.0) * 0.16
                self.roll += math.cos(self.time_elapsed * 13.0) * 0.18
                
            # Integrate velocities
            self.vx = (self.vx + fx * self.tick_dt) * 0.94
            self.vy = (self.vy + fy * self.tick_dt) * 0.94
            self.vz = (self.vz + fz * self.tick_dt) * 0.90
            
            self.x += self.vx * self.tick_dt
            self.y += self.vy * self.tick_dt
            self.z += self.vz * self.tick_dt

            # Ground collision: landing skids rest on ground surface
            if hasattr(self.protocol, 'map'):
                m = self.protocol.map
                px_i = int(max(0, min(511, self.x)))
                py_i = int(max(0, min(511, self.y)))
                ground_z = 63.0
                for cz in range(0, 63):
                    if m.get_solid(px_i, py_i, cz):
                        ground_z = float(cz)
                        break
                if self.z >= ground_z - 0.2:
                    self.z = ground_z - 0.2
                    if self.vz > 0.0:
                        self.vz = 0.0
                    self.pitch *= 0.8
                    self.roll *= 0.8
            
            # Map boundaries & flight limits
            self.x = max(10.0, min(502.0, self.x))
            self.y = max(10.0, min(502.0, self.y))
            self.z = max(4.0, min(58.0, self.z))
            
            # Keep pilot inside the cockpit seat and airborne
            try:
                self.pilot.fly = True
                self.pilot.world_object.set_position(self.x, self.y, self.z)
                self.pilot.set_location((self.x, self.y, self.z))
            except Exception:
                pass
                
        else:
            # When unoccupied: stay parked or slowly settle
            self.is_flying = False
            self.vx *= 0.8
            self.vy *= 0.8
            self.vz *= 0.8
            
        # Broadcast sync every 2 ticks (10 Hz)
        if self.sync_counter % 2 == 0:
            self.broadcast_sync()

    def broadcast_sync(self):
        """Sends [HELI_SYNC] metadata to all connected clients."""
        pilot_id = self.pilot.player_id if (self.pilot and not self.pilot.disconnected) else -1
        is_fly = 1 if (self.is_flying or self.pilot) else 0
        sync_msg = f"[HELI_SYNC] 1 {self.x:.2f} {self.y:.2f} {self.z:.2f} {self.yaw:.2f} {self.pitch:.2f} {self.roll:.2f} {pilot_id} {is_fly} {int(self.hp)} {int(self.max_hp)} {self.tier}"
        
        # Broadcast via silent system message
        for player in self.protocol.players.values():
            if not player.disconnected:
                try:
                    player.send_chat(sync_msg)
                except Exception:
                    pass

    def embark(self, player):
        """Places player into the pilot cockpit."""
        if self.hp <= 0.0 or not self.active:
            player.send_chat("[HELICOPTER] El vehículo está destruido. Despliega uno nuevo con /spawn helicopter 1.")
            return False
            
        if self.pilot and not self.pilot.disconnected and self.pilot != player:
            player.send_chat("[HELICOPTER] La cabina ya está ocupada por otro piloto.")
            return False
            
        self.pilot = player
        self.is_flying = True
        player.heli_inputs = {'forward': False, 'back': False, 'left': False, 'right': False, 'up': False, 'down': False}
        player.is_piloting = True
        player.fly = True
        # PILOT IS VULNERABLE TO GROUND SNIPERS/FIRE (god mode disabled)
        player.god = False
        
        try:
            player.world_object.set_position(self.x, self.y, self.z)
            player.set_location((self.x, self.y, self.z))
        except Exception:
            pass
            
        player.send_chat("[HELICOPTER] ¡Has tomado los controles del helicóptero militar!")
        player.send_chat(f"[HELICOPTER] Estado del fuselaje: {int(self.hp)}/{int(self.max_hp)} HP [Nivel {self.tier}]")
        player.send_chat("[HELICOPTER] [W/S] Adelante/Atrás | [A/D] Viraje | [Espacio] Ascender | [C] Descender | [E] Salir")
        self.protocol.broadcast_chat(f"[HELICOPTER] {player.name} ha tomado los mandos del helicóptero militar.")
        self.broadcast_sync()
        return True

    def disembark(self, player):
        """Safely lands or disembarks the pilot."""
        if self.pilot == player:
            self.pilot = None
            self.is_flying = False
            player.is_piloting = False
            player.god = False
            player.fly = False
            player.heli_inputs = {}
            
            try:
                px, py, pz = self.x, self.y, min(self.z + 1.8, 58.0)
                player.world_object.set_position(px, py, pz)
                player.set_location((px, py, pz))
            except Exception:
                pass
                
            player.send_chat("[HELICOPTER] Has desembarcado del helicóptero.")
            self.protocol.broadcast_chat(f"[HELICOPTER] {player.name} ha desembarcado del helicóptero.")
            self.broadcast_sync()
            return True
        return False

# Global instance reference
GLOBAL_HELI = None

@command('spawn')
def spawn_command(connection, *args):
    """
    Spawn vehicles or entities in the world.
    Usage: /spawn helicopter 1 or /spawn heli
    """
    global GLOBAL_HELI
    if not args:
        return "Uso: /spawn helicopter 1"
        
    target = args[0].lower()
    if target in ('helicopter', 'heli', 'chopper'):
        px, py, pz = connection.world_object.position.get()
        
        # Ground detection: spawn resting on surface
        ground_z = pz
        if hasattr(connection.protocol, 'map'):
            m = connection.protocol.map
            for cz in range(max(1, int(pz) - 2), min(62, int(pz) + 6)):
                if m.get_solid(int(px), int(py), cz):
                    ground_z = cz - 0.5
                    break

        if not GLOBAL_HELI:
            GLOBAL_HELI = ActiveHelicopter(connection.protocol)
            
        yaw = connection.world_object.orientation.get()[0] if hasattr(connection.world_object, 'orientation') else 0.0
        GLOBAL_HELI.spawn_at(px, py, ground_z, yaw=yaw)
        
        connection.send_chat(f"[HELICOPTER] Helicóptero militar desplegado en ({px:.1f}, {py:.1f}, {ground_z:.1f}) con 300 HP.")
        connection.send_chat("[HELICOPTER] ¡Acércate y presiona 'E' para pilotarlo!")
        connection.protocol.broadcast_chat(f"[HELICOPTER] {connection.name} desplegó un helicóptero militar. ¡Presiona 'E' para pilotarlo!")
        return
    return f"Entidad '{args[0]}' desconocida. Opciones: helicopter"

@command('heli', 'pilot', '7heli', 'h')
@player_only
def heli_command(connection):
    """
    Board or disembark from the military helicopter.
    Usage: /heli or /pilot
    """
    global GLOBAL_HELI
    if not GLOBAL_HELI:
        GLOBAL_HELI = ActiveHelicopter(connection.protocol)
        
    if getattr(connection, 'is_piloting', False):
        GLOBAL_HELI.disembark(connection)
        return
    else:
        # Distance check if parked
        if not GLOBAL_HELI.pilot:
            px, py, pz = connection.world_object.position.get()
            dist = math.sqrt((px - GLOBAL_HELI.x)**2 + (py - GLOBAL_HELI.y)**2 + (pz - GLOBAL_HELI.z)**2)
            if dist > 18.0:
                connection.send_chat(f"[HELICOPTER] Estás demasiado lejos del helicóptero ({dist:.1f} bloques). Acércate para subirte.")
                return
        GLOBAL_HELI.embark(connection)

@command('exit', 'leave')
@player_only
def exit_command(connection):
    """Exit the current vehicle / helicopter."""
    global GLOBAL_HELI
    if GLOBAL_HELI and getattr(connection, 'is_piloting', False):
        GLOBAL_HELI.disembark(connection)
    else:
        connection.send_chat("No estás dentro de ningún vehículo.")

@command('damageheli', 'dmgheli')
def damageheli_command(connection, amount="50"):
    """Inflict damage on the military helicopter."""
    global GLOBAL_HELI
    if GLOBAL_HELI and GLOBAL_HELI.active:
        try:
            dmg = float(amount)
        except ValueError:
            dmg = 50.0
        GLOBAL_HELI.take_damage(dmg, attacker=connection)
        return f"Helicóptero dañado por {dmg:.0f} HP."
    return "No hay helicóptero activo."

@command('sethelihp')
def sethelihp_command(connection, amount="300"):
    """Set the helicopter HP directly to test stability tiers."""
    global GLOBAL_HELI
    if GLOBAL_HELI and GLOBAL_HELI.active:
        try:
            val = float(amount)
        except ValueError:
            val = 300.0
        GLOBAL_HELI.hp = max(0.0, min(GLOBAL_HELI.max_hp, val))
        GLOBAL_HELI.update_tier()
        GLOBAL_HELI.broadcast_sync()
        return f"Salud del helicóptero fijada a {GLOBAL_HELI.hp:.0f} HP [Nivel {GLOBAL_HELI.tier}]."
    return "No hay helicóptero activo."

def apply_script(protocol, connection, config):
    class HelicopterProtocol(protocol):
        def on_map_change(self, map):
            super().on_map_change(map)
            global GLOBAL_HELI
            GLOBAL_HELI = ActiveHelicopter(self)

    class HelicopterConnection(connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.is_piloting = False
            self.heli_inputs = {}

        def on_walk_update(self, up, down, left, right):
            if getattr(self, 'is_piloting', False):
                self.heli_inputs['forward'] = up
                self.heli_inputs['back'] = down
                self.heli_inputs['left'] = left
                self.heli_inputs['right'] = right
            super().on_walk_update(up, down, left, right)

        def on_animation_update(self, jump, crouch, sneak, sprint):
            if getattr(self, 'is_piloting', False):
                self.heli_inputs['up'] = jump or sprint
                self.heli_inputs['down'] = crouch or sneak
            super().on_animation_update(jump, crouch, sneak, sprint)

        def on_fall(self, damage):
            if getattr(self, 'is_piloting', False):
                return False
            return super().on_fall(damage)

        def on_hit(self, hit_amount, hit_player, kill_type, grenade):
            """Triggered when this player hits someone."""
            global GLOBAL_HELI
            # If target player is currently piloting the helicopter
            if getattr(hit_player, 'is_piloting', False) and GLOBAL_HELI and GLOBAL_HELI.active:
                # Gunfire also damages the helicopter structure
                heli_dmg = hit_amount * 0.75
                GLOBAL_HELI.take_damage(heli_dmg, attacker=self)
            return super().on_hit(hit_amount, hit_player, kill_type, grenade)

        def on_kill(self, killer, kill_type, grenade):
            """Triggered when this player is killed."""
            global GLOBAL_HELI
            if getattr(self, 'is_piloting', False) and GLOBAL_HELI:
                GLOBAL_HELI.disembark(self)
                if killer and not killer.disconnected:
                    self.protocol.broadcast_chat(f"[HELICOPTER] ¡{killer.name} derribó al piloto {self.name} de un certero disparo desde tierra!")
            return super().on_kill(killer, kill_type, grenade)

        def on_shoot_set(self, fire):
            """Detects gunfire aimed from the ground towards the helicopter."""
            global GLOBAL_HELI
            if fire and GLOBAL_HELI and GLOBAL_HELI.active and GLOBAL_HELI.hp > 0.0:
                if not getattr(self, 'is_piloting', False):
                    try:
                        sx, sy, sz = self.world_object.position.get()
                        ori = self.world_object.orientation
                        ox, oy, oz = ori.x, ori.y, ori.z
                        
                        # Ray-to-helicopter distance
                        vx = GLOBAL_HELI.x - sx
                        vy = GLOBAL_HELI.y - sy
                        vz = (GLOBAL_HELI.z - 0.5) - sz
                        
                        t = vx * ox + vy * oy + vz * oz
                        if 0.5 < t < 120.0:
                            cx = sx + t * ox
                            cy = sy + t * oy
                            cz = sz + t * oz
                            dist_sq = (GLOBAL_HELI.x - cx)**2 + (GLOBAL_HELI.y - cy)**2 + (GLOBAL_HELI.z - cz)**2
                            
                            # Helicopter bounding volume radius (7.5 blocks for 3X larger military gunship)
                            if dist_sq <= 7.5**2:
                                # Determine weapon damage
                                weap = getattr(self, 'weapon', 0)
                                dmg = 45.0 if weap == 0 else (22.0 if weap == 1 else 35.0)
                                GLOBAL_HELI.take_damage(dmg, attacker=self)
                                
                                # Cockpit vulnerability: 2.8 blocks radius damages the pilot directly from ground!
                                if GLOBAL_HELI.pilot and not GLOBAL_HELI.pilot.disconnected:
                                    if dist_sq <= 2.8**2:
                                        GLOBAL_HELI.pilot.hit(dmg * 0.7, self, 0)
                    except Exception:
                        pass
            return super().on_shoot_set(fire)

        def on_chat(self, value, global_message):
            global GLOBAL_HELI
            msg = value.strip().lower()
            
            # Spawn commands
            if msg.startswith(('/spawn helicopter', '/spawn heli', 'spawn helicopter', 'spawn heli', '7spawn helicopter', '7spawn heli')):
                px, py, pz = self.world_object.position.get()
                ground_z = pz
                if hasattr(self.protocol, 'map'):
                    m = self.protocol.map
                    for cz in range(max(1, int(pz) - 2), min(62, int(pz) + 6)):
                        if m.get_solid(int(px), int(py), cz):
                            ground_z = cz - 0.5
                            break
                            
                if not GLOBAL_HELI:
                    GLOBAL_HELI = ActiveHelicopter(self.protocol)
                GLOBAL_HELI.spawn_at(px, py, ground_z)
                self.send_chat(f"[HELICOPTER] Helicóptero militar desplegado en tu posición con 300 HP.")
                self.send_chat("[HELICOPTER] ¡Acércate y presiona 'E' para entrar y pilotarlo!")
                self.protocol.broadcast_chat(f"[HELICOPTER] {self.name} desplegó un helicóptero militar. ¡Presiona 'E' para pilotarlo!")
                return False

            if msg.startswith(('damageheli', '/damageheli', 'dmgheli', '/dmgheli')):
                parts = msg.split()
                dmg = 50.0
                if len(parts) > 1:
                    try:
                        dmg = float(parts[1])
                    except ValueError:
                        pass
                if GLOBAL_HELI and GLOBAL_HELI.active:
                    GLOBAL_HELI.take_damage(dmg, attacker=self)
                return False

            if msg.startswith(('sethelihp', '/sethelihp')):
                parts = msg.split()
                val = 300.0
                if len(parts) > 1:
                    try:
                        val = float(parts[1])
                    except ValueError:
                        pass
                if GLOBAL_HELI and GLOBAL_HELI.active:
                    GLOBAL_HELI.hp = max(0.0, min(GLOBAL_HELI.max_hp, val))
                    GLOBAL_HELI.update_tier()
                    GLOBAL_HELI.broadcast_sync()
                    self.send_chat(f"[HELICOPTER] Salud ajustada a {int(GLOBAL_HELI.hp)}/300 [Nivel {GLOBAL_HELI.tier}]")
                return False
                
            # Enter / Exit triggers
            if msg in ('/heli', 'heli', '7heli', '/pilot', 'pilot', '!heli', '/exit', 'exit'):
                if not GLOBAL_HELI:
                    GLOBAL_HELI = ActiveHelicopter(self.protocol)
                if getattr(self, 'is_piloting', False):
                    GLOBAL_HELI.disembark(self)
                else:
                    px, py, pz = self.world_object.position.get()
                    dist = math.sqrt((px - GLOBAL_HELI.x)**2 + (py - GLOBAL_HELI.y)**2 + (pz - GLOBAL_HELI.z)**2)
                    if dist <= 18.0:
                        GLOBAL_HELI.embark(self)
                    else:
                        self.send_chat(f"[HELICOPTER] El helicóptero está a {dist:.1f} bloques. Acércate para subirte.")
                return False
                
            return super().on_chat(value, global_message)

        def on_disconnect(self):
            global GLOBAL_HELI
            if GLOBAL_HELI and getattr(self, 'is_piloting', False):
                GLOBAL_HELI.disembark(self)
            super().on_disconnect()

    return HelicopterProtocol, HelicopterConnection
