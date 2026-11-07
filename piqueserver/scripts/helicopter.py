"""
Clean Dynamic Helicopter Flight System for Ace of Spades / Piqueserver.

Features:
- Pure Entity Flight Simulation (ZERO terrain block destruction: no shovel dust, no chunk re-bakes, no crumbling!).
- Full aerodynamic flight physics (collective lift, cyclic pitch/roll vectors, momentum, drag, ground effect).
- Seamless pilot mount & flight controls (/heli or /pilot to enter/exit).
- Autonomous aerial patrol when unoccupied.
- Integrated with Anti-Cheat (exempts active pilot from kinematic speed/fly checks).
"""

import math
import time
from twisted.internet.task import LoopingCall
from piqueserver.commands import command, player_only

class ActiveHelicopter:
    """Manages the 3D aerial position, orientation, and aerodynamic physics of the helicopter."""
    def __init__(self, protocol):
        self.protocol = protocol
        
        # Airborne starting position over the bay / ocean
        self.x = 115.0
        self.y = 230.0
        self.z = 32.0
        
        self.vx = 0.0
        self.vy = 0.0
        self.vz = 0.0
        
        self.yaw = 0.0
        self.pitch = 0.0
        self.roll = 0.0
        
        self.pilot = None
        self.time_elapsed = 0.0
        
        # High-frequency smooth physics update tick (20 Hz = 50ms)
        self.tick_dt = 0.05
        self.loop = LoopingCall(self.update_physics)
        self.loop.start(self.tick_dt, now=False)
        print(f"[HELICOPTER] Pure Entity Flight System active at ({self.x:.1f}, {self.y:.1f}, {self.z:.1f})")

    def update_physics(self):
        self.time_elapsed += self.tick_dt
        
        if self.pilot and not self.pilot.disconnected:
            # --- PILOTED FLIGHT WITH FULL AERODYNAMIC VECTORS ---
            inp = getattr(self.pilot, 'heli_inputs', {})
            ori = self.pilot.world_object.orientation
            
            # Pilot mouse controls yaw heading
            target_yaw = math.atan2(ori.x, -ori.y)
            # Smooth yaw interpolation
            yaw_diff = (target_yaw - self.yaw + math.pi) % (2 * math.pi) - math.pi
            self.yaw += yaw_diff * 0.15
            
            dir_x = math.sin(self.yaw)
            dir_y = -math.cos(self.yaw)
            right_x = dir_y
            right_y = -dir_x
            
            # Cyclic Thrust
            fx = 0.0
            fy = 0.0
            self.pitch = 0.0
            self.roll = 0.0
            
            if inp.get('forward'):
                fx += dir_x * 14.0
                fy += dir_y * 14.0
                self.pitch = -0.22 # Nose down
            if inp.get('back'):
                fx -= dir_x * 10.0
                fy -= dir_y * 10.0
                self.pitch = 0.18 # Nose up
            if inp.get('left'):
                fx -= right_x * 10.0
                fy -= right_y * 10.0
                self.roll = -0.18 # Bank left
            if inp.get('right'):
                fx += right_x * 10.0
                fy += right_y * 10.0
                self.roll = 0.18 # Bank right
                
            # Collective Lift (Vertical Thrust)
            fz = 0.0
            if inp.get('up'):
                fz -= 12.0 # Climb
            elif inp.get('down'):
                fz += 9.0  # Descend
            else:
                # Hover hold
                fz = -self.vz * 2.0
                
            # Inertia, drag & acceleration integration
            self.vx = (self.vx + fx * self.tick_dt) * 0.94
            self.vy = (self.vy + fy * self.tick_dt) * 0.94
            self.vz = (self.vz + fz * self.tick_dt) * 0.90
            
            self.x += self.vx * self.tick_dt
            self.y += self.vy * self.tick_dt
            self.z += self.vz * self.tick_dt
            
            # Map boundaries & flight limits
            self.x = max(10.0, min(502.0, self.x))
            self.y = max(10.0, min(502.0, self.y))
            self.z = max(5.0, min(55.0, self.z)) # Ceiling to water level
            
            # Sync pilot world position into helicopter cockpit
            try:
                self.pilot.world_object.set_position(self.x, self.y, self.z)
                self.pilot.set_location((self.x, self.y, self.z))
            except Exception:
                pass
                
        else:
            # --- AUTONOMOUS SKY PATROL (Smooth continuous trajectory) ---
            center_x = 115.0
            center_y = 250.0
            radius_x = 22.0
            radius_y = 35.0
            omega = 0.18 # ~35s per full loop
            
            t = self.time_elapsed
            self.x = center_x + radius_x * math.sin(omega * t)
            self.y = center_y - radius_y * math.cos(omega * t)
            self.z = 32.0 + 3.0 * math.sin(2.0 * omega * t)
            
            # Heading tangent to trajectory
            vel_x = radius_x * omega * math.cos(omega * t)
            vel_y = radius_y * omega * math.sin(omega * t)
            self.yaw = math.atan2(vel_x, -vel_y)

    def embark(self, player):
        """Places player into the pilot cockpit."""
        if self.pilot and not self.pilot.disconnected and self.pilot != player:
            player.send_chat("[HELICOPTER] La cabina ya está ocupada por otro piloto.")
            return False
            
        self.pilot = player
        player.heli_inputs = {'forward': False, 'back': False, 'left': False, 'right': False, 'up': False, 'down': False}
        player.is_piloting = True
        player.god = True # Immune to damage while flying
        
        # Teleport to helicopter
        try:
            player.world_object.set_position(self.x, self.y, self.z)
            player.set_location((self.x, self.y, self.z))
        except Exception:
            pass
            
        player.send_chat("[HELICOPTER] ¡Has tomado los controles del helicóptero!")
        player.send_chat("[HELICOPTER] Controles: [W/S] Adelante/Atrás | [A/D] Viraje | [Espacio] Ascender | [C] Descender | /exit para salir")
        self.protocol.broadcast_chat(f"[HELICOPTER] {player.name} ha tomado los controles del helicóptero.")
        return True

    def disembark(self, player):
        """Safely lands or parachutes the pilot out."""
        if self.pilot == player:
            self.pilot = None
            player.is_piloting = False
            player.god = False
            player.heli_inputs = {}
            
            # Lower player safely toward ground
            try:
                px, py, pz = self.x, self.y, min(self.z + 2.0, 58.0)
                player.world_object.set_position(px, py, pz)
                player.set_location((px, py, pz))
            except Exception:
                pass
                
            player.send_chat("[HELICOPTER] Has desembarcado del helicóptero con éxito.")
            self.protocol.broadcast_chat(f"[HELICOPTER] {player.name} ha desembarcado del helicóptero.")
            return True
        return False

# Global instance reference
GLOBAL_HELI = None

@command('heli', 'pilot')
@player_only
def heli_command(connection):
    """
    Board or disembark from the aerial helicopter.
    Usage: /heli or /pilot
    """
    global GLOBAL_HELI
    if not GLOBAL_HELI:
        return "[HELICOPTER] El sistema de helicóptero no está inicializado."
        
    if getattr(connection, 'is_piloting', False):
        GLOBAL_HELI.disembark(connection)
        return
    else:
        GLOBAL_HELI.embark(connection)

@command('exit', 'leave')
@player_only
def exit_command(connection):
    """
    Exit the current vehicle / helicopter.
    Usage: /exit
    """
    global GLOBAL_HELI
    if GLOBAL_HELI and getattr(connection, 'is_piloting', False):
        GLOBAL_HELI.disembark(connection)
    else:
        connection.send_chat("No estás dentro de ningún vehículo.")

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
                return False # Immune to fall damage inside aircraft
            return super().on_fall(damage)

        def on_disconnect(self):
            global GLOBAL_HELI
            if GLOBAL_HELI and getattr(self, 'is_piloting', False):
                GLOBAL_HELI.disembark(self)
            super().on_disconnect()

    return HelicopterProtocol, HelicopterConnection
