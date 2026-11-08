"""
Dynamic Server-Wide Weather Synchronization System for Ace of Spades / Piqueserver.

Features:
- Global synchronized weather states broadcast to all compatible clients.
- Seamless multi-weather support:
    * CLEAR   (Día Claro: cielo azul profundo, alta visibilidad)
    * SUNNY   (Día Soleado: sol radiante, nubes cúmulos blancas)
    * FOG     (Neblina / Niebla: niebla densa y misteriosa, baja visibilidad)
    * RAIN    (Lluvia Tranquila: cielo encapotado, lluvia suave continua)
    * STORM   (Lluvia Fuerte / Tormenta: lluvia torrencial, truenos y relámpagos)
    * SUNSET  (Atardecer: horizonte anaranjado y crepúsculo violeta)
    * NIGHT   (Noche Estrellada: cielo nocturno con estrellas titilantes)
- Backward compatibility with native Ace of Spades clients via FogColor packets.
- Real-time admin/player commands: /weather <tipo>, /clima <tipo>, /weather cycle <on|off>.
- Automatic dynamic weather cycle transitions.
"""

import time
import random
from twisted.internet.task import LoopingCall
from piqueserver.commands import command, player_only
from pyspades.contained import FogColor

def make_fog_int(r, g, b):
    # AoS packet: 32-bit packed color (B in high byte, G, R)
    return (b << 16) | (g << 8) | r

WEATHER_PRESETS = {
    "clear": {
        "name": "clear",
        "display": "Día Claro (Despejado)",
        "fog_int": make_fog_int(140, 191, 242),
        "description": "Cielo azul intenso, brisa fresca y visibilidad panorámica máxima."
    },
    "sunny": {
        "name": "sunny",
        "display": "Día Soleado",
        "fog_int": make_fog_int(153, 199, 250),
        "description": "Sol brillante radiante, cúmulos blancos esponjosos y atmósfera cálida."
    },
    "fog": {
        "name": "fog",
        "display": "Neblina / Niebla Densa",
        "fog_int": make_fog_int(184, 191, 199),
        "description": "Densa capa de niebla plateada envolviendo el terreno. Visibilidad reducida."
    },
    "rain": {
        "name": "rain",
        "display": "Lluvia Tranquila",
        "fog_int": make_fog_int(107, 120, 135),
        "description": "Cielo plomizo cubierto con suave llovizna constante y brisa fresca."
    },
    "storm": {
        "name": "storm",
        "display": "Lluvia Fuerte (Tormenta Eléctrica)",
        "fog_int": make_fog_int(61, 66, 82),
        "description": "¡Aguacero torrencial, viento tempestuoso, relámpagos y truenos ensordecedores!"
    },
    "sunset": {
        "name": "sunset",
        "display": "Atardecer Dorado",
        "fog_int": make_fog_int(217, 107, 56),
        "description": "Horizonte de fuego en tonos ámbar y carmesí con nubes doradas."
    },
    "night": {
        "name": "night",
        "display": "Noche Estrellada",
        "fog_int": make_fog_int(10, 13, 23),
        "description": "Cúpula celeste oscura salpicada de estrellas titilantes y luz de luna."
    }
}

WEATHER_CYCLE_ORDER = ["sunny", "clear", "sunset", "night", "clear", "fog", "rain", "storm", "rain", "clear"]

ALIAS_MAP = {
    "claro": "clear",
    "clear": "clear",
    "despejado": "clear",
    "soleado": "sunny",
    "sunny": "sunny",
    "sol": "sunny",
    "dia": "sunny",
    "niebla": "fog",
    "neblina": "fog",
    "fog": "fog",
    "mist": "fog",
    "lluvia": "rain",
    "rain": "rain",
    "drizzle": "rain",
    "llovizna": "rain",
    "tranquila": "rain",
    "tormenta": "storm",
    "storm": "storm",
    "fuerte": "storm",
    "temporal": "storm",
    "atardecer": "sunset",
    "sunset": "sunset",
    "crepusculo": "sunset",
    "tarde": "sunset",
    "noche": "night",
    "night": "night",
    "oscuro": "night"
}

@command('weather', 'clima')
def set_weather_command(connection, *args):
    """
    Controla o consulta el sistema meteorológico sincronizado del servidor.
    Uso: /weather [clear|sunny|fog|rain|storm|sunset|night|cycle|info]
    """
    protocol = connection.protocol
    if not hasattr(protocol, 'weather_manager'):
        return "El gestor de clima no está activo en este servidor."
        
    manager = protocol.weather_manager
    if not args:
        current = manager.get_current_info()
        return f"[CLIMA] Clima actual: {current['display']}. {current['description']}"
        
    sub = args[0].lower().strip()
    
    if sub in ("info", "status", "actual"):
        current = manager.get_current_info()
        return f"[CLIMA] Clima actual: {current['display']}. Ciclo automático: {'Activado' if manager.auto_cycle else 'Desactivado'}."
        
    if sub in ("cycle", "ciclo"):
        if len(args) > 1:
            val = args[1].lower()
            if val in ("on", "1", "true", "si", "activar"):
                manager.auto_cycle = True
                return "[CLIMA] Ciclo automático de clima ACTIVADO (cambio cada 7 minutos)."
            elif val in ("off", "0", "false", "no", "desactivar"):
                manager.auto_cycle = False
                return "[CLIMA] Ciclo automático de clima DESACTIVADO. El clima actual permanecerá fijo."
        manager.auto_cycle = not manager.auto_cycle
        state_str = "ACTIVADO" if manager.auto_cycle else "DESACTIVADO"
        return f"[CLIMA] Ciclo automático de clima ahora {state_str}."
        
    resolved = ALIAS_MAP.get(sub)
    if not resolved:
        for k, v in ALIAS_MAP.items():
            if sub.startswith(k):
                resolved = v
                break
    if not resolved:
        valid_options = ", ".join(WEATHER_PRESETS.keys())
        return f"[CLIMA] Clima desconocido '{sub}'. Opciones válidas: {valid_options}"
        
    manager.set_weather(resolved, announced_by=connection.name)
    return f"[CLIMA] Has cambiado el clima del servidor a {manager.get_current_info()['display']}."

class ServerWeatherManager:
    def __init__(self, protocol):
        self.protocol = protocol
        self.current_weather = "sunny"
        self.cycle_index = 0
        self.auto_cycle = True
        self.cycle_interval = 420.0 # 7 minutes per weather cycle
        
        # Periodic cycle timer
        self.loop = LoopingCall(self.on_cycle_tick)
        self.loop.start(self.cycle_interval, now=False)
        print(f"[WEATHER] Server Weather Manager initialized with default '{self.current_weather}'")

    def get_current_info(self):
        return WEATHER_PRESETS.get(self.current_weather, WEATHER_PRESETS["clear"])

    def set_weather(self, weather_key, announced_by=None):
        if weather_key not in WEATHER_PRESETS:
            return
            
        self.current_weather = weather_key
        info = WEATHER_PRESETS[weather_key]
        
        # 1. Broadcast standard AoS FogColor packet for backwards compatibility
        fog = FogColor()
        fog.color = info["fog_int"]
        self.protocol.send_contained(fog)
        
        # 2. Broadcast high-level synchronization tag to all compatible clients
        tag = f"[WEATHER] {weather_key.upper()}"
        self.protocol.broadcast_chat(tag)
        
        # 3. Informative announcement in Spanish
        if announced_by:
            announce = f"[CLIMA] {announced_by} ha cambiado las condiciones a: {info['display']}!"
        else:
            announce = f"[CLIMA] El tiempo atmosférico cambia a: {info['display']}!"
            
        self.protocol.broadcast_chat(announce)
        print(f"[WEATHER] Broadcasted new weather: {weather_key.upper()} ({info['display']})")

    def sync_player(self, player):
        """Sends initial weather state to newly connected player."""
        info = self.get_current_info()
        
        # Native fog color packet
        fog = FogColor()
        fog.color = info["fog_int"]
        player.send_contained(fog)
        
        # High-level weather tag
        player.send_chat(f"[WEATHER] {self.current_weather.upper()}")
        player.send_chat(f"[CLIMA] Condición actual del servidor: {info['display']}.")

    def on_cycle_tick(self):
        if not self.auto_cycle:
            return
        self.cycle_index = (self.cycle_index + 1) % len(WEATHER_CYCLE_ORDER)
        next_weather = WEATHER_CYCLE_ORDER[self.cycle_index]
        self.set_weather(next_weather)

def apply_script(protocol, connection, config):
    class WeatherProtocol(protocol):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.weather_manager = ServerWeatherManager(self)

    class WeatherConnection(connection):
        def on_spawn(self, pos):
            # When player spawns, ensure weather is synchronized
            if hasattr(self.protocol, 'weather_manager'):
                self.protocol.weather_manager.sync_player(self)
            return super().on_spawn(pos)

    return WeatherProtocol, WeatherConnection
