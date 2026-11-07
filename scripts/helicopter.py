"""
Advanced Military Combat Helicopter System with 3 Seats, Pilot Succession,
Aerodynamics, Health Tiers, and Unpiloted High-Speed Crash Explosions.

Features:
- 3 Seats Capacity:
    * Seat 0: Conductor (Piloto) - Ubicado en la cabina frontal detrás de la ventana.
    * Seat 1: Compañero Izquierdo - Sentado en el borde lateral exterior expuesto.
    * Seat 2: Compañero Derecho - Sentado en el borde lateral exterior expuesto.
- Ground Combat & Hitboxes:
    * Enemigos en tierra pueden disparar al fuselaje del helicóptero (radio 7.5 bloques).
    * Enemigos pueden apuntar a la ventana frontal y abatir al conductor (radio 2.2 bloques).
    * Enemigos pueden apuntar a los bordes laterales y abatir a los compañeros (radio 2.0 bloques).
- Sucesión de Conductor:
    * Si el conductor es abatido por disparos terrestres, el juego mueve inmediatamente
      al primer compañero disponible al asiento del conductor en la ventana y le otorga
      los mandos de vuelo.
- Descenso sin Conductor y Explosión contra el Piso:
    * En caso de no tener conductores (el piloto murió o saltó y no hay compañeros),
      el helicóptero pierde sustentación y desciende acelerando con velocidad gravitatoria.
    * Si impacta contra el suelo con suficiente velocidad (o en picada descontrolada),
      ¡EXPLOTA contra el piso provocando una explosión equivalente a 1 granada!
      (Cráter 3x3x3, daño en área balístico, efecto sonoro y visual de granada).
"""

import math
import time
from twisted.internet.task import LoopingCall
from piqueserver.commands import command, player_only
from pyspades.contained import BlockAction, GrenadePacket
from pyspades.constants import GRENADE_DESTROY, GRENADE_KILL

class ActiveHelicopter:
    """Manages the 3D position, orientation, aerodynamic physics, 3-seats, succession and crash explosions."""
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
        
        # 3-Seat Occupants
        self.pilot = None          # Seat 0: Conductor (ventana frontal)
        self.passengers = []       # Max 2 compañeros (Seat 1: lateral izq, Seat 2: lateral der)
        
        # Health & Stability Tiers
        self.hp = 300.0
        self.max_hp = 300.0
        self.tier = 3              # 3: Óptimo, 2: Inestable, 1: Crítico (Rotor cola), 0: Destruido
        self.is_flying = False
        self.uncontrolled_crash = False
        self.active = True
        self.time_elapsed = 0.0
        self.sync_counter = 0
        
        # 20 Hz physics update tick (50ms)
        self.tick_dt = 0.05
        self.loop = LoopingCall(self.update_physics)
        self.loop.start(self.tick_dt, now=False)
        print(f"[HELICOPTER] Military Combat Helicopter active at ({self.x:.1f}, {self.y:.1f}, {self.z:.1f})")

    def get_seat_positions(self):
        """
        Calculates exact world positions for:
        - Pilot (Seat 0): Inside front window cockpit.
        - Companion 1 (Seat 1): Exposed on left lateral edge bench.
        - Companion 2 (Seat 2): Exposed on right lateral edge bench.
        """
        dir_x = math.sin(self.yaw)
        dir_y = -math.cos(self.yaw)
        right_x = dir_y
        right_y = -dir_x
        
        # Conductor: ventana frontal (cabina)
        pilot_pos = (self.x + dir_x * 2.2, self.y + dir_y * 2.2, self.z - 2.0)
        
        # Compañero 1: lateral izquierdo en el borde exterior
        p1_pos = (self.x - right_x * 1.6 - dir_x * 0.4, self.y - right_y * 1.6 - dir_y * 0.4, self.z - 1.4)
        
        # Compañero 2: lateral derecho en el borde exterior
        p2_pos = (self.x + right_x * 1.6 - dir_x * 0.4, self.y + right_y * 1.6 - dir_y * 0.4, self.z - 1.4)
        
        return pilot_pos, p1_pos, p2_pos

    def spawn_at(self, x, y, z, yaw=0.0):
        """Spawns or repairs the helicopter parked at a specified position."""
        if self.pilot and not self.pilot.disconnected:
            self.disembark(self.pilot)
        for cp in self.passengers[:]:
            if not cp.disconnected:
                self.disembark(cp)
        self.passengers.clear()
            
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
        self.uncontrolled_crash = False
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
            attacker.send_chat(f"[HELICOPTER] ¡Impacto certero al fuselaje! Salud: {int(self.hp)}/{int(self.max_hp)} HP [Nivel {self.tier}: {tier_str}]")
            
        if self.pilot and not self.pilot.disconnected and attacker != self.pilot:
            self.pilot.send_chat(f"[HELICOPTER] ¡Fuselaje impactado por {attacker.name if attacker else 'un enemigo'}! (-{int(amount)} HP) | Salud: {int(self.hp)}/300")
            
        self.broadcast_sync()

    def trigger_crash_explosion(self, impact_x, impact_y, impact_z):
        """
        Triggers a 1-grenade equivalent explosion upon high-speed impact with the ground:
        - Creates a 3x3x3 block crater in the terrain.
        - Emits a standard grenade explosion packet (flash, smoke, particle debris, sound).
        - Deals area splash damage to nearby players (100% damage dropoff up to 10 blocks).
        - Destroys vehicle and eliminates any remaining passengers.
        """
        ix = int(max(0, min(511, impact_x)))
        iy = int(max(0, min(511, impact_y)))
        iz = int(max(0, min(63, impact_z)))
        
        # 1. 3x3x3 block destruction crater in terrain (equal to 1 grenade)
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
            
        # 2. Visual and audio Grenade Explosion packet
        gp = GrenadePacket()
        gp.value = 0.0
        gp.player_id = 31
        gp.position = (impact_x, impact_y, impact_z)
        gp.velocity = (0.0, 0.0, 0.0)
        self.protocol.broadcast_contained(gp)
        
        # 3. Area damage to nearby players (equivalent to 1 grenade)
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
                        
        # 4. Eliminate occupants in the explosion
        if self.pilot and not self.pilot.disconnected:
            p = self.pilot
            self.pilot = None
            p.is_piloting = False
            p.fly = False
            try:
                p.kill(killer=None, kill_type=GRENADE_KILL, grenade=None)
            except Exception:
                pass
                
        for cp in self.passengers[:]:
            if not cp.disconnected:
                cp.is_passenger = False
                cp.fly = False
                try:
                    cp.kill(killer=None, kill_type=GRENADE_KILL, grenade=None)
                except Exception:
                    pass
        self.passengers.clear()
        
        self.protocol.broadcast_chat("[HELICOPTER] ¡¡CRASH!! ¡El helicóptero sin conductor se estrelló a gran velocidad contra el piso y EXPLOTÓ como 1 granada!")
        self.destroy()

    def destroy(self):
        """Triggers cataclysmic explosion and vehicle elimination."""
        self.hp = 0.0
        self.tier = 0
        self.is_flying = False
        self.uncontrolled_crash = False
        self.protocol.broadcast_chat("[HELICOPTER] ¡BOOM! ¡El helicóptero militar ha sido DESTRUIDO!")
        
        if self.pilot and not self.pilot.disconnected:
            p = self.pilot
            self.pilot = None
            p.is_piloting = False
            p.fly = False
            try:
                p.kill(killer=None, kill_type=GRENADE_KILL, grenade=None)
            except Exception:
                pass
                
        for cp in self.passengers[:]:
            if not cp.disconnected:
                cp.is_passenger = False
                cp.fly = False
                try:
                    cp.kill(killer=None, kill_type=GRENADE_KILL, grenade=None)
                except Exception:
                    pass
        self.passengers.clear()
        
        self.broadcast_sync()
        self.active = False

    def update_physics(self):
        self.time_elapsed += self.tick_dt
        self.sync_counter += 1
        
        if not self.active or self.hp <= 0.0:
            return
            
        # Clean up any disconnected passengers
        self.passengers = [p for p in self.passengers if not p.disconnected and p.hp]
        
        # Calculate terrain surface elevation below helicopter
        ground_z = 63.0
        if hasattr(self.protocol, 'map'):
            m = self.protocol.map
            px_i = int(max(0, min(511, self.x)))
            py_i = int(max(0, min(511, self.y)))
            for cz in range(0, 63):
                if m.get_solid(px_i, py_i, cz):
                    ground_z = float(cz)
                    break
        
        # =========================================================================
        # CASE A: CONDUCTOR AL MANDO (VUELO PILOTADO ACTIVO)
        # =========================================================================
        if self.pilot and not self.pilot.disconnected and self.pilot.hp:
            self.is_flying = True
            self.uncontrolled_crash = False
            inp = getattr(self.pilot, 'heli_inputs', {})
            ori = self.pilot.world_object.orientation
            
            # --- 1. Control de Guiñada (Yaw Heading) ---
            target_yaw = math.atan2(ori.x, -ori.y)
            yaw_diff = (target_yaw - self.yaw + math.pi) % (2 * math.pi) - math.pi
            self.yaw += yaw_diff * 0.16
            
            # --- Efecto Tier 1: Fallo de Rotor de Cola (Giro por Torque) ---
            if self.tier == 1:
                torque_spin = 2.4 # rad/sec
                self.yaw += torque_spin * self.tick_dt
            
            dir_x = math.sin(self.yaw)
            dir_y = -math.cos(self.yaw)
            right_x = dir_y
            right_y = -dir_x
            
            # --- 2. Empuje Cíclico y Cabeceo Táctico ---
            fx = 0.0
            fy = 0.0
            self.pitch = 0.0
            self.roll = 0.0
            
            power_factor = 1.0 if self.tier == 3 else (0.75 if self.tier == 2 else 0.50)
            
            if inp.get('forward'):
                fx += dir_x * (5.5 * power_factor)
                fy += dir_y * (5.5 * power_factor)
                self.pitch = -0.12 # Cabeceo natural de crucero táctico
            if inp.get('back'):
                fx -= dir_x * (3.5 * power_factor)
                fy -= dir_y * (3.5 * power_factor)
                self.pitch = 0.10
            if inp.get('left'):
                fx -= right_x * (4.2 * power_factor)
                fy -= right_y * (4.2 * power_factor)
                self.roll = -0.14
            if inp.get('right'):
                fx += right_x * (4.2 * power_factor)
                fy += right_y * (4.2 * power_factor)
                self.roll = 0.14
                
            # --- 3. Colectivo Vertical (Sustentación Espacio / C) ---
            fz = 0.0
            if inp.get('up'):
                fz -= 6.5 * power_factor # Ascender hacia el cielo (Z negativo)
            elif inp.get('down'):
                fz += 5.5                # Descender hacia el suelo
            else:
                # Vuelo estacionario automático
                fz = -self.vz * 1.5
                
            # Inestabilidad en Tier 2 y Tier 1
            if self.tier == 2:
                wobble_x = math.sin(self.time_elapsed * 5.0) * 1.8
                wobble_y = math.cos(self.time_elapsed * 4.5) * 1.8
                fx += wobble_x
                fy += wobble_y
                self.roll += math.sin(self.time_elapsed * 7.0) * 0.05
            elif self.tier == 1:
                wobble_x = math.sin(self.time_elapsed * 8.0) * 3.8
                wobble_y = math.cos(self.time_elapsed * 7.5) * 3.8
                fx += wobble_x
                fy += wobble_y
                fz += 6.5 # Pérdida constante de sustentación
                self.pitch += math.sin(self.time_elapsed * 14.0) * 0.16
                self.roll += math.cos(self.time_elapsed * 13.0) * 0.18
                
            # Integración con resistencia aerodinámica del aire
            self.vx = (self.vx + fx * self.tick_dt) * 0.88
            self.vy = (self.vy + fy * self.tick_dt) * 0.88
            self.vz = (self.vz + fz * self.tick_dt) * 0.86
            
            self.x += self.vx * self.tick_dt
            self.y += self.vy * self.tick_dt
            self.z += self.vz * self.tick_dt

            # Colisión de patines contra la superficie del mapa
            if self.z >= ground_z - 0.2:
                self.z = ground_z - 0.2
                if self.vz > 0.0:
                    self.vz = 0.0
                self.pitch *= 0.8
                self.roll *= 0.8
            
            # Límites de vuelo del mapa
            self.x = max(10.0, min(502.0, self.x))
            self.y = max(10.0, min(502.0, self.y))
            self.z = max(4.0, min(58.0, self.z))
            
            # --- Actualizar Posición del Conductor y de los Compañeros ---
            pilot_pos, p1_pos, p2_pos = self.get_seat_positions()
            try:
                self.pilot.fly = True
                self.pilot.world_object.set_position(*pilot_pos)
                self.pilot.set_location(pilot_pos)
            except Exception:
                pass
                
            if len(self.passengers) > 0 and not self.passengers[0].disconnected and self.passengers[0].hp:
                try:
                    self.passengers[0].fly = True
                    self.passengers[0].world_object.set_position(*p1_pos)
                    self.passengers[0].set_location(p1_pos)
                except Exception:
                    pass
                    
            if len(self.passengers) > 1 and not self.passengers[1].disconnected and self.passengers[1].hp:
                try:
                    self.passengers[1].fly = True
                    self.passengers[1].world_object.set_position(*p2_pos)
                    self.passengers[1].set_location(p2_pos)
                except Exception:
                    pass

        # =========================================================================
        # CASE B: SIN CONDUCTOR EN EL AIRE -> DESCENSO ACELERADO Y EXPLOSIÓN CONTRA EL PISO
        # =========================================================================
        else:
            is_airborne = (self.z < ground_z - 0.8)
            if is_airborne and (self.is_flying or self.uncontrolled_crash):
                self.uncontrolled_crash = True
                # Descenso acelerado por gravedad: la velocidad hacia abajo (vz) aumenta
                self.vz += 14.5 * self.tick_dt
                self.vx *= 0.94
                self.vy *= 0.94
                self.pitch += 0.15 * self.tick_dt # Morro desciende en picada
                self.roll += 0.22 * math.sin(self.time_elapsed * 8.0) * self.tick_dt
                
                self.x += self.vx * self.tick_dt
                self.y += self.vy * self.tick_dt
                self.z += self.vz * self.tick_dt
                
                # Compañeros que queden a bordo mientras cae la aeronave
                pilot_pos, p1_pos, p2_pos = self.get_seat_positions()
                if len(self.passengers) > 0 and not self.passengers[0].disconnected:
                    try:
                        self.passengers[0].world_object.set_position(*p1_pos)
                        self.passengers[0].set_location(p1_pos)
                    except Exception:
                        pass
                if len(self.passengers) > 1 and not self.passengers[1].disconnected:
                    try:
                        self.passengers[1].world_object.set_position(*p2_pos)
                        self.passengers[1].set_location(p2_pos)
                    except Exception:
                        pass
                
                # Impacto con el piso
                if self.z >= ground_z - 0.5:
                    impact_speed = abs(self.vz)
                    print(f"[HELICOPTER CRASH] Impacto con suelo z={ground_z:.1f} a velocidad {impact_speed:.2f} b/s")
                    # Si cae a suficiente velocidad -> ¡Explota contra el piso como 1 granada!
                    if impact_speed >= 3.8 or self.uncontrolled_crash:
                        self.trigger_crash_explosion(self.x, self.y, ground_z)
                        return
                    else:
                        self.z = ground_z - 0.2
                        self.vz = 0.0
                        self.is_flying = False
                        self.uncontrolled_crash = False
            else:
                # Estacionado en tierra / en reposo
                self.is_flying = False
                self.uncontrolled_crash = False
                self.vx *= 0.8
                self.vy *= 0.8
                self.vz *= 0.8
                if self.z > ground_z - 0.2:
                    self.z = ground_z - 0.2
            
        # Broadcast sync every 2 ticks (10 Hz)
        if self.sync_counter % 2 == 0:
            self.broadcast_sync()

    def broadcast_sync(self):
        """Sends [HELI_SYNC] metadata to all connected clients."""
        pilot_id = self.pilot.player_id if (self.pilot and not self.pilot.disconnected and self.pilot.hp) else -1
        p1_id = self.passengers[0].player_id if (len(self.passengers) > 0 and not self.passengers[0].disconnected and self.passengers[0].hp) else -1
        p2_id = self.passengers[1].player_id if (len(self.passengers) > 1 and not self.passengers[1].disconnected and self.passengers[1].hp) else -1
        is_fly = 1 if (self.is_flying or self.pilot or self.uncontrolled_crash) else 0
        sync_msg = f"[HELI_SYNC] 1 {self.x:.2f} {self.y:.2f} {self.z:.2f} {self.yaw:.2f} {self.pitch:.2f} {self.roll:.2f} {pilot_id} {is_fly} {int(self.hp)} {int(self.max_hp)} {self.tier} {p1_id} {p2_id}"
        
        for player in self.protocol.players.values():
            if not player.disconnected:
                try:
                    player.send_chat(sync_msg)
                except Exception:
                    pass

    def embark(self, player):
        """Places player into pilot cockpit or companion lateral edge seats."""
        if self.hp <= 0.0 or not self.active:
            player.send_chat("[HELICOPTER] El vehículo está destruido. Despliega uno nuevo con /spawn helicopter 1.")
            return False
            
        self.passengers = [p for p in self.passengers if not p.disconnected and p.hp]
        
        # Check if already inside
        if player == self.pilot or player in self.passengers:
            player.send_chat("[HELICOPTER] Ya estás a bordo de la aeronave militar.")
            return False
            
        # 1. Asiento del Conductor en la Ventana Frontal
        if not self.pilot or self.pilot.disconnected or not self.pilot.hp:
            self.pilot = player
            self.is_flying = True
            self.uncontrolled_crash = False
            # Spool up into hover upon boarding if on ground
            self.z = max(6.0, self.z - 4.5)
            self.vz = 0.0
            player.heli_inputs = {'forward': False, 'back': False, 'left': False, 'right': False, 'up': False, 'down': False}
            player.is_piloting = True
            player.is_passenger = False
            player.heli_seat = 0
            player.fly = True
            player.god = False
            
            pilot_pos, _, _ = self.get_seat_positions()
            try:
                player.world_object.set_position(*pilot_pos)
                player.set_location(pilot_pos)
            except Exception:
                pass
                
            player.send_chat("[HELICOPTER] ¡Has tomado los mandos como CONDUCTOR en la ventana frontal!")
            player.send_chat(f"[HELICOPTER] Estado del fuselaje: {int(self.hp)}/{int(self.max_hp)} HP [Nivel {self.tier}]")
            player.send_chat("[HELICOPTER] [W/S] Volar | [A/D] Viraje | [Espacio] Ascender | [C] Descender | [E] Salir")
            self.protocol.broadcast_chat(f"[HELICOPTER] {player.name} tomó el asiento del CONDUCTOR en el helicóptero militar.")
            self.broadcast_sync()
            return True
            
        # 2. Asientos de los Compañeros en los Bordes Laterales (Máx 2)
        elif len(self.passengers) < 2:
            self.passengers.append(player)
            seat_num = len(self.passengers) # 1 = Izquierdo, 2 = Derecho
            player.is_piloting = False
            player.is_passenger = True
            player.heli_seat = seat_num
            player.fly = True
            player.god = False
            
            seat_name = "LATERAL IZQUIERDO" if seat_num == 1 else "LATERAL DERECHO"
            pilot_name = self.pilot.name if self.pilot else "Ninguno"
            player.send_chat(f"[HELICOPTER] ¡Has abordado en el asiento de borde: {seat_name}!")
            player.send_chat(f"[HELICOPTER] Conductor al mando: {pilot_name} | Fuselaje: {int(self.hp)}/300 HP")
            player.send_chat("[HELICOPTER] ¡Estás en el borde exterior! Dispara a los enemigos. Si el conductor cae, ¡asumirás los mandos!")
            self.protocol.broadcast_chat(f"[HELICOPTER] {player.name} abordó en el asiento {seat_name} del helicóptero militar.")
            self.broadcast_sync()
            return True
            
        else:
            player.send_chat("[HELICOPTER] El helicóptero militar está completo (1 Conductor + 2 Compañeros en los bordes).")
            return False

    def disembark(self, player):
        """Safely disembarks a passenger or pilot, handling pilot succession or unpiloted freefall."""
        # Disembarking a companion
        if player in self.passengers:
            self.passengers.remove(player)
            player.is_passenger = False
            player.fly = False
            player.heli_seat = -1
            player.send_chat("[HELICOPTER] Has desembarcado del helicóptero.")
            self.protocol.broadcast_chat(f"[HELICOPTER] {player.name} desembarcó del borde del helicóptero.")
            self.broadcast_sync()
            return True
            
        # Disembarking the pilot
        if self.pilot == player:
            self.pilot = None
            player.is_piloting = False
            player.fly = False
            player.heli_inputs = {}
            player.heli_seat = -1
            player.send_chat("[HELICOPTER] Has dejado el asiento del conductor.")
            
            # Sucesión automática: promover al primer compañero a conductor
            self.passengers = [cp for cp in self.passengers if not cp.disconnected and cp.hp]
            if len(self.passengers) > 0:
                new_pilot = self.passengers.pop(0)
                self.pilot = new_pilot
                new_pilot.is_piloting = True
                new_pilot.is_passenger = False
                new_pilot.heli_seat = 0
                new_pilot.heli_inputs = {'forward': False, 'back': False, 'left': False, 'right': False, 'up': False, 'down': False}
                new_pilot.send_chat("[HELICOPTER] ¡El conductor anterior ha saltado!")
                new_pilot.send_chat("[HELICOPTER] ¡Te has movido a la ventana frontal y has TOMADO EL ASIENTO DEL CONDUCTOR!")
                self.protocol.broadcast_chat(f"[HELICOPTER] ¡{new_pilot.name} se movió a la ventana del conductor y tomó los mandos!")
                self.uncontrolled_crash = False
            else:
                # Sin conductores: descenderá con velocidad y explotará al chocar
                if self.is_flying and self.z < 55.0:
                    self.uncontrolled_crash = True
                    self.protocol.broadcast_chat("[HELICOPTER] ¡ALERTA! El helicóptero se ha quedado SIN CONDUCTOR y desciende con velocidad.")
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
        return "Uso: /spawn helicopter [id]"
    entity = args[0].lower()
    if entity in ('helicopter', 'heli', 'chopper', 'elicoptero'):
        if not GLOBAL_HELI:
            GLOBAL_HELI = ActiveHelicopter(connection.protocol)
            
        px, py, pz = connection.world_object.position.get()
        ori = connection.world_object.orientation if hasattr(connection.world_object, 'orientation') else None
        ox = ori.x if ori else 0.0
        oy = ori.y if ori else -1.0
        norm = math.sqrt(ox**2 + oy**2)
        dir_x, dir_y = (ox / norm, oy / norm) if norm > 0.001 else (0.0, -1.0)
        
        spawn_x = px + dir_x * 8.0
        spawn_y = py + dir_y * 8.0
        ground_z = pz
        if hasattr(connection.protocol, 'map'):
            m = connection.protocol.map
            for cz in range(max(1, int(pz) - 4), min(62, int(pz) + 8)):
                if m.get_solid(int(spawn_x), int(spawn_y), cz):
                    ground_z = cz - 0.2
                    break
            
        yaw = math.atan2(ox, -oy) if hasattr(connection.world_object, 'orientation') else 0.0
        GLOBAL_HELI.spawn_at(spawn_x, spawn_y, ground_z, yaw=yaw)
        
        connection.send_chat(f"[HELICOPTER] Helicóptero militar desplegado en ({spawn_x:.1f}, {spawn_y:.1f}, {ground_z:.1f}) con 300 HP.")
        connection.send_chat("[HELICOPTER] ¡Acércate y presiona 'E' para entrar (1 Conductor + 2 Compañeros en bordes)!")
        connection.protocol.broadcast_chat(f"[HELICOPTER] {connection.name} desplegó un helicóptero militar. ¡Presiona 'E' para subirte!")
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
        
    if getattr(connection, 'is_piloting', False) or getattr(connection, 'is_passenger', False):
        GLOBAL_HELI.disembark(connection)
        return
    else:
        # Distance check if parked
        if not GLOBAL_HELI.pilot and len(GLOBAL_HELI.passengers) == 0:
            px, py, pz = connection.world_object.position.get()
            dist = math.sqrt((px - GLOBAL_HELI.x)**2 + (py - GLOBAL_HELI.y)**2 + (pz - GLOBAL_HELI.z)**2)
            if dist > 25.0:
                connection.send_chat(f"[HELICOPTER] Estás demasiado lejos del helicóptero ({dist:.1f} bloques). Acércate para subirte.")
                return
        GLOBAL_HELI.embark(connection)

@command('exit', 'leave')
@player_only
def exit_command(connection):
    """Exit the current vehicle / helicopter."""
    global GLOBAL_HELI
    if GLOBAL_HELI and (getattr(connection, 'is_piloting', False) or getattr(connection, 'is_passenger', False)):
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
        GLOBAL_HELI.hp = max(0.0, min(300.0, val))
        GLOBAL_HELI.update_tier()
        GLOBAL_HELI.broadcast_sync()
        connection.send_chat(f"[HELICOPTER] Salud ajustada a {int(GLOBAL_HELI.hp)}/300 [Nivel {GLOBAL_HELI.tier}]")
        return
    return "No hay helicóptero activo."

def apply_script(protocol, connection, config):
    class HelicopterProtocol(protocol):
        def on_map_change(self, map):
            global GLOBAL_HELI
            GLOBAL_HELI = None
            return super().on_map_change(map)

    class HelicopterConnection(connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.heli_inputs = {'forward': False, 'back': False, 'left': False, 'right': False, 'up': False, 'down': False}
            self.is_piloting = False
            self.is_passenger = False
            self.heli_seat = -1

        def on_disconnect(self):
            global GLOBAL_HELI
            if GLOBAL_HELI:
                if self.is_piloting or self.is_passenger:
                    GLOBAL_HELI.disembark(self)
            super().on_disconnect()

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
            if getattr(self, 'is_piloting', False) or getattr(self, 'is_passenger', False):
                return False
            return super().on_fall(damage)

        def on_hit(self, hit_amount, hit_player, kill_type, grenade):
            """Triggered when this player hits someone."""
            global GLOBAL_HELI
            if (getattr(hit_player, 'is_piloting', False) or getattr(hit_player, 'is_passenger', False)) and GLOBAL_HELI and GLOBAL_HELI.active:
                heli_dmg = hit_amount * 0.75
                GLOBAL_HELI.take_damage(heli_dmg, attacker=self)
            return super().on_hit(hit_amount, hit_player, kill_type, grenade)

        def on_kill(self, killer, kill_type, grenade):
            """
            Triggered when this player is killed.
            Handles pilot succession:
            - If conductor is killed, promotes the next available companion to conductor
              and moves them directly into the front window seat!
            - If no conductors left, helicopter enters freefall descent and will crash explode!
            """
            global GLOBAL_HELI
            
            # --- EL CONDUCTOR HA SIDO ASESINADO ---
            if getattr(self, 'is_piloting', False) and GLOBAL_HELI:
                dead_p = self
                GLOBAL_HELI.pilot = None
                dead_p.is_piloting = False
                dead_p.fly = False
                dead_p.heli_seat = -1
                
                if killer and not killer.disconnected:
                    self.protocol.broadcast_chat(f"[HELICOPTER] ¡{killer.name} eliminó al CONDUCTOR {dead_p.name} de un certero disparo en la ventana!")
                    
                # SUCESIÓN INMEDIATA: Mover compañero a la ubicación del conductor
                GLOBAL_HELI.passengers = [cp for cp in GLOBAL_HELI.passengers if not cp.disconnected and cp.hp]
                if len(GLOBAL_HELI.passengers) > 0:
                    promoted = GLOBAL_HELI.passengers.pop(0)
                    GLOBAL_HELI.pilot = promoted
                    promoted.is_piloting = True
                    promoted.is_passenger = False
                    promoted.heli_seat = 0
                    promoted.heli_inputs = {'forward': False, 'back': False, 'left': False, 'right': False, 'up': False, 'down': False}
                    
                    pilot_pos, _, _ = GLOBAL_HELI.get_seat_positions()
                    try:
                        promoted.world_object.set_position(*pilot_pos)
                        promoted.set_location(pilot_pos)
                    except Exception:
                        pass
                        
                    promoted.send_chat("[HELICOPTER] ¡¡EL CONDUCTOR HA SIDO ABATIDO POR FUEGO ENEMIGO!!")
                    promoted.send_chat("[HELICOPTER] ¡Te has movido a la ventana frontal y has TOMADO EL ASIENTO DEL CONDUCTOR!")
                    self.protocol.broadcast_chat(f"[HELICOPTER] ¡{promoted.name} se movió al asiento del conductor en la ventana y tomó los mandos!")
                    GLOBAL_HELI.uncontrolled_crash = False
                else:
                    # En caso de no tener conductores, el avión descenderá con velocidad y explotará contra el piso
                    GLOBAL_HELI.uncontrolled_crash = True
                    self.protocol.broadcast_chat("[HELICOPTER] ¡El helicóptero se ha quedado SIN CONDUCTOR y desciende a gran velocidad hacia el suelo!")
                    
                GLOBAL_HELI.broadcast_sync()

            # --- UN COMPAÑERO EN EL BORDE HA SIDO ASESINADO ---
            elif getattr(self, 'is_passenger', False) and GLOBAL_HELI:
                if self in GLOBAL_HELI.passengers:
                    GLOBAL_HELI.passengers.remove(self)
                self.is_passenger = False
                self.fly = False
                self.heli_seat = -1
                if killer and not killer.disconnected:
                    self.protocol.broadcast_chat(f"[HELICOPTER] ¡{killer.name} abatió al artillero {self.name} de un disparo en el borde del helicóptero!")
                GLOBAL_HELI.broadcast_sync()
                
            return super().on_kill(killer, kill_type, grenade)

        def on_shoot_set(self, fire):
            """
            Detects gunfire aimed from the ground towards:
            - Conductor in front window (radio 2.2) -> direct damage to conductor!
            - Companion 1 on left edge bench (radio 2.0) -> direct damage to companion!
            - Companion 2 on right edge bench (radio 2.0) -> direct damage to companion!
            - Helicopter fuselage (radio 7.5) -> damage to aircraft!
            """
            global GLOBAL_HELI
            if fire and GLOBAL_HELI and GLOBAL_HELI.active and GLOBAL_HELI.hp > 0.0:
                if not getattr(self, 'is_piloting', False) and not getattr(self, 'is_passenger', False):
                    try:
                        sx, sy, sz = self.world_object.position.get()
                        ori = self.world_object.orientation
                        ox, oy, oz = ori.x, ori.y, ori.z
                        
                        weap = getattr(self, 'weapon', 0)
                        dmg = 45.0 if weap == 0 else (22.0 if weap == 1 else 35.0)
                        
                        pilot_pos, p1_pos, p2_pos = GLOBAL_HELI.get_seat_positions()
                        
                        # 1. Disparo directo al Conductor en la Ventana Frontal
                        if GLOBAL_HELI.pilot and not GLOBAL_HELI.pilot.disconnected and GLOBAL_HELI.pilot.hp:
                            p_vx = pilot_pos[0] - sx; p_vy = pilot_pos[1] - sy; p_vz = pilot_pos[2] - sz
                            p_t = p_vx * ox + p_vy * oy + p_vz * oz
                            if 0.5 < p_t < 130.0:
                                p_cx = sx + p_t * ox; p_cy = sy + p_t * oy; p_cz = sz + p_t * oz
                                if (pilot_pos[0] - p_cx)**2 + (pilot_pos[1] - p_cy)**2 + (pilot_pos[2] - p_cz)**2 <= 2.2**2:
                                    GLOBAL_HELI.pilot.hit(dmg, self, 0)
                                    self.send_chat(f"[COMBATE] ¡Impactaste al CONDUCTOR a través de la ventana frontal! (-{int(dmg)} HP)")
                                    GLOBAL_HELI.take_damage(dmg * 0.4, attacker=self)
                                    return super().on_shoot_set(fire)
                                    
                        # 2. Disparo directo al Compañero 1 en el Borde Izquierdo
                        if len(GLOBAL_HELI.passengers) > 0:
                            cp1 = GLOBAL_HELI.passengers[0]
                            if not cp1.disconnected and cp1.hp:
                                c1_vx = p1_pos[0] - sx; c1_vy = p1_pos[1] - sy; c1_vz = p1_pos[2] - sz
                                c1_t = c1_vx * ox + c1_vy * oy + c1_vz * oz
                                if 0.5 < c1_t < 130.0:
                                    c1_cx = sx + c1_t * ox; c1_cy = sy + c1_t * oy; c1_cz = sz + c1_t * oz
                                    if (p1_pos[0] - c1_cx)**2 + (p1_pos[1] - c1_cy)**2 + (p1_pos[2] - c1_cz)**2 <= 2.0**2:
                                        cp1.hit(dmg, self, 0)
                                        self.send_chat(f"[COMBATE] ¡Impactaste al COMPAÑERO en el borde izquierdo! (-{int(dmg)} HP)")
                                        GLOBAL_HELI.take_damage(dmg * 0.4, attacker=self)
                                        return super().on_shoot_set(fire)

                        # 3. Disparo directo al Compañero 2 en el Borde Derecho
                        if len(GLOBAL_HELI.passengers) > 1:
                            cp2 = GLOBAL_HELI.passengers[1]
                            if not cp2.disconnected and cp2.hp:
                                c2_vx = p2_pos[0] - sx; c2_vy = p2_pos[1] - sy; c2_vz = p2_pos[2] - sz
                                c2_t = c2_vx * ox + c2_vy * oy + c2_vz * oz
                                if 0.5 < c2_t < 130.0:
                                    c2_cx = sx + c2_t * ox; c2_cy = sy + c2_t * oy; c2_cz = sz + c2_t * oz
                                    if (p2_pos[0] - c2_cx)**2 + (p2_pos[1] - c2_cy)**2 + (p2_pos[2] - c2_cz)**2 <= 2.0**2:
                                        cp2.hit(dmg, self, 0)
                                        self.send_chat(f"[COMBATE] ¡Impactaste al COMPAÑERO en el borde derecho! (-{int(dmg)} HP)")
                                        GLOBAL_HELI.take_damage(dmg * 0.4, attacker=self)
                                        return super().on_shoot_set(fire)
                                        
                        # 4. Disparo al Fuselaje General del Helicóptero
                        vx = GLOBAL_HELI.x - sx
                        vy = GLOBAL_HELI.y - sy
                        vz = (GLOBAL_HELI.z - 0.5) - sz
                        t = vx * ox + vy * oy + vz * oz
                        if 0.5 < t < 130.0:
                            cx = sx + t * ox; cy = sy + t * oy; cz = sz + t * oz
                            dist_sq = (GLOBAL_HELI.x - cx)**2 + (GLOBAL_HELI.y - cy)**2 + (GLOBAL_HELI.z - cz)**2
                            if dist_sq <= 7.5**2:
                                GLOBAL_HELI.take_damage(dmg, attacker=self)
                    except Exception:
                        pass
            return super().on_shoot_set(fire)

        def on_chat(self, value, global_message):
            global GLOBAL_HELI
            msg = value.strip().lower()
            
            # Spawn commands
            if msg.startswith(('/spawn helicopter', '/spawn heli', 'spawn helicopter', 'spawn heli', '7spawn helicopter', '7spawn heli')):
                px, py, pz = self.world_object.position.get()
                ori = self.world_object.orientation
                ox, oy = ori.x, ori.y
                norm = math.sqrt(ox**2 + oy**2)
                dir_x, dir_y = (ox / norm, oy / norm) if norm > 0.001 else (0.0, -1.0)
                spawn_x = px + dir_x * 8.0
                spawn_y = py + dir_y * 8.0
                ground_z = pz
                if hasattr(self.protocol, 'map'):
                    m = self.protocol.map
                    for cz in range(max(1, int(pz) - 4), min(62, int(pz) + 8)):
                        if m.get_solid(int(spawn_x), int(spawn_y), cz):
                            ground_z = cz - 0.2
                            break
                            
                if not GLOBAL_HELI:
                    GLOBAL_HELI = ActiveHelicopter(self.protocol)
                yaw = math.atan2(ox, -oy)
                GLOBAL_HELI.spawn_at(spawn_x, spawn_y, ground_z, yaw=yaw)
                self.send_chat(f"[HELICOPTER] Helicóptero militar desplegado en frente tuyo con 300 HP.")
                self.send_chat("[HELICOPTER] ¡Acércate y presiona 'E' para entrar (1 Conductor + 2 Compañeros en bordes)!")
                self.protocol.broadcast_chat(f"[HELICOPTER] {self.name} desplegó un helicóptero militar. ¡Presiona 'E' para subirte!")
                return False

            if msg.startswith(('/heli', 'heli', '7heli', '/pilot', 'pilot')):
                if not GLOBAL_HELI:
                    GLOBAL_HELI = ActiveHelicopter(self.protocol)
                if self.is_piloting or self.is_passenger:
                    GLOBAL_HELI.disembark(self)
                else:
                    px, py, pz = self.world_object.position.get()
                    dist = math.sqrt((px - GLOBAL_HELI.x)**2 + (py - GLOBAL_HELI.y)**2 + (pz - GLOBAL_HELI.z)**2)
                    if dist > 25.0:
                        self.send_chat(f"[HELICOPTER] Estás demasiado lejos del helicóptero ({dist:.1f} bloques). Acércate para subirte.")
                    else:
                        GLOBAL_HELI.embark(self)
                return False

            if msg.startswith(('/exit', 'exit', '7exit', '/leave', 'leave')):
                if GLOBAL_HELI and (self.is_piloting or self.is_passenger):
                    GLOBAL_HELI.disembark(self)
                else:
                    self.send_chat("No estás dentro de ningún vehículo.")
                return False
                
            return super().on_chat(value, global_message)

    return HelicopterProtocol, HelicopterConnection
