"""
Multi-Vehicle Ground System (5 Distinct Vehicles):
1. Chevrolet Chevelle SS Coupe 1970s (Classic Street Muscle Car V8)
2. Toyota Land Cruiser Serie 79 Pick-Up 4x4 "Macho" (Off-Road Snorkel & Mud Tires)
3. ISV Buggy Militar Táctico 4x4 (Open Tubular Roll Cage Squad Assault Buggy)
4. GMC Sierra Denali Crew Cab 4x4 (Luxury American Heavy Duty Truck)
5. Fiat Uno Turbo i.e. (Classic Red Italian Hot Hatch Pocket Rocket)

Features:
- Vehicle-specific ground physics (torque, max speed, climb clearance, roll damping).
- Dynamic 3D seating offsets for driver and passenger per vehicle cabin.
- 3 Damage/Health Tiers with visual engine smoke, sputtering, and non-lethal explosion.
- 20 Hz network synchronization via [CAR_SYNC].
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

VEHICLE_CONFIGS = {
    1: {
        'name': "Chevrolet Chevelle SS Coupe 1970s",
        'tag': "CHEVROLET CHEVELLE",
        'hp': 350.0,
        'max_speed': 24.0,
        'accel': 28.0,
        'reverse_speed': 10.0,
        'turn_rate_factor': 4.2,
        'step_tolerance': 1.6,
        'desc': "Muscle car de calle clásico con motor V8 rugiente y franja deportiva roja."
    },
    2: {
        'name': "Toyota Land Cruiser 79 Pick-Up 4x4 (Macho)",
        'tag': "TOYOTA LAND CRUISER 79",
        'hp': 450.0,
        'max_speed': 22.0,
        'accel': 26.0,
        'reverse_speed': 9.0,
        'turn_rate_factor': 4.4,
        'step_tolerance': 1.85, # Trepadora todoterreno superior
        'desc': "Pick-up 4x4 todoterreno blanca con franjas retro, snorkel y cauchos Mud-Terrain."
    },
    3: {
        'name': "ISV Buggy Militar Táctico 4x4",
        'tag': "ISV MILITAR TÁCTICO",
        'hp': 400.0,
        'max_speed': 25.5,
        'accel': 32.0,
        'reverse_speed': 11.0,
        'turn_rate_factor': 3.9,
        'step_tolerance': 1.75,
        'desc': "Vehículo de asalto con jaula antivuelco abierta (combate y tiro en 360°)."
    },
    4: {
        'name': "GMC Sierra Denali Crew Cab 4x4",
        'tag': "GMC SIERRA DENALI",
        'hp': 500.0,
        'max_speed': 23.0,
        'accel': 24.0,
        'reverse_speed': 9.5,
        'turn_rate_factor': 4.8,
        'step_tolerance': 1.6,
        'desc': "Pickup moderna plateada de lujo con parrilla cromada GMC masiva y blindaje pesado."
    },
    5: {
        'name': "Fiat Uno Turbo i.e.",
        'tag': "FIAT UNO TURBO I.E.",
        'hp': 280.0,
        'max_speed': 27.5, # El más veloz en recta!
        'accel': 35.0, # Turbo boost rápido!
        'reverse_speed': 10.5,
        'turn_rate_factor': 3.5, # Giro más cerrado y ágil
        'step_tolerance': 1.4,
        'desc': "Hatchback deportivo compacto italiano rojo, ultra ágil y con turbo boost."
    }
}

def parse_vehicle_type(arg):
    if not arg:
        return 1
    a = str(arg).lower().strip()
    if a in ('1', 'chevelle', 'malibu', 'muscle', 'clasico', 'classic', 'coupe'):
        return 1
    elif a in ('2', 'toyota', 'lc79', 'macho', 'machito', 'landcruiser', 'roraima', 'pickup4x4'):
        return 2
    elif a in ('3', 'isv', 'militar', 'buggy', 'tactico', 'tactical', 'army', 'escuadron'):
        return 3
    elif a in ('4', 'gmc', 'sierra', 'denali', 'pickup', 'truck', 'camioneta'):
        return 4
    elif a in ('5', 'fiat', 'uno', 'turbo', 'hatchback', 'fiatuno'):
        return 5
    try:
        val = int(a)
        if 1 <= val <= 5:
            return val
    except ValueError:
        pass
    return 1

class ActiveCar:
    def __init__(self, protocol, car_id=1, car_type=1):
        self.protocol = protocol
        self.id = car_id
        self.car_type = car_type
        self.apply_config(car_type)

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
        
        self.tier = 3 # 3: Optimo, 2: Humo, 1: Fuego, 0: Destruido
        self.active = False
        self.loop = None
        self.sync_counter = 0
        self.time_elapsed = 0.0
        self.tick_dt = 0.05 # 20 Hz
        self.horn_cooldown = 0.0

    def apply_config(self, car_type):
        cfg = VEHICLE_CONFIGS.get(car_type, VEHICLE_CONFIGS[1])
        self.car_type = car_type
        self.name = cfg['name']
        self.tag = cfg['tag']
        self.max_hp = cfg['hp']
        self.hp = cfg['hp']
        self.max_speed = cfg['max_speed']
        self.accel = cfg['accel']
        self.reverse_speed = cfg['reverse_speed']
        self.turn_rate_factor = cfg['turn_rate_factor']
        self.step_tolerance = cfg['step_tolerance']

    def start_loop(self):
        if self.loop is None:
            self.loop = LoopingCall(self.update_physics)
            self.loop.start(self.tick_dt, now=False)

    def stop_loop(self):
        if self.loop and self.loop.running:
            self.loop.stop()
        self.loop = None

    def spawn_at(self, x, y, z, yaw=0.0, car_type=None):
        if car_type is not None:
            self.apply_config(car_type)
        else:
            self.hp = self.max_hp

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
        self.tier = 3
        self.active = True
        self.driver = None
        self.passenger = None
        self.start_loop()
        self.broadcast_sync()

    def update_tier(self):
        ratio = self.hp / self.max_hp
        old_tier = self.tier
        if ratio > 0.52:
            self.tier = 3 # Óptimo: 100% velocidad, sin humo
        elif ratio > 0.18:
            self.tier = 2 # Motor Dañado: echa humo del capó y se pone un pelín más lento (75% potencia)
        elif ratio > 0.0:
            self.tier = 1 # Crítico: motor en llamas y humo denso (50% potencia)
        else:
            self.tier = 0 # Destruido / Explosión
            
        if self.tier != old_tier and self.tier > 0:
            tier_names = {
                3: "Óptimo (100% Potencia)",
                2: "Motor Dañado (Echando Humo, Velocidad Reducida)",
                1: "Crítico (Motor en Llamas, 50% Potencia)"
            }
            self.protocol.broadcast_chat(f"[{self.tag}] ¡Estado cambiado a Nivel {self.tier}: {tier_names[self.tier]}! HP: {int(self.hp)}/{int(self.max_hp)}")

    def take_damage(self, amount, attacker=None):
        if not self.active or self.hp <= 0.0:
            return
            
        self.hp = max(0.0, self.hp - amount)
        self.update_tier()
        
        if self.hp <= 0.0:
            self.trigger_crash_explosion()

    def trigger_crash_explosion(self):
        """Catastrophic vehicle explosion when HP hits 0."""
        impact_x = int(self.x)
        impact_y = int(self.y)
        impact_z = int(self.z)
        
        # 1. Crater excavation
        if hasattr(self.protocol, 'map'):
            m = self.protocol.map
            for dx in range(-1, 2):
                for dy in range(-1, 2):
                    for dz in range(-1, 2):
                        bx = impact_x + dx
                        by = impact_y + dy
                        bz = impact_z + dz
                        if 0 <= bx < 512 and 0 <= by < 512 and 0 <= bz < 63:
                            if m.get_solid(bx, by, bz):
                                m.destroy_point(bx, by, bz)
                                block_action = BlockAction()
                                block_action.x = bx
                                block_action.y = by
                                block_action.z = bz
                                block_action.value = GRENADE_DESTROY
                                self.protocol.broadcast_contained(block_action)

        # 2. Visual explosion & sound
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
                        
        # 4. Moderate damage to occupants (occupants survive to fight on foot)
        occupant_damage = 30.0
        if self.driver and not self.driver.disconnected:
            p = self.driver
            self.driver = None
            p.is_driving_car = False
            p.fly = False
            try:
                p.send_chat(f"[{self.tag}] ¡¡BOOM!! El vehículo explotó. Sufriste {int(occupant_damage)} HP de daño pero lograste saltar.")
                p.hit(occupant_damage, None, GRENADE_KILL)
            except Exception:
                pass
                
        if self.passenger and not self.passenger.disconnected:
            cp = self.passenger
            self.passenger = None
            cp.is_car_passenger = False
            cp.fly = False
            try:
                cp.send_chat(f"[{self.tag}] ¡¡BOOM!! El vehículo explotó. Sufriste {int(occupant_damage)} HP de daño pero lograste saltar.")
                cp.hit(occupant_damage, None, GRENADE_KILL)
            except Exception:
                pass
                
        self.protocol.broadcast_chat(f"[{self.tag}] ¡¡BOOM!! ¡El vehículo fue destruido y EXPLOTÓ!")
        self.destroy()

    def destroy(self):
        self.hp = 0.0
        self.tier = 0
        self.active = False
        self.broadcast_sync()
        self.stop_loop()

    def get_ground_elevation(self, px, py):
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
        dir_x = math.sin(self.yaw)
        dir_y = -math.cos(self.yaw)
        right_x = dir_y
        right_y = -dir_x
        
        seat_x = 0.85
        seat_y = 0.35
        seat_z = 1.60
        if self.car_type == 2: # Toyota LC79
            seat_x = 0.90; seat_y = 0.30; seat_z = 1.85
        elif self.car_type == 3: # Military ISV
            seat_x = 0.90; seat_y = 0.35; seat_z = 1.70
        elif self.car_type == 4: # GMC Sierra Denali
            seat_x = 0.98; seat_y = 0.40; seat_z = 1.80
        elif self.car_type == 5: # Fiat Uno Turbo
            seat_x = 0.72; seat_y = 0.25; seat_z = 1.45
            
        driver_pos = (
            self.x - right_x * seat_x - dir_x * seat_y,
            self.y - right_y * seat_x - dir_y * seat_y,
            self.z - seat_z
        )
        pass_pos = (
            self.x + right_x * seat_x - dir_x * seat_y,
            self.y + right_y * seat_x - dir_y * seat_y,
            self.z - seat_z
        )
        return driver_pos, pass_pos

    def honk(self):
        if self.horn_cooldown <= 0.0:
            self.horn_cooldown = 0.8
            self.protocol.broadcast_chat(f"[{self.tag}] *¡¡BEEP-BEEP!!*")

    def update_physics(self):
        if not self.active:
            return

        self.time_elapsed += self.tick_dt
        if self.horn_cooldown > 0.0:
            self.horn_cooldown = max(0.0, self.horn_cooldown - self.tick_dt)

        # 1. 4-Wheel Terrain Elevation Probing
        dir_x = math.sin(self.yaw)
        dir_y = -math.cos(self.yaw)
        right_x = dir_y
        right_y = -dir_x
        
        half_wb = 2.6
        half_tw = 1.6
        if self.car_type == 4: # GMC
            half_wb = 3.0
            half_tw = 1.8
        elif self.car_type == 5: # Fiat
            half_wb = 2.2
            half_tw = 1.3
        
        fl_x, fl_y = self.x + dir_x * half_wb - right_x * half_tw, self.y + dir_y * half_wb - right_y * half_tw
        fr_x, fr_y = self.x + dir_x * half_wb + right_x * half_tw, self.y + dir_y * half_wb + right_y * half_tw
        rl_x, rl_y = self.x - dir_x * half_wb - right_x * half_tw, self.y - dir_y * half_wb - right_y * half_tw
        rr_x, rr_y = self.x - dir_x * half_wb + right_x * half_tw, self.y - dir_y * half_wb + right_y * half_tw
        
        z_fl = self.get_ground_elevation(fl_x, fl_y)
        z_fr = self.get_ground_elevation(fr_x, fr_y)
        z_rl = self.get_ground_elevation(rl_x, rl_y)
        z_rr = self.get_ground_elevation(rr_x, rr_y)
        
        z_front = (z_fl + z_fr) * 0.5
        z_rear  = (z_rl + z_rr) * 0.5
        z_left  = (z_fl + z_rl) * 0.5
        z_right = (z_fr + z_rr) * 0.5
        
        target_ground_z = (z_front + z_rear) * 0.5 - 0.2
        target_pitch = math.atan2(z_front - z_rear, half_wb * 2.0)
        target_roll  = -math.atan2(z_right - z_left, half_tw * 2.0)

        # 2. Driver input processing
        if self.driver and not self.driver.disconnected and self.driver.hp:
            inp = getattr(self.driver, 'car_inputs', {})
            
            # Steering
            max_steer = 0.58 if self.car_type == 5 else 0.52
            steer_speed = 2.8 if self.car_type == 5 else 2.2
            if inp.get('left_a'):
                self.steer_angle = max(-max_steer, self.steer_angle - steer_speed * self.tick_dt)
            elif inp.get('right_d'):
                self.steer_angle = min(max_steer, self.steer_angle + steer_speed * self.tick_dt)
            else:
                self.steer_angle *= max(0.0, 1.0 - 5.0 * self.tick_dt)
                
            # Power multiplier based on damage tier
            power_mult = 1.0
            if self.tier == 2:
                power_mult = 0.75 # "un pelín más lento"
            elif self.tier == 1:
                power_mult = 0.50 # Crítico
                
            max_fwd = self.max_speed * power_mult
            accel_val = self.accel * power_mult
            
            if inp.get('up_w'):
                if self.forward_speed < 0.0:
                    self.forward_speed += 32.0 * self.tick_dt # Strong braking
                else:
                    self.forward_speed = min(max_fwd, self.forward_speed + accel_val * self.tick_dt)
            elif inp.get('down_s'):
                if self.forward_speed > 0.0:
                    self.forward_speed = max(0.0, self.forward_speed - 32.0 * self.tick_dt) # Strong braking
                else:
                    self.forward_speed = max(-self.reverse_speed, self.forward_speed - 14.0 * self.tick_dt) # Reverse
            else:
                friction = 6.0
                if self.forward_speed > 0.0:
                    self.forward_speed = max(0.0, self.forward_speed - friction * self.tick_dt)
                elif self.forward_speed < 0.0:
                    self.forward_speed = min(0.0, self.forward_speed + friction * self.tick_dt)
                    
            if inp.get('click'):
                self.honk()
                
            if abs(self.forward_speed) > 0.1:
                turn_rate = (self.forward_speed / self.turn_rate_factor) * math.tan(self.steer_angle)
                self.yaw += turn_rate * self.tick_dt
                
            chassis_lean = -self.steer_angle * (self.forward_speed / 20.0) * 0.12
            target_roll += chassis_lean

        else:
            self.forward_speed *= max(0.0, 1.0 - 6.0 * self.tick_dt)
            self.steer_angle *= max(0.0, 1.0 - 5.0 * self.tick_dt)

        self.pitch += (target_pitch - self.pitch) * (8.0 * self.tick_dt)
        self.roll += (target_roll - self.roll) * (8.0 * self.tick_dt)

        # 3. Position integration
        self.vx = dir_x * self.forward_speed
        self.vy = dir_y * self.forward_speed
        
        cand_x = self.x + self.vx * self.tick_dt
        cand_y = self.y + self.vy * self.tick_dt
        
        # Smooth step climbing & wall collision check
        obstacle_z = self.get_ground_elevation(cand_x + dir_x * 1.5, cand_y + dir_y * 1.5)
        height_diff = self.z - obstacle_z
        
        if height_diff > self.step_tolerance:
            # Wall impact: crash stop & damage
            impact_speed = abs(self.forward_speed)
            if impact_speed > 6.0:
                self.take_damage(impact_speed * 1.5)
            self.forward_speed = 0.0
            self.vx = 0.0
            self.vy = 0.0
        else:
            self.x = max(2.0, min(509.0, cand_x))
            self.y = max(2.0, min(509.0, cand_y))
            
        z_blend = 1.0 - math.exp(-12.0 * self.tick_dt)
        self.z += (target_ground_z - self.z) * z_blend
        self.vz = (target_ground_z - self.z)

        # 4. Sync passengers
        driver_pos, pass_pos = self.get_seat_positions()
        if self.driver and not self.driver.disconnected and self.driver.hp:
            try:
                self.driver.world_object.set_position(*driver_pos)
                self.driver.set_location(driver_pos)
            except Exception:
                pass
                
        if self.passenger and not self.passenger.disconnected and self.passenger.hp:
            try:
                self.passenger.world_object.set_position(*pass_pos)
                self.passenger.set_location(pass_pos)
            except Exception:
                pass

        self.broadcast_sync()

    def broadcast_sync(self):
        """Sends [CAR_SYNC] metadata to all connected clients."""
        driver_id = self.driver.player_id if (self.driver and not self.driver.disconnected and self.driver.hp) else -1
        pass_id = self.passenger.player_id if (self.passenger and not self.passenger.disconnected and self.passenger.hp) else -1
        
        # 17 tokens: id, x, y, z, yaw, pitch, roll, steer, driver, pass, hp, max_hp, tier, vx, vy, vz, model_type
        sync_msg = f"[CAR_SYNC] {self.car_type} {self.x:.2f} {self.y:.2f} {self.z:.2f} {self.yaw:.2f} {self.pitch:.2f} {self.roll:.2f} {self.steer_angle:.2f} {driver_id} {pass_id} {int(self.hp)} {int(self.max_hp)} {self.tier} {self.vx:.2f} {self.vy:.2f} {self.vz:.2f} {self.car_type}"
        
        for player in self.protocol.players.values():
            if not player.disconnected:
                try:
                    player.send_chat(sync_msg)
                except Exception:
                    pass

    def embark(self, player):
        if self.hp <= 0.0 or not self.active:
            player.send_chat(f"[{self.tag}] El vehículo está destruido. Despliega uno nuevo con /spawn car [1-5].")
            return False
            
        if player == self.driver or player == self.passenger:
            player.send_chat(f"[{self.tag}] Ya estás a bordo del vehículo.")
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
                
            player.send_chat(f"[{self.tag}] ¡Tomaste el volante de: {self.name}!")
            player.send_chat(f"[{self.tag}] Controles: [W] Acelerar, [S] Frenar/Reversa, [A/D] Girar, [Clic Izq] Bocina, [E] Salir")
            self.protocol.broadcast_chat(f"[{self.tag}] {player.name} se subió como conductor de {self.name}.")
            self.broadcast_sync()
            return True
            
        # Passenger seat
        elif not self.passenger or self.passenger.disconnected or not self.passenger.hp:
            self.passenger = player
            player.is_driving_car = False
            player.is_car_passenger = True
            player.car_seat = 1
            player.fly = True
            
            _, pass_pos = self.get_seat_positions()
            try:
                player.world_object.set_position(*pass_pos)
                player.set_location(pass_pos)
            except Exception:
                pass
                
            player.send_chat(f"[{self.tag}] ¡Te subiste como copiloto/pasajero en {self.name}!")
            player.send_chat(f"[{self.tag}] ¡Las ventanillas están abiertas! Puedes apuntar y disparar con tu arma.")
            self.protocol.broadcast_chat(f"[{self.tag}] {player.name} se subió como acompañante en {self.name}.")
            self.broadcast_sync()
            return True
            
        else:
            player.send_chat(f"[{self.tag}] El vehículo está lleno (2/2 ocupantes).")
            return False

    def disembark(self, player):
        if player == self.driver:
            self.driver = None
            player.is_driving_car = False
            player.fly = False
            player.send_chat(f"[{self.tag}] Te bajaste del vehículo.")
            self.protocol.broadcast_chat(f"[{self.tag}] {player.name} dejó el volante de {self.name}.")
            
            # Place player to left of vehicle
            dir_x = math.sin(self.yaw)
            dir_y = -math.cos(self.yaw)
            right_x = dir_y
            right_y = -dir_x
            exit_pos = (self.x - right_x * 2.2, self.y - right_y * 2.2, self.z - 0.5)
            try:
                player.world_object.set_position(*exit_pos)
                player.set_location(exit_pos)
            except Exception:
                pass
            self.broadcast_sync()
            return True
            
        elif player == self.passenger:
            self.passenger = None
            player.is_car_passenger = False
            player.fly = False
            player.send_chat(f"[{self.tag}] Te bajaste del vehículo.")
            self.protocol.broadcast_chat(f"[{self.tag}] {player.name} se bajó de {self.name}.")
            
            dir_x = math.sin(self.yaw)
            dir_y = -math.cos(self.yaw)
            right_x = dir_y
            right_y = -dir_x
            exit_pos = (self.x + right_x * 2.2, self.y + right_y * 2.2, self.z - 0.5)
            try:
                player.world_object.set_position(*exit_pos)
                player.set_location(exit_pos)
            except Exception:
                pass
            self.broadcast_sync()
            return True
            
        return False

GLOBAL_CAR = None

@command('car', 'auto', 'carro', 'coche', 'drive')
@player_only
def car_command(connection, *args):
    """
    Board, disembark or select a vehicle:
    /car 1 -> Chevelle Coupe
    /car 2 -> Toyota LC79 4x4
    /car 3 -> ISV Buggy Militar
    /car 4 -> GMC Sierra Denali
    /car 5 -> Fiat Uno Turbo
    """
    global GLOBAL_CAR
    v_type = parse_vehicle_type(args[0]) if args else 1

    if not GLOBAL_CAR:
        GLOBAL_CAR = ActiveCar(connection.protocol, car_type=v_type)
        
    if getattr(connection, 'is_driving_car', False) or getattr(connection, 'is_car_passenger', False):
        GLOBAL_CAR.disembark(connection)
        return
    else:
        px, py, pz = connection.world_object.position.get()
        ori = connection.world_object.orientation if hasattr(connection.world_object, 'orientation') else None
        ox = ori.x if ori else 0.0
        oy = ori.y if ori else -1.0
        norm = math.sqrt(ox**2 + oy**2)
        dir_x, dir_y = (ox / norm, oy / norm) if norm > 0.001 else (0.0, -1.0)
        
        need_spawn = not GLOBAL_CAR.active or (args and (args[0].lower() in ('spawn', 'here', 'aqui') or any(str(i) in args[0] for i in range(1, 6))))
        if need_spawn:
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
            GLOBAL_CAR.spawn_at(spawn_x, spawn_y, ground_z, yaw=yaw, car_type=v_type)
            connection.send_chat(f"[{GLOBAL_CAR.tag}] Desplegado en ({spawn_x:.1f}, {spawn_y:.1f}, {ground_z:.1f}) con {int(GLOBAL_CAR.hp)} HP.")

        if not GLOBAL_CAR.driver and not GLOBAL_CAR.passenger:
            dist = math.sqrt((px - GLOBAL_CAR.x)**2 + (py - GLOBAL_CAR.y)**2 + (pz - GLOBAL_CAR.z)**2)
            if dist > 20.0:
                connection.send_chat(f"[{GLOBAL_CAR.tag}] Estás lejos del vehículo ({dist:.1f} bloques). Acércate para subirte.")
                return
        GLOBAL_CAR.embark(connection)

@command('spawncar')
@player_only
def spawncar_command(connection, *args):
    """Spawn vehicle directly: /spawncar [1-5|toyota|isv|gmc|fiat]"""
    global GLOBAL_CAR
    v_type = parse_vehicle_type(args[0]) if args else 1
    if not GLOBAL_CAR:
        GLOBAL_CAR = ActiveCar(connection.protocol, car_type=v_type)
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
    GLOBAL_CAR.spawn_at(spawn_x, spawn_y, ground_z, yaw=yaw, car_type=v_type)
    connection.send_chat(f"[{GLOBAL_CAR.tag}] {GLOBAL_CAR.name} desplegado con {int(GLOBAL_CAR.hp)} HP.")

@command('honk', 'horn', 'bocina')
def honk_command(connection):
    global GLOBAL_CAR
    if GLOBAL_CAR and GLOBAL_CAR.active:
        GLOBAL_CAR.honk()
    else:
        return "No hay ningún vehículo activo en este momento."

@command('damagecar', 'dmgcar')
def damagecar_command(connection, amount="50"):
    global GLOBAL_CAR
    if GLOBAL_CAR and GLOBAL_CAR.active:
        try:
            dmg = float(amount)
            GLOBAL_CAR.take_damage(dmg)
            connection.send_chat(f"[{GLOBAL_CAR.tag}] Infligiste {dmg:.0f} de daño. HP restante: {int(GLOBAL_CAR.hp)}/{int(GLOBAL_CAR.max_hp)}.")
        except ValueError:
            return "Uso: /damagecar <cantidad>"
    else:
        return "No hay ningún vehículo activo en este momento."

@command('setcarhp')
def setcarhp_command(connection, amount="350"):
    global GLOBAL_CAR
    if GLOBAL_CAR and GLOBAL_CAR.active:
        try:
            val = float(amount)
            GLOBAL_CAR.hp = min(GLOBAL_CAR.max_hp, max(1.0, val))
            GLOBAL_CAR.update_tier()
            connection.send_chat(f"[{GLOBAL_CAR.tag}] HP establecido a: {int(GLOBAL_CAR.hp)}/{int(GLOBAL_CAR.max_hp)}.")
        except ValueError:
            return "Uso: /setcarhp <cantidad>"
    else:
        return "No hay ningún vehículo activo en este momento."

def apply_script(protocol, connection, config):
    class CarProtocol(protocol):
        def on_map_change(self, map):
            global GLOBAL_CAR
            if GLOBAL_CAR:
                GLOBAL_CAR.stop_loop()
                GLOBAL_CAR = None
            return protocol.on_map_change(self, map)

    class CarConnection(connection):
        def __init__(self, *args, **kwargs):
            connection.__init__(self, *args, **kwargs)
            self.is_driving_car = False
            self.is_car_passenger = False
            self.car_seat = -1
            self.car_inputs = {
                'up_w': False,
                'down_s': False,
                'left_a': False,
                'right_d': False,
                'click': False,
                'jump': False,
                'crouch': False
            }

        def on_disconnect(self):
            global GLOBAL_CAR
            if GLOBAL_CAR and GLOBAL_CAR.active:
                if self == GLOBAL_CAR.driver or self == GLOBAL_CAR.passenger:
                    GLOBAL_CAR.disembark(self)
            return connection.on_disconnect(self)

        def on_kill(self, killer, kill_type, grenade):
            global GLOBAL_CAR
            if GLOBAL_CAR and GLOBAL_CAR.active:
                if self == GLOBAL_CAR.driver or self == GLOBAL_CAR.passenger:
                    GLOBAL_CAR.disembark(self)
            return connection.on_kill(self, killer, kill_type, grenade)

        def on_walk(self, walk):
            if self.is_driving_car:
                self.car_inputs['up_w'] = getattr(walk, 'up', False)
                self.car_inputs['down_s'] = getattr(walk, 'down', False)
                self.car_inputs['left_a'] = getattr(walk, 'left', False)
                self.car_inputs['right_d'] = getattr(walk, 'right', False)
                return False
            elif self.is_car_passenger:
                return False
            return connection.on_walk(self, walk)

        def on_primary(self, value):
            if self.is_driving_car:
                self.car_inputs['click'] = bool(value)
                return False
            return connection.on_primary(self, value)

        def on_secondary(self, value):
            if self.is_driving_car:
                return False
            return connection.on_secondary(self, value)

        def on_jump(self):
            if self.is_driving_car or self.is_car_passenger:
                return False
            return connection.on_jump(self)

        def on_crouch(self):
            if self.is_driving_car or self.is_car_passenger:
                return False
            return connection.on_crouch(self)

    return CarProtocol, CarConnection
