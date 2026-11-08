"""
Classic Vintage Street Car (1930s Gangster / Street Cruiser Coupe)
System for yServer / piqueserver.

Features:
- High-fidelity ground vehicle driving physics (engine torque, Ackermann steering, braking, reverse).
- Terrain height sampling at all 4 wheels for pitch and roll slope alignment.
- Dynamic Ackermann front wheel steering angle and tire rolling sync.
- 2-passenger interior seating: Driver (steering wheel) + Copiloto / Passenger (shoots from window).
- Classic vintage horn ("Beep-Beep" / Ah-ooga) on Left Click or /honk.
- 3 Stability/Health Tiers (350 HP max):
  * Tier 3 (350-250 HP): Óptimo.
  * Tier 2 (249-100 HP): Dañado (humo del radiador frontal).
  * Tier 1 (99-1 HP): Crítico (fuego en el motor, velocidad reducida).
  * Tier 0 (0 HP): Explosión catastrófica (cráter 3x3x3, daño en área como 1 granada).
- Seamless network synchronization via [CAR_SYNC] at 20 Hz.
"""

import sys
import os
import math
from twisted.internet.task import LoopingCall
from piqueserver.commands import command, player_only
from pyspades.contained import BlockAction, GrenadePacket
from pyspades.constants import GRENADE_DESTROY, GRENADE_KILL

# Ensure scripts directory is on sys.path and this module is available as 'car'
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)
if __name__ in sys.modules:
    sys.modules['car'] = sys.modules[__name__]

class ActiveCar:
    def __init__(self, protocol, car_id=1):
        self.protocol = protocol
        self.id = car_id
        self.x = 256.0
        self.y = 256.0
        self.z = 32.0
        self.yaw = 0.0
        self.pitch = 0.0
        self.roll = 0.0
        self.steer_angle = 0.0
        
        self.vx = 0.0
        self.vy = 0.0
        self.vz = 0.0
        self.forward_speed = 0.0
        
        self.driver = None
        self.passenger = None
        
        self.hp = 350.0
        self.max_hp = 350.0
        self.tier = 3 # 3: Optimo, 2: Humo, 1: Fuego, 0: Destruido
        
        self.active = False
        self.loop = None
        self.sync_counter = 0
        self.time_elapsed = 0.0
        self.tick_dt = 0.05 # 20 Hz
        self.horn_cooldown = 0.0

    def start_loop(self):
        if self.loop is None:
            self.loop = LoopingCall(self.update_physics)
            self.loop.start(self.tick_dt, now=False)

    def stop_loop(self):
        if self.loop and self.loop.running:
            self.loop.stop()
        self.loop = None

    def spawn_at(self, x, y, z, yaw=0.0):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)
        self.yaw = float(yaw)
        self.pitch = 0.0
        self.roll = 0.0
        self.steer_angle = 0.0
        self.vx = 0.0
        self.vy = 0.0
        self.vz = 0.0
        self.forward_speed = 0.0
        self.hp = 350.0
        self.tier = 3
        self.active = True
        self.driver = None
        self.passenger = None
        self.start_loop()
        self.broadcast_sync()

    def update_tier(self):
        ratio = self.hp / self.max_hp
        old_tier = self.tier
        if ratio > 0.70:
            self.tier = 3
        elif ratio > 0.28:
            self.tier = 2
        elif ratio > 0.0:
            self.tier = 1
        else:
            self.tier = 0
            
        if self.tier != old_tier and self.tier > 0:
            tier_names = {3: "Óptimo", 2: "Motor Dañado (Humo)", 1: "Crítico (Motor en Llamas)"}
            self.protocol.broadcast_chat(f"[AUTO CLÁSICO] ¡Estado del vehículo cambiado a Nivel {self.tier}: {tier_names[self.tier]}! HP: {int(self.hp)}/{int(self.max_hp)}")

    def take_damage(self, amount, attacker=None):
        if not self.active or self.hp <= 0.0:
            return
            
        self.hp = max(0.0, self.hp - amount)
        self.update_tier()
        
        if self.hp <= 0.0:
            self.trigger_crash_explosion(self.x, self.y, self.z)
            return
            
        if attacker and not attacker.disconnected:
            attacker.send_chat(f"[AUTO CLÁSICO] ¡Impacto en carrocería! Salud: {int(self.hp)}/{int(self.max_hp)} HP [Nivel {self.tier}]")
            
        if self.driver and not self.driver.disconnected and attacker != self.driver:
            self.driver.send_chat(f"[AUTO CLÁSICO] ¡Carrocería impactada! (-{int(amount)} HP) | Salud: {int(self.hp)}/{int(self.max_hp)}")
            
        self.broadcast_sync()

    def trigger_crash_explosion(self, impact_x, impact_y, impact_z):
        """Destroys car with 1-grenade terrain explosion, area damage, and eliminates occupants."""
        ix = int(max(0, min(511, impact_x)))
        iy = int(max(0, min(511, impact_y)))
        iz = int(max(0, min(63, impact_z)))
        
        # 1. 3x3x3 block crater in terrain
        m = getattr(self.protocol, 'map', None)
        if m:
            for nx in range(ix - 1, ix + 2):
                for ny in range(iy - 1, iy + 2):
                    for nz in range(iz - 1, iz + 2):
                        if 0 <= nx < 512 and 0 <= ny < 512 and 0 <= nz < 64:
                            m.destroy_point(nx, ny, nz)
            ba = BlockAction()
            ba.x = ix
            ba.y = iy
            ba.z = iz
            ba.value = GRENADE_DESTROY
            ba.player_id = 31
            self.protocol.broadcast_contained(ba, save=True)
            
        # 2. Visual and audio Grenade Explosion
        gp = GrenadePacket()
        gp.value = 0.0
        gp.player_id = 31
        gp.position = (impact_x, impact_y, impact_z)
        gp.velocity = (0.0, 0.0, 0.0)
        self.protocol.broadcast_contained(gp)
        
        # 3. Splash damage to nearby players
        for p in self.protocol.players.values():
            if not p.disconnected and p.hp:
                ppos = p.world_object.position
                d_sq = (ppos.x - impact_x)**2 + (ppos.y - impact_y)**2 + (ppos.z - impact_z)**2
                if d_sq < 10.0**2:
                    dist = math.sqrt(d_sq)
                    dmg = max(10.0, 100.0 * (1.0 - dist / 10.0))
                    try:
                        p.hit(dmg, None, GRENADE_KILL)
                    except Exception:
                        pass
                        
        # 4. Eliminate driver and passenger
        if self.driver and not self.driver.disconnected:
            p = self.driver
            self.driver = None
            p.is_driving_car = False
            p.fly = False
            try:
                p.kill(killer=None, kill_type=GRENADE_KILL, grenade=None)
            except Exception:
                pass
                
        if self.passenger and not self.passenger.disconnected:
            cp = self.passenger
            self.passenger = None
            cp.is_car_passenger = False
            cp.fly = False
            try:
                cp.kill(killer=None, kill_type=GRENADE_KILL, grenade=None)
            except Exception:
                pass
                
        self.protocol.broadcast_chat("[AUTO CLÁSICO] ¡¡BOOM!! ¡El auto clásico fue destruido y EXPLOTÓ violentamente!")
        self.destroy()

    def destroy(self):
        self.hp = 0.0
        self.tier = 0
        self.active = False
        self.broadcast_sync()
        self.stop_loop()

    def get_ground_elevation(self, px, py):
        """Samples solid block elevation at map coordinate (px, py)."""
        ground_z = 63.0
        if hasattr(self.protocol, 'map'):
            m = self.protocol.map
            ix = int(max(0, min(511, px)))
            iy = int(max(0, min(511, py)))
            for cz in range(0, 63):
                if m.get_solid(ix, iy, cz):
                    ground_z = float(cz)
                    break
        return ground_z

    def get_seat_positions(self):
        """
        Calculates 3D coordinates for driver and passenger in 1970s Muscle Coupe:
        - Driver: Left front bench seat behind steering wheel.
        - Passenger: Right front bench seat (copiloto).
        """
        dir_x = math.sin(self.yaw)
        dir_y = -math.cos(self.yaw)
        right_x = dir_y
        right_y = -dir_x
        
        # Driver: offset -0.70 blocks right, -0.30 blocks forward, -1.35 blocks up (cabin bench)
        driver_pos = (
            self.x - right_x * 0.70 - dir_x * 0.30,
            self.y - right_y * 0.70 - dir_y * 0.30,
            self.z - 1.35
        )
        
        # Passenger: offset +0.70 blocks right, -0.30 blocks forward, -1.35 blocks up (copiloto bench)
        pass_pos = (
            self.x + right_x * 0.70 - dir_x * 0.30,
            self.y + right_y * 0.70 - dir_y * 0.30,
            self.z - 1.35
        )
        
        return driver_pos, pass_pos

    def honk(self):
        """Sounds the vintage street car horn."""
        if self.horn_cooldown <= 0.0:
            self.horn_cooldown = 1.2
            driver_name = self.driver.name if self.driver else "Conductor"
            self.protocol.broadcast_chat(f"[AUTO CLÁSICO] ¡¡BEEP-BEEP!! {driver_name} tocó la bocina clásica vintage.")

    def update_physics(self):
        self.time_elapsed += self.tick_dt
        self.sync_counter += 1
        if self.horn_cooldown > 0.0:
            self.horn_cooldown -= self.tick_dt
            
        if not self.active or self.hp <= 0.0:
            return
            
        # Clean disconnected occupants
        if self.driver and (self.driver.disconnected or not self.driver.hp):
            self.driver = None
        if self.passenger and (self.passenger.disconnected or not self.passenger.hp):
            self.passenger = None

        power_factor = 1.0 if self.tier == 3 else (0.80 if self.tier == 2 else 0.55)
        
        dir_x = math.sin(self.yaw)
        dir_y = -math.cos(self.yaw)
        right_x = dir_y
        right_y = -dir_x

        # 1. Sample ground height at all 4 wheels
        # Wheel offsets: front/rear +/- 2.2 blocks, left/right +/- 1.4 blocks
        z_fl = self.get_ground_elevation(self.x + dir_x * 2.2 - right_x * 1.4, self.y + dir_y * 2.2 - right_y * 1.4)
        z_fr = self.get_ground_elevation(self.x + dir_x * 2.2 + right_x * 1.4, self.y + dir_y * 2.2 + right_y * 1.4)
        z_rl = self.get_ground_elevation(self.x - dir_x * 2.2 - right_x * 1.4, self.y - dir_y * 2.2 - right_y * 1.4)
        z_rr = self.get_ground_elevation(self.x - dir_x * 2.2 + right_x * 1.4, self.y - dir_y * 2.2 + right_y * 1.4)
        
        ground_z = (z_fl + z_fr + z_rl + z_rr) * 0.25
        
        # Calculate pitch and roll based on slope of terrain under the wheels
        z_front = (z_fl + z_fr) * 0.5
        z_rear = (z_rl + z_rr) * 0.5
        z_left = (z_fl + z_rl) * 0.5
        z_right = (z_fr + z_rr) * 0.5
        
        target_pitch = math.atan2(z_front - z_rear, 4.4)
        target_roll = math.atan2(z_right - z_left, 2.8)

        # 2. Driver inputs and motion
        if self.driver and not self.driver.disconnected and self.driver.hp:
            inp = getattr(self.driver, 'car_inputs', {})
            ori = self.driver.world_object.orientation
            
            # Steering with A and D
            steer_input = 0.0
            if inp.get('left_a'):
                steer_input -= 1.0
            if inp.get('right_d'):
                steer_input += 1.0
                
            # Optional mouse steering: gentle steering if player looks away from car heading
            target_yaw_mouse = math.atan2(ori.x, -ori.y)
            yaw_diff = (target_yaw_mouse - self.yaw + math.pi) % (2.0 * math.pi) - math.pi
            if abs(steer_input) < 0.1 and abs(yaw_diff) > 0.10:
                steer_input = max(-1.0, min(1.0, yaw_diff * 1.5))
                
            target_steer = steer_input * 0.52 # Up to +/- 30 degrees Ackermann wheel turn
            self.steer_angle += (target_steer - self.steer_angle) * (12.0 * self.tick_dt)
            
            # Throttle and Braking
            max_forward_speed = 24.0 * power_factor # ~85 km/h top speed
            max_reverse_speed = -8.0 * power_factor
            
            if inp.get('up_w') or inp.get('jump'):
                # Throttle forward
                if self.forward_speed < max_forward_speed:
                    acc = 22.0 * power_factor
                    self.forward_speed = min(max_forward_speed, self.forward_speed + acc * self.tick_dt)
            elif inp.get('down_s') or inp.get('crouch'):
                # Braking / Reverse
                if self.forward_speed > 1.0:
                    # Strong brakes
                    decel = 32.0
                    self.forward_speed = max(0.0, self.forward_speed - decel * self.tick_dt)
                else:
                    # Reverse gear
                    acc_rev = 14.0 * power_factor
                    self.forward_speed = max(max_reverse_speed, self.forward_speed - acc_rev * self.tick_dt)
            else:
                # Rolling friction / engine compression deceleration
                friction = 8.5
                if self.forward_speed > 0.0:
                    self.forward_speed = max(0.0, self.forward_speed - friction * self.tick_dt)
                elif self.forward_speed < 0.0:
                    self.forward_speed = min(0.0, self.forward_speed + friction * self.tick_dt)
                    
            # Honk on left click
            if inp.get('click'):
                self.honk()
                
            # Ackermann yaw turning based on speed and steer angle
            if abs(self.forward_speed) > 0.1:
                # Turn rate is proportional to speed and steer angle (Ackermann geometry)
                turn_rate = (self.forward_speed / 4.2) * math.tan(self.steer_angle)
                self.yaw += turn_rate * self.tick_dt
                
            # Chassis centrifugal lean (vintage soft suspension body roll into corners)
            chassis_lean = -self.steer_angle * (self.forward_speed / 20.0) * 0.12
            target_roll += chassis_lean

        else:
            # Unoccupied: friction to stop
            self.forward_speed *= max(0.0, 1.0 - 6.0 * self.tick_dt)
            self.steer_angle *= max(0.0, 1.0 - 5.0 * self.tick_dt)

        # Smooth pitch & roll suspension damping
        self.pitch += (target_pitch - self.pitch) * (8.0 * self.tick_dt)
        self.roll += (target_roll - self.roll) * (8.0 * self.tick_dt)

        # Compute velocities
        self.vx = dir_x * self.forward_speed
        self.vy = dir_y * self.forward_speed

        # 3. Obstacle collision and step climbing
        next_x = self.x + self.vx * self.tick_dt
        next_y = self.y + self.vy * self.tick_dt
        
        # Check height in front of the car
        probe_x = next_x + dir_x * (2.2 if self.forward_speed >= 0 else -2.2)
        probe_y = next_y + dir_y * (2.2 if self.forward_speed >= 0 else -2.2)
        front_obstacle_z = self.get_ground_elevation(probe_x, probe_y)
        
        height_diff = self.z - front_obstacle_z # Positive if obstacle is higher (remember -Z is sky)
        
        if height_diff > 1.35 and abs(self.forward_speed) > 1.5:
            # Wall obstacle collision: halts forward momentum
            impact_speed = abs(self.forward_speed)
            self.forward_speed *= -0.20 # Slight bounce back
            if impact_speed > 14.0:
                self.take_damage(impact_speed * 1.5)
        else:
            # Clear or gentle slope/stair: integrate position
            self.x = max(10.0, min(502.0, next_x))
            self.y = max(10.0, min(502.0, next_y))
            
        # Ground following: wheels rest on surface (Z is ground_z - 0.20)
        target_z = ground_z - 0.20
        self.z += (target_z - self.z) * (14.0 * self.tick_dt)
        self.vz = (target_z - self.z)

        # 4. Synchronize occupant positions
        driver_pos, pass_pos = self.get_seat_positions()
        if self.driver and not self.driver.disconnected and self.driver.hp:
            try:
                self.driver.fly = True
                self.driver.world_object.set_position(*driver_pos)
                self.driver.set_location(driver_pos)
            except Exception:
                pass
                
        if self.passenger and not self.passenger.disconnected and self.passenger.hp:
            try:
                self.passenger.fly = True
                self.passenger.world_object.set_position(*pass_pos)
                self.passenger.set_location(pass_pos)
            except Exception:
                pass

        # Broadcast sync every tick (20 Hz = 50ms)
        self.broadcast_sync()

    def broadcast_sync(self):
        """Sends [CAR_SYNC] metadata to all connected clients."""
        driver_id = self.driver.player_id if (self.driver and not self.driver.disconnected and self.driver.hp) else -1
        pass_id = self.passenger.player_id if (self.passenger and not self.passenger.disconnected and self.passenger.hp) else -1
        
        sync_msg = f"[CAR_SYNC] 1 {self.x:.2f} {self.y:.2f} {self.z:.2f} {self.yaw:.2f} {self.pitch:.2f} {self.roll:.2f} {self.steer_angle:.2f} {driver_id} {pass_id} {int(self.hp)} {int(self.max_hp)} {self.tier} {self.vx:.2f} {self.vy:.2f} {self.vz:.2f}"
        
        for player in self.protocol.players.values():
            if not player.disconnected:
                try:
                    player.send_chat(sync_msg)
                except Exception:
                    pass

    def embark(self, player):
        """Boards a player as driver or passenger."""
        if self.hp <= 0.0 or not self.active:
            player.send_chat("[AUTO CLÁSICO] El vehículo está destruido. Despliega uno nuevo con /spawn car 1.")
            return False
            
        if player == self.driver or player == self.passenger:
            player.send_chat("[AUTO CLÁSICO] Ya estás a bordo del vehículo.")
            return False
            
        # Driver seat
        if not self.driver or self.driver.disconnected or not self.driver.hp:
            self.driver = player
            player.car_inputs = {'up_w': False, 'down_s': False, 'left_a': False, 'right_d': False, 'click': False, 'jump': False, 'crouch': False}
            player.is_driving_car = True
            player.is_car_passenger = False
            player.car_seat = 0
            player.fly = True
            
            driver_pos, _ = self.get_seat_positions()
            try:
                player.world_object.set_position(*driver_pos)
                player.set_location(driver_pos)
            except Exception:
                pass
                
            player.send_chat("[AUTO CLÁSICO] ¡Has tomado el volante como CONDUCTOR del auto clásico vintage!")
            player.send_chat(f"[AUTO CLÁSICO] Estado: {int(self.hp)}/{int(self.max_hp)} HP [Nivel {self.tier}]")
            player.send_chat("[AUTO CLÁSICO] Controles: [W] Acelerar | [S] Frenar/Reversa | [A/D] Girar | [Clic Izq] Bocina | [E] Salir")
            self.protocol.broadcast_chat(f"[AUTO CLÁSICO] {player.name} tomó el volante del auto clásico vintage.")
            self.broadcast_sync()
            return True
            
        # Passenger seat
        elif not self.passenger or self.passenger.disconnected or not self.passenger.hp:
            self.passenger = player
            player.is_driving_car = False
            player.is_car_passenger = True
            player.car_seat = 1
            player.fly = True
            
            driver_name = self.driver.name if self.driver else "Ninguno"
            player.send_chat("[AUTO CLÁSICO] ¡Has subido como COPILOTO en el asiento del pasajero!")
            player.send_chat(f"[AUTO CLÁSICO] Conductor: {driver_name} | Dispara a los enemigos por la ventanilla.")
            self.protocol.broadcast_chat(f"[AUTO CLÁSICO] {player.name} subió como copiloto en el auto clásico vintage.")
            self.broadcast_sync()
            return True
        else:
            player.send_chat("[AUTO CLÁSICO] El vehículo está lleno (2/2 Plazas ocupadas: Conductor + Copiloto).")
            return False

    def disembark(self, player):
        """Safely disembarks an occupant."""
        if player == self.passenger:
            self.passenger = None
            player.is_car_passenger = False
            player.fly = False
            player.car_seat = -1
            player.send_chat("[AUTO CLÁSICO] Has bajado del auto clásico.")
            self.protocol.broadcast_chat(f"[AUTO CLÁSICO] {player.name} bajó del asiento del copiloto.")
            self.broadcast_sync()
            return True
            
        if player == self.driver:
            self.driver = None
            player.is_driving_car = False
            player.fly = False
            player.car_inputs = {}
            player.car_seat = -1
            player.send_chat("[AUTO CLÁSICO] Has salido del asiento del conductor.")
            
            # Succession: promote passenger to driver
            if self.passenger and not self.passenger.disconnected and self.passenger.hp:
                new_driver = self.passenger
                self.passenger = None
                self.driver = new_driver
                new_driver.is_driving_car = True
                new_driver.is_car_passenger = False
                new_driver.car_seat = 0
                new_driver.car_inputs = {'up_w': False, 'down_s': False, 'left_a': False, 'right_d': False, 'click': False, 'jump': False, 'crouch': False}
                new_driver.send_chat("[AUTO CLÁSICO] ¡Has tomado el volante como nuevo CONDUCTOR!")
                self.protocol.broadcast_chat(f"[AUTO CLÁSICO] {new_driver.name} se pasó al asiento del conductor.")
            self.broadcast_sync()
            return True
            
        return False

# Global instance reference
GLOBAL_CAR = None

@command('car', 'auto', 'carro', 'coche', 'drive')
@player_only
def car_command(connection, *args):
    """
    Board or disembark from the classic vintage street car.
    Usage: /car or /auto
    """
    global GLOBAL_CAR
    if not GLOBAL_CAR:
        GLOBAL_CAR = ActiveCar(connection.protocol)
        
    if getattr(connection, 'is_driving_car', False) or getattr(connection, 'is_car_passenger', False):
        GLOBAL_CAR.disembark(connection)
        return
    else:
        # Distance check & auto-spawn if inactive or requested
        px, py, pz = connection.world_object.position.get()
        ori = connection.world_object.orientation if hasattr(connection.world_object, 'orientation') else None
        ox = ori.x if ori else 0.0
        oy = ori.y if ori else -1.0
        norm = math.sqrt(ox**2 + oy**2)
        dir_x, dir_y = (ox / norm, oy / norm) if norm > 0.001 else (0.0, -1.0)
        
        if not GLOBAL_CAR.active or (args and args[0].lower() in ('spawn', 'here', 'aqui', '1')):
            spawn_x = px + dir_x * 5.0
            spawn_y = py + dir_y * 5.0
            ground_z = pz
            if hasattr(connection.protocol, 'map'):
                m = connection.protocol.map
                for cz in range(max(1, int(pz) - 4), min(62, int(pz) + 8)):
                    if m.get_solid(int(spawn_x), int(spawn_y), cz):
                        ground_z = cz - 0.2
                        break
            yaw = math.atan2(ox, -oy) if hasattr(connection.world_object, 'orientation') else 0.0
            GLOBAL_CAR.spawn_at(spawn_x, spawn_y, ground_z, yaw=yaw)
            connection.send_chat(f"[AUTO CLÁSICO] Auto clásico vintage desplegado en ({spawn_x:.1f}, {spawn_y:.1f}, {ground_z:.1f})")

        if not GLOBAL_CAR.driver and not GLOBAL_CAR.passenger:
            dist = math.sqrt((px - GLOBAL_CAR.x)**2 + (py - GLOBAL_CAR.y)**2 + (pz - GLOBAL_CAR.z)**2)
            if dist > 20.0:
                connection.send_chat(f"[AUTO CLÁSICO] Estás demasiado lejos del auto ({dist:.1f} bloques). Acércate para subirte.")
                return
        GLOBAL_CAR.embark(connection)

@command('spawncar')
@player_only
def spawncar_command(connection, *args):
    """Spawn classic street car directly."""
    global GLOBAL_CAR
    if not GLOBAL_CAR:
        GLOBAL_CAR = ActiveCar(connection.protocol)
    px, py, pz = connection.world_object.position.get()
    ori = connection.world_object.orientation if hasattr(connection.world_object, 'orientation') else None
    ox = ori.x if ori else 0.0
    oy = ori.y if ori else -1.0
    norm = math.sqrt(ox**2 + oy**2)
    dir_x, dir_y = (ox / norm, oy / norm) if norm > 0.001 else (0.0, -1.0)
    spawn_x = px + dir_x * 5.0
    spawn_y = py + dir_y * 5.0
    ground_z = pz
    if hasattr(connection.protocol, 'map'):
        m = connection.protocol.map
        for cz in range(max(1, int(pz) - 4), min(62, int(pz) + 8)):
            if m.get_solid(int(spawn_x), int(spawn_y), cz):
                ground_z = cz - 0.2
                break
    yaw = math.atan2(ox, -oy) if hasattr(connection.world_object, 'orientation') else 0.0
    GLOBAL_CAR.spawn_at(spawn_x, spawn_y, ground_z, yaw=yaw)
    connection.send_chat(f"[AUTO CLÁSICO] Auto clásico vintage desplegado con 350 HP.")

@command('honk', 'horn', 'bocina')
@player_only
def honk_command(connection):
    """Honk the classic vintage car horn."""
    global GLOBAL_CAR
    if GLOBAL_CAR and getattr(connection, 'is_driving_car', False):
        GLOBAL_CAR.honk()
    else:
        connection.send_chat("Debes ser el conductor del auto clásico para tocar la bocina.")

@command('damagecar', 'dmgcar')
def damagecar_command(connection, amount="50"):
    """Inflict damage on the classic car."""
    global GLOBAL_CAR
    if GLOBAL_CAR and GLOBAL_CAR.active:
        try:
            dmg = float(amount)
        except ValueError:
            dmg = 50.0
        GLOBAL_CAR.take_damage(dmg, attacker=connection)
        return f"Auto clásico dañado por {dmg:.0f} HP."
    return "No hay auto clásico activo."

@command('setcarhp')
def setcarhp_command(connection, amount="350"):
    """Set the classic car HP directly."""
    global GLOBAL_CAR
    if GLOBAL_CAR and GLOBAL_CAR.active:
        try:
            val = float(amount)
        except ValueError:
            val = 350.0
        GLOBAL_CAR.hp = max(0.0, min(350.0, val))
        GLOBAL_CAR.update_tier()
        GLOBAL_CAR.broadcast_sync()
        connection.send_chat(f"[AUTO CLÁSICO] Salud ajustada a {int(GLOBAL_CAR.hp)}/350 [Nivel {GLOBAL_CAR.tier}]")
        return
    return "No hay auto clásico activo."

def apply_script(protocol, connection, config):
    class CarProtocol(protocol):
        def on_map_change(self, map):
            global GLOBAL_CAR
            GLOBAL_CAR = None
            return super().on_map_change(map)

    class CarConnection(connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.car_inputs = {'up_w': False, 'down_s': False, 'left_a': False, 'right_d': False, 'click': False, 'jump': False, 'crouch': False}
            self.is_driving_car = False
            self.is_car_passenger = False
            self.car_seat = -1

        def on_disconnect(self):
            global GLOBAL_CAR
            if GLOBAL_CAR:
                if self.is_driving_car or self.is_car_passenger:
                    GLOBAL_CAR.disembark(self)
            super().on_disconnect()

        def on_walk_update(self, up, down, left, right):
            if getattr(self, 'is_driving_car', False):
                self.car_inputs['up_w'] = bool(up)
                self.car_inputs['down_s'] = bool(down)
                self.car_inputs['left_a'] = bool(left)
                self.car_inputs['right_d'] = bool(right)
            super().on_walk_update(up, down, left, right)

        def on_animation_update(self, jump, crouch, sneak, sprint):
            if getattr(self, 'is_driving_car', False):
                self.car_inputs['jump'] = bool(jump)
                self.car_inputs['crouch'] = bool(crouch)
            super().on_animation_update(jump, crouch, sneak, sprint)

        def on_fall(self, damage):
            if getattr(self, 'is_driving_car', False) or getattr(self, 'is_car_passenger', False):
                return False
            return super().on_fall(damage)

        def on_hit(self, hit_amount, hit_player, kill_type, grenade):
            global GLOBAL_CAR
            if (getattr(hit_player, 'is_driving_car', False) or getattr(hit_player, 'is_car_passenger', False)) and GLOBAL_CAR and GLOBAL_CAR.active:
                car_dmg = hit_amount * 0.85
                GLOBAL_CAR.take_damage(car_dmg, attacker=self)
            return super().on_hit(hit_amount, hit_player, kill_type, grenade)

        def on_kill(self, killer, kill_type, grenade):
            global GLOBAL_CAR
            if getattr(self, 'is_driving_car', False) and GLOBAL_CAR:
                dead_p = self
                dead_p.is_driving_car = False
                dead_p.fly = False
                GLOBAL_CAR.driver = None
                
                if killer and not killer.disconnected:
                    self.protocol.broadcast_chat(f"[AUTO CLÁSICO] ¡{killer.name} abatió al conductor {dead_p.name} de un certero disparo al parabrisas!")
                    
                # Promote passenger to driver
                if GLOBAL_CAR.passenger and not GLOBAL_CAR.passenger.disconnected and GLOBAL_CAR.passenger.hp:
                    promoted = GLOBAL_CAR.passenger
                    GLOBAL_CAR.passenger = None
                    GLOBAL_CAR.driver = promoted
                    promoted.is_driving_car = True
                    promoted.is_car_passenger = False
                    promoted.car_seat = 0
                    promoted.car_inputs = {'up_w': False, 'down_s': False, 'left_a': False, 'right_d': False, 'click': False, 'jump': False, 'crouch': False}
                    
                    driver_pos, _ = GLOBAL_CAR.get_seat_positions()
                    try:
                        promoted.world_object.set_position(*driver_pos)
                        promoted.set_location(driver_pos)
                    except Exception:
                        pass
                    promoted.send_chat("[AUTO CLÁSICO] ¡El conductor cayó! ¡Te has pasado al volante y has TOMADO EL CONTROL!")
                    self.protocol.broadcast_chat(f"[AUTO CLÁSICO] ¡{promoted.name} se pasó al volante y tomó los mandos!")
                GLOBAL_CAR.broadcast_sync()
                
            elif getattr(self, 'is_car_passenger', False) and GLOBAL_CAR:
                self.is_car_passenger = False
                self.fly = False
                GLOBAL_CAR.passenger = None
                if killer and not killer.disconnected:
                    self.protocol.broadcast_chat(f"[AUTO CLÁSICO] ¡{killer.name} abatió al copiloto {self.name} de un disparo por la ventanilla!")
                GLOBAL_CAR.broadcast_sync()
                
            return super().on_kill(killer, kill_type, grenade)

        def on_shoot_set(self, fire):
            global GLOBAL_CAR
            if getattr(self, 'is_driving_car', False):
                self.car_inputs['click'] = bool(fire)
                if fire and GLOBAL_CAR:
                    GLOBAL_CAR.honk()
                return
            super().on_shoot_set(fire)

    return CarProtocol, CarConnection
